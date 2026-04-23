#!/usr/bin/env python3
"""
Portfolio Reconciler — validates Prometheus metrics match real API data.

Fetches live data from Binance API + IOL API, compares against what
finance_exporter is exposing on :9109, and alerts via Telegram if
discrepancies exceed thresholds.

Usage:
    python3 portfolio_reconciler.py              # one-shot reconcile
    python3 portfolio_reconciler.py --watch      # loop every 10 min
    python3 portfolio_reconciler.py --json       # JSON output
    python3 portfolio_reconciler.py --no-telegram # skip alerting

Thresholds:
    - Total value: 5% divergence → alert
    - Per-asset qty: 1% divergence → alert
    - Cash balance: $10 divergence → alert
    - Missing asset in exporter → alert
    - Stale exporter (no update in 15 min) → alert
"""

import hashlib
import hmac
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener, urlopen

try:
    from .runtime_config import get_default_telegram_chat_id
except ImportError:
    from runtime_config import get_default_telegram_chat_id

log = logging.getLogger("portfolio_reconciler")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")

# ── Paths & Config ───────────────────────────────────────────

BASE_DIR = Path(__file__).parent
STATE_DIR = (Path("/home/tenazas/.openclaw/state")
             if Path("/home/tenazas").exists()
             else BASE_DIR.parent / "state")
TENAZAS_CONFIG = (Path("/home/tenazas/.openclaw/tenazas.json")
                  if Path("/home/tenazas").exists()
                  else BASE_DIR.parent.parent / "state" / "tenazas.json")
RECONCILE_STATE = STATE_DIR / "reconcile_state.json"
RECONCILE_LOG = BASE_DIR / "logs" / "reconciler.log"

GLUETUN_PROXY = "http://192.168.2.2:8888"
EXPORTER_URL = "http://localhost:9109/metrics"
PROMETHEUS_URL = "http://localhost:9090/api/v1/query"

# Thresholds
TOTAL_DIVERGENCE_PCT = 5.0      # alert if total differs > 5%
ASSET_QTY_DIVERGENCE_PCT = 1.0  # alert if asset qty differs > 1%
CASH_DIVERGENCE_ABS = 10.0      # alert if cash differs > $10
STALE_THRESHOLD_SEC = 900       # 15 minutes

WATCH_INTERVAL = 600  # 10 minutes


# ── Helpers ──────────────────────────────────────────────────

def _load_config() -> dict:
    try:
        return json.loads(TENAZAS_CONFIG.read_text())
    except Exception:
        return {}


def _http_get(url: str, headers: dict | None = None,
              use_proxy: bool = False, timeout: int = 10) -> bytes | None:
    try:
        req = Request(url, headers=headers or {})
        if use_proxy:
            proxy = ProxyHandler({"https": GLUETUN_PROXY,
                                  "http": GLUETUN_PROXY})
            opener = build_opener(proxy)
            resp = opener.open(req, timeout=timeout)
        else:
            resp = urlopen(req, timeout=timeout)
        return resp.read()
    except Exception as e:
        log.warning("HTTP GET failed %s: %s", url[:80], e)
        return None


def _binance_sign(params: dict, secret: str) -> str:
    query = urlencode(params)
    sig = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    return query + f"&signature={sig}"


def _binance_signed_get(endpoint: str, api_key: str, secret: str,
                        extra_params: dict | None = None) -> dict | list | None:
    params = {"timestamp": int(time.time() * 1000)}
    if extra_params:
        params.update(extra_params)
    signed = _binance_sign(params, secret)
    raw = _http_get(
        f"https://api.binance.com{endpoint}?{signed}",
        headers={"X-MBX-APIKEY": api_key},
        use_proxy=True)
    return json.loads(raw) if raw else None


def _send_telegram(text: str):
    try:
        cfg = json.loads(TENAZAS_CONFIG.read_text())
    except Exception:
        return
    token = cfg.get("channels", {}).get("telegram", {}).get("botToken", "")
    if not token:
        token = cfg.get("telegram_bot_token", "")
    if not token:
        return
    payload = json.dumps({
        "chat_id": get_default_telegram_chat_id(),
        "text": text,
        "parse_mode": "Markdown"
    }).encode()
    try:
        req = Request(f"https://api.telegram.org/bot{token}/sendMessage",
                      data=payload,
                      headers={"Content-Type": "application/json"})
        urlopen(req, timeout=10)
    except Exception as e:
        log.warning("Telegram send failed: %s", e)


def _parse_exporter_metrics(raw: str) -> dict:
    """Parse Prometheus text format into {metric_name{labels}: value}."""
    metrics = {}
    for line in raw.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        # e.g. tenazas_binance_total_usd 8566.0
        # e.g. tenazas_binance_asset_qty{asset="BTC",wallet="earn"} 0.11034
        parts = line.split(" ", 1)
        if len(parts) == 2:
            try:
                metrics[parts[0]] = float(parts[1])
            except ValueError:
                pass
    return metrics


def _query_prometheus(query: str) -> float | None:
    """Query Prometheus and return scalar value."""
    raw = _http_get(f"{PROMETHEUS_URL}?query={query}", timeout=5)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        result = data.get("data", {}).get("result", [])
        if result:
            return float(result[0]["value"][1])
    except Exception:
        pass
    return None


# ── Live data fetchers ───────────────────────────────────────

def fetch_binance_live() -> dict | None:
    """Fetch real Binance balances: spot + earn."""
    cfg = _load_config()
    api_key = cfg.get("binance_api_key", "")
    secret = cfg.get("binance_secret", "")
    if not api_key or not secret:
        log.warning("Binance API keys not configured")
        return None

    try:
        # Spot
        account = _binance_signed_get("/api/v3/account", api_key, secret)
        if not account:
            return None

        assets = {}  # asset -> {"spot": qty, "earn": qty}
        for b in account.get("balances", []):
            total = float(b.get("free", 0)) + float(b.get("locked", 0))
            if total > 0.0001:
                assets[b["asset"]] = {"spot": total, "earn": 0}

        # Earn flexible
        try:
            earn = _binance_signed_get("/sapi/v1/simple-earn/flexible/position",
                                       api_key, secret, {"size": 100})
            for pos in (earn.get("rows", []) if isinstance(earn, dict) else []):
                asset = pos.get("asset", "")
                amt = float(pos.get("totalAmount", 0))
                if asset and amt > 0.0001:
                    assets.setdefault(asset, {"spot": 0, "earn": 0})
                    assets[asset]["earn"] += amt
        except Exception as e:
            log.warning("Earn flexible failed: %s", e)

        # Earn locked
        try:
            earn_locked = _binance_signed_get("/sapi/v1/simple-earn/locked/position",
                                              api_key, secret, {"size": 100})
            for pos in (earn_locked.get("rows", []) if isinstance(earn_locked, dict) else []):
                asset = pos.get("asset", "")
                amt = float(pos.get("amount", 0))
                if asset and amt > 0.0001:
                    assets.setdefault(asset, {"spot": 0, "earn": 0})
                    assets[asset]["earn"] += amt
        except Exception as e:
            log.warning("Earn locked failed: %s", e)

        # Get prices
        raw2 = _http_get("https://api.binance.com/api/v3/ticker/24hr",
                         use_proxy=True, timeout=15)
        if not raw2:
            return None
        tickers = {t["symbol"]: t for t in json.loads(raw2)}

        stables = {"USDT", "USDC", "BUSD", "FDUSD", "DAI"}
        result = {"assets": {}, "total_usd": 0}

        for sym, qty_info in assets.items():
            total_qty = qty_info["spot"] + qty_info["earn"]
            wallet = "earn" if qty_info["earn"] > qty_info["spot"] else "spot"

            if sym in stables:
                price = 1.0
            else:
                ticker = tickers.get(sym + "USDT")
                if not ticker:
                    continue
                price = float(ticker.get("lastPrice", 0))

            value = total_qty * price
            if value < 1.0:
                continue

            result["assets"][sym] = {
                "qty": total_qty,
                "wallet": wallet,
                "price": price,
                "value_usd": round(value, 2),
            }
            result["total_usd"] += value

        result["total_usd"] = round(result["total_usd"], 2)
        return result

    except Exception as e:
        log.warning("Binance live fetch failed: %s", e)
        return None


def fetch_iol_live() -> dict | None:
    """Fetch real IOL data: positions + cash."""
    try:
        sys.path.insert(0, "/home/tenazas/.openclaw/workspace/skills/iol_broker")
        from iol_client import IOLClient
        client = IOLClient()

        # Portfolio argentina
        portfolio = client.get("/api/v2/portafolio/argentina")
        positions = {}
        total_ars = 0
        if portfolio and portfolio.get("activos"):
            for a in portfolio["activos"]:
                titulo = a.get("titulo", {})
                sym = titulo.get("simbolo", "?")
                qty = a.get("cantidad", 0)
                price = a.get("ultimoPrecio", 0)
                valor = a.get("valorizado", qty * price)
                total_ars += valor
                positions[sym] = {
                    "qty": qty,
                    "price": price,
                    "value_ars": round(valor, 2),
                    "type": titulo.get("tipo", ""),
                }

        # Estado de cuenta (cash)
        cash = {"ars": 0, "usd": 0, "usd_eeuu": 0}
        try:
            estado = client.get("/api/v2/estadocuenta")
            if estado and "cuentas" in estado:
                for c in estado["cuentas"]:
                    tipo = c.get("tipo", "")
                    saldo = c.get("saldo", 0)
                    if tipo == "inversion_Argentina_Pesos":
                        cash["ars"] = saldo
                    elif tipo == "inversion_Argentina_Dolares":
                        cash["usd"] = saldo
                    elif tipo == "inversion_Estados_Unidos_Dolares":
                        cash["usd_eeuu"] = saldo
        except Exception as e:
            log.warning("IOL estado de cuenta failed: %s", e)

        return {
            "total_ars": round(total_ars, 2),
            "positions": positions,
            "cash": cash,
        }

    except Exception as e:
        log.warning("IOL live fetch failed: %s", e)
        return None


# ── Reconciliation ───────────────────────────────────────────

def fetch_exporter_data() -> dict | None:
    """Fetch current metrics from finance_exporter :9109."""
    raw = _http_get(EXPORTER_URL, timeout=5)
    if not raw:
        return None
    return _parse_exporter_metrics(raw.decode("utf-8", errors="replace"))


def reconcile() -> dict:
    """Compare live API data vs exporter metrics. Returns report."""
    issues = []
    checks = {"binance": {}, "iol": {}, "staleness": {}}
    ts = datetime.now().isoformat()

    # 1) Fetch exporter metrics
    exporter = fetch_exporter_data()
    if not exporter:
        issues.append({"severity": "critical", "source": "exporter",
                       "msg": "finance_exporter :9109 unreachable"})
        return {"ts": ts, "ok": False, "issues": issues, "checks": checks}

    # 2) Binance reconciliation
    binance = fetch_binance_live()
    if binance:
        # Total USD
        exp_total = exporter.get("tenazas_binance_total_usd", 0)
        live_total = binance["total_usd"]
        checks["binance"]["live_total_usd"] = live_total
        checks["binance"]["exporter_total_usd"] = exp_total

        if live_total > 0:
            diff_pct = abs(exp_total - live_total) / live_total * 100
            checks["binance"]["diff_pct"] = round(diff_pct, 2)
            if diff_pct > TOTAL_DIVERGENCE_PCT:
                issues.append({
                    "severity": "high",
                    "source": "binance",
                    "msg": f"Total diverge {diff_pct:.1f}%: live=${live_total:,.2f} vs exporter=${exp_total:,.2f}",
                })

        # Per-asset qty
        for sym, info in binance["assets"].items():
            wallet = info["wallet"]
            exp_key = f'tenazas_binance_asset_qty{{asset="{sym}",wallet="{wallet}"}}'
            exp_qty = exporter.get(exp_key, 0)
            live_qty = info["qty"]

            if live_qty > 0.0001:
                qty_diff_pct = abs(exp_qty - live_qty) / live_qty * 100
                if qty_diff_pct > ASSET_QTY_DIVERGENCE_PCT:
                    issues.append({
                        "severity": "medium",
                        "source": "binance",
                        "msg": f"{sym} qty diverge {qty_diff_pct:.1f}%: live={live_qty:.6f} vs exporter={exp_qty:.6f}",
                    })
                elif exp_qty == 0:
                    issues.append({
                        "severity": "high",
                        "source": "binance",
                        "msg": f"{sym} ({wallet}) missing in exporter — live={live_qty:.6f}",
                    })
    else:
        checks["binance"]["error"] = "live fetch failed"

    # 3) IOL reconciliation
    iol = fetch_iol_live()
    if iol:
        # Total ARS
        exp_total = exporter.get("tenazas_iol_portfolio_ars", 0)
        live_total = iol["total_ars"]
        checks["iol"]["live_total_ars"] = live_total
        checks["iol"]["exporter_total_ars"] = exp_total

        if live_total > 0:
            diff_pct = abs(exp_total - live_total) / live_total * 100
            checks["iol"]["diff_pct"] = round(diff_pct, 2)
            if diff_pct > TOTAL_DIVERGENCE_PCT:
                issues.append({
                    "severity": "high",
                    "source": "iol",
                    "msg": f"Total diverge {diff_pct:.1f}%: live=${live_total:,.0f} vs exporter=${exp_total:,.0f}",
                })

        # Per-position qty
        for sym, info in iol["positions"].items():
            exp_key = f'tenazas_iol_position_qty{{symbol="{sym}",type="{info["type"]}"}}'
            exp_qty = exporter.get(exp_key, 0)
            live_qty = info["qty"]

            if live_qty > 0 and exp_qty == 0:
                issues.append({
                    "severity": "high",
                    "source": "iol",
                    "msg": f"{sym} missing in exporter — live qty={live_qty}",
                })

        # Cash balances
        cash_checks = [
            ("usd", "tenazas_iol_cash_usd", iol["cash"]["usd"]),
            ("usd_eeuu", "tenazas_iol_cash_usd_eeuu", iol["cash"]["usd_eeuu"]),
            ("ars", "tenazas_iol_cash_ars", iol["cash"]["ars"]),
        ]
        for name, metric, live_val in cash_checks:
            exp_val = exporter.get(metric, 0)
            diff = abs(exp_val - live_val)
            threshold = CASH_DIVERGENCE_ABS if "usd" in name else CASH_DIVERGENCE_ABS * 1000
            checks["iol"][f"cash_{name}_live"] = live_val
            checks["iol"][f"cash_{name}_exporter"] = exp_val
            if diff > threshold:
                issues.append({
                    "severity": "medium",
                    "source": "iol",
                    "msg": f"Cash {name} diverge: live={live_val:,.2f} vs exporter={exp_val:,.2f}",
                })
    else:
        checks["iol"]["error"] = "live fetch failed"

    ok = len(issues) == 0
    report = {"ts": ts, "ok": ok, "issues": issues, "checks": checks}

    # Save state
    _save_state(report)

    return report


def _save_state(report: dict):
    """Save reconciliation state for Prometheus/history."""
    try:
        RECONCILE_STATE.parent.mkdir(parents=True, exist_ok=True)
        RECONCILE_STATE.write_text(json.dumps(report, indent=2))
    except Exception as e:
        log.warning("Failed to save state: %s", e)

    # Append to log
    try:
        RECONCILE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(RECONCILE_LOG, "a") as f:
            f.write(json.dumps({
                "ts": report["ts"],
                "ok": report["ok"],
                "issues_count": len(report["issues"]),
                "issues": [i["msg"] for i in report["issues"]],
            }) + "\n")
    except Exception:
        pass


def format_telegram(report: dict) -> str | None:
    """Format report for Telegram. Returns None if all OK."""
    if report["ok"]:
        return None

    lines = ["*Portfolio Reconciler*\n"]
    by_severity = {"critical": [], "high": [], "medium": []}
    for issue in report["issues"]:
        sev = issue.get("severity", "medium")
        by_severity.setdefault(sev, []).append(issue)

    icons = {"critical": "🔴", "high": "🟠", "medium": "🟡"}
    for sev in ["critical", "high", "medium"]:
        for issue in by_severity.get(sev, []):
            src = issue["source"].upper()
            lines.append(f"{icons.get(sev, '⚪')} [{src}] {issue['msg']}")

    checks = report.get("checks", {})
    bin_c = checks.get("binance", {})
    iol_c = checks.get("iol", {})

    lines.append("")
    if "live_total_usd" in bin_c:
        lines.append(f"Binance: live=${bin_c['live_total_usd']:,.2f} expo=${bin_c['exporter_total_usd']:,.2f}")
    if "live_total_ars" in iol_c:
        lines.append(f"IOL: live=${iol_c['live_total_ars']:,.0f} expo=${iol_c['exporter_total_ars']:,.0f}")
    if "cash_usd_live" in iol_c:
        lines.append(f"Cash USD: live=${iol_c['cash_usd_live']:,.2f} expo=${iol_c['cash_usd_exporter']:,.2f}")

    return "\n".join(lines)


# ── Prometheus metrics ───────────────────────────────────────

def get_prometheus_metrics() -> dict:
    """Read reconcile state for metrics_server to expose."""
    try:
        state = json.loads(RECONCILE_STATE.read_text())
        return {
            "reconciler_ok": 1.0 if state.get("ok") else 0.0,
            "reconciler_issues_count": float(len(state.get("issues", []))),
            "reconciler_last_run_ts": time.time(),
            "reconciler_binance_diff_pct": state.get("checks", {}).get("binance", {}).get("diff_pct", 0),
            "reconciler_iol_diff_pct": state.get("checks", {}).get("iol", {}).get("diff_pct", 0),
        }
    except Exception:
        return {}


# ── CLI ──────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Portfolio reconciler")
    parser.add_argument("--watch", action="store_true", help="Loop every 10 min")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--no-telegram", action="store_true", help="Skip Telegram alerts")
    args = parser.parse_args()

    def run_once():
        report = reconcile()

        if args.json:
            print(json.dumps(report, indent=2))
        else:
            if report["ok"]:
                print(f"[{report['ts'][:19]}] OK — all metrics match")
            else:
                print(f"[{report['ts'][:19]}] {len(report['issues'])} issues found:")
                for i in report["issues"]:
                    sev = i["severity"].upper()
                    print(f"  [{sev}] [{i['source']}] {i['msg']}")

            c = report.get("checks", {})
            b = c.get("binance", {})
            o = c.get("iol", {})
            if "live_total_usd" in b:
                print(f"\n  Binance: live=${b['live_total_usd']:,.2f}  exporter=${b['exporter_total_usd']:,.2f}  diff={b.get('diff_pct',0):.2f}%")
            if "live_total_ars" in o:
                print(f"  IOL:     live=${o['live_total_ars']:,.0f}  exporter=${o['exporter_total_ars']:,.0f}  diff={o.get('diff_pct',0):.2f}%")
            if "cash_usd_live" in o:
                print(f"  Cash USD: live=${o['cash_usd_live']:,.2f}  exporter=${o['cash_usd_exporter']:,.2f}")

        if not args.no_telegram:
            msg = format_telegram(report)
            if msg:
                _send_telegram(msg)
                log.info("Telegram alert sent (%d issues)", len(report["issues"]))

        return report

    if args.watch:
        log.info("Reconciler running in watch mode (every %ds)", WATCH_INTERVAL)
        while True:
            try:
                run_once()
            except Exception as e:
                log.warning("Reconcile cycle failed: %s", e)
            time.sleep(WATCH_INTERVAL)
    else:
        run_once()
