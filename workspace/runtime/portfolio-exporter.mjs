/**
 * portfolio-exporter.js
 * Prometheus exporter — Binance portfolio metrics
 *
 * Metrics:
 *   portfolio_total_value_usd
 *   portfolio_drawdown_pct
 *   portfolio_allocation_pct{asset}
 *   portfolio_liquidity_pct{type}
 *   portfolio_cash_available_usd
 *
 * Usage:
 *   node portfolio-exporter.mjs
 */

import crypto   from 'crypto'
import http     from 'http'
import https    from 'https'
import fs       from 'fs'
import path     from 'path'
import { URL }  from 'url'

// ─── config ──────────────────────────────────────────────────────────────────

const CONFIG_PATH = '/home/tenazas/.openclaw/tenazas.json'
let BINANCE_API_KEY = process.env.BINANCE_API_KEY || ''
let BINANCE_SECRET  = process.env.BINANCE_SECRET || ''

// Try loading from tenazas.json if env vars not set
if (!BINANCE_API_KEY || !BINANCE_SECRET) {
  try {
    const cfg = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8'))
    BINANCE_API_KEY = cfg.binance_api_key || ''
    BINANCE_SECRET  = cfg.binance_secret || ''
  } catch { /* ignore */ }
}

if (!BINANCE_API_KEY || !BINANCE_SECRET) {
  try {
    const cfg = JSON.parse(fs.readFileSync('/home/tenazas/.openclaw/openclaw.json', 'utf8'))
    BINANCE_API_KEY = cfg.binance_api_key || ''
    BINANCE_SECRET  = cfg.binance_secret || ''
  } catch { /* ignore */ }
}

if (!BINANCE_API_KEY || !BINANCE_SECRET) {
  console.error('ERROR: BINANCE_API_KEY y BINANCE_SECRET no encontrados')
  process.exit(1)
}

const PORT         = parseInt(process.env.PORT || '9101', 10)
const SCRAPE_MS    = parseInt(process.env.SCRAPE_MS || '30000', 10)
const PEAK_FILE    = process.env.PEAK_FILE || '/home/tenazas/.openclaw/state/finance_peak.json'
const BASE_URL     = 'https://api.binance.com'
const PROXY_HOST   = '192.168.2.2'
const PROXY_PORT   = 8888

// ─── peak persistence (drawdown) ─────────────────────────────────────────────

function loadPeak() {
  try {
    const data = JSON.parse(fs.readFileSync(PEAK_FILE, 'utf8'))
    console.log(`[peak] loaded: $${data.portfolio_peak_usd} (${data.updated_at})`)
    return data.portfolio_peak_usd ?? 0
  } catch {
    console.log('[peak] no previous file, starting from 0')
    return 0
  }
}

function savePeak(value) {
  try {
    fs.mkdirSync(path.dirname(PEAK_FILE), { recursive: true })
    fs.writeFileSync(PEAK_FILE, JSON.stringify({
      portfolio_peak_usd: value,
      updated_at: new Date().toISOString(),
    }, null, 2))
  } catch (e) {
    console.error('[peak] save failed:', e.message)
  }
}

let portfolioPeak = loadPeak()

// ─── Binance helpers ─────────────────────────────────────────────────────────

// ─── HTTP via proxy (CONNECT tunnel for HTTPS) ──────────────────────────────

function httpsViaProxy(urlStr, headers = {}, timeout = 15000) {
  return new Promise((resolve, reject) => {
    const target = new URL(urlStr)
    const connectReq = http.request({
      host: PROXY_HOST, port: PROXY_PORT,
      method: 'CONNECT',
      path: `${target.hostname}:443`,
    })
    connectReq.setTimeout(timeout, () => { connectReq.destroy(); reject(new Error('proxy timeout')) })
    connectReq.on('error', reject)
    connectReq.on('connect', (res, socket) => {
      if (res.statusCode !== 200) {
        socket.destroy()
        return reject(new Error(`proxy CONNECT ${res.statusCode}`))
      }
      const req = https.request({
        socket, host: target.hostname,
        path: target.pathname + target.search,
        method: 'GET', headers,
      }, (resp) => {
        const chunks = []
        resp.on('data', c => chunks.push(c))
        resp.on('end', () => {
          const body = Buffer.concat(chunks).toString()
          if (resp.statusCode >= 400) {
            return reject(new Error(`${target.pathname} → ${resp.statusCode}: ${body.slice(0, 200)}`))
          }
          try { resolve(JSON.parse(body)) }
          catch { reject(new Error(`JSON parse error: ${body.slice(0, 100)}`)) }
        })
      })
      req.on('error', reject)
      req.setTimeout(timeout, () => { req.destroy(); reject(new Error('request timeout')) })
      req.end()
    })
    connectReq.end()
  })
}

function sign(params) {
  const qs  = new URLSearchParams({ ...params, timestamp: Date.now() }).toString()
  const sig = crypto.createHmac('sha256', BINANCE_SECRET).update(qs).digest('hex')
  return `${qs}&signature=${sig}`
}

async function bFetch(endpoint, params = {}) {
  const url = `${BASE_URL}${endpoint}?${sign(params)}`
  return httpsViaProxy(url, { 'X-MBX-APIKEY': BINANCE_API_KEY })
}

async function publicFetch(endpoint) {
  return httpsViaProxy(`${BASE_URL}${endpoint}`)
}

// ─── Prometheus (manual, no dependency needed) ───────────────────────────────

const metrics = {
  portfolio_total_value_usd:    { type: 'gauge', help: 'Total portfolio value USD', value: 0 },
  portfolio_drawdown_pct:       { type: 'gauge', help: 'Drawdown from ATH %', value: 0 },
  portfolio_cash_available_usd: { type: 'gauge', help: 'USDT available in spot', value: 0 },
  portfolio_allocation_pct:     { type: 'gauge', help: 'Allocation % per asset', labels: {} },
  portfolio_liquidity_pct:      { type: 'gauge', help: 'Liquidity % per type', labels: {} },
}

function setGauge(name, value, labels = null) {
  if (labels) {
    metrics[name].labels = metrics[name].labels || {}
    const key = Object.entries(labels).map(([k,v]) => `${k}="${v}"`).join(',')
    metrics[name].labels[key] = value
  } else {
    metrics[name].value = value
  }
}

function resetLabels(name) {
  metrics[name].labels = {}
}

function renderMetrics() {
  const lines = []
  for (const [name, m] of Object.entries(metrics)) {
    lines.push(`# HELP ${name} ${m.help}`)
    lines.push(`# TYPE ${name} gauge`)
    if (m.labels && Object.keys(m.labels).length > 0) {
      for (const [lbl, val] of Object.entries(m.labels)) {
        lines.push(`${name}{${lbl}} ${val}`)
      }
    } else if (!m.labels || Object.keys(m.labels).length === 0) {
      if (m.value !== undefined) lines.push(`${name} ${m.value}`)
    }
  }
  return lines.join('\n') + '\n'
}

// ─── collection logic ────────────────────────────────────────────────────────

async function collectMetrics() {
  const t0 = Date.now()
  try {
    // 1. BTC price (needed for wallet/balance conversion)
    const btcTicker = await publicFetch('/api/v3/ticker/price?symbol=BTCUSDT')
    const btcPrice  = parseFloat(btcTicker.price)

    // 2. Wallet balances (values are in BTC, multiply by btcPrice for USD)
    let totalUSD = 0, spotUSD = 0, earnUSD = 0
    try {
      const wallets = await bFetch('/sapi/v1/asset/wallet/balance')
      wallets.forEach(w => {
        const btcVal = parseFloat(w.balance ?? 0)
        const usdVal = btcVal * btcPrice
        totalUSD += usdVal
        const name = (w.walletName ?? '').toLowerCase()
        if (name.includes('earn') || name.includes('savings') || name.includes('staking')) {
          earnUSD += usdVal
        } else if (name === 'spot' || name === 'funding') {
          spotUSD += usdVal
        }
      })
    } catch (e) {
      console.warn('[wallet/balance] failed, using account fallback:', e.message)
      // Fallback: spot account + earn positions
      const account = await bFetch('/api/v3/account')
      for (const b of account.balances) {
        const qty = parseFloat(b.free) + parseFloat(b.locked)
        if (qty < 0.0001) continue
        if (b.asset === 'USDT' || b.asset === 'USDC') {
          totalUSD += qty; spotUSD += qty
        } else {
          try {
            const t = await publicFetch(`/api/v3/ticker/price?symbol=${b.asset}USDT`)
            const v = qty * parseFloat(t.price)
            totalUSD += v; spotUSD += v
          } catch { /* skip */ }
        }
      }
      try {
        const earn = await bFetch('/sapi/v1/simple-earn/flexible/position', { size: 100 })
        for (const pos of (earn.rows || [])) {
          const amt = parseFloat(pos.totalAmount || 0)
          if (amt < 0.0001) continue
          let v = amt
          if (pos.asset !== 'USDT' && pos.asset !== 'USDC') {
            try {
              const t = await publicFetch(`/api/v3/ticker/price?symbol=${pos.asset}USDT`)
              v = amt * parseFloat(t.price)
            } catch { continue }
          }
          totalUSD += v; earnUSD += v
        }
      } catch { /* skip */ }
    }

    // 2. Spot balances for allocation + cash
    const account = await bFetch('/api/v3/account')
    const nonZero = account.balances.filter(b =>
      parseFloat(b.free) + parseFloat(b.locked) > 0.0001
    )

    const assetValues = {}
    await Promise.allSettled(
      nonZero.map(async b => {
        const qty = parseFloat(b.free) + parseFloat(b.locked)
        if (b.asset === 'USDT') {
          assetValues['USDT'] = qty
          return
        }
        try {
          const t = await publicFetch(`/api/v3/ticker/price?symbol=${b.asset}USDT`)
          assetValues[b.asset] = qty * parseFloat(t.price)
        } catch { /* skip */ }
      })
    )

    // Also add earn positions to allocation
    try {
      const earn = await bFetch('/sapi/v1/simple-earn/flexible/position', { size: 100 })
      for (const pos of (earn.rows || [])) {
        const amt = parseFloat(pos.totalAmount || 0)
        if (amt < 0.0001) continue
        const asset = pos.asset
        let val = amt
        if (asset !== 'USDT' && asset !== 'USDC') {
          try {
            const t = await publicFetch(`/api/v3/ticker/price?symbol=${asset}USDT`)
            val = amt * parseFloat(t.price)
          } catch { continue }
        }
        assetValues[asset] = (assetValues[asset] || 0) + val
      }
    } catch { /* skip */ }

    // Cash = USDT free in spot
    const usdtBal = account.balances.find(b => b.asset === 'USDT')
    const cashUSD = usdtBal ? parseFloat(usdtBal.free) : 0

    // 3. Drawdown
    if (totalUSD > portfolioPeak) {
      portfolioPeak = totalUSD
      savePeak(portfolioPeak)
    }
    const drawdownPct = portfolioPeak > 0
      ? ((totalUSD - portfolioPeak) / portfolioPeak) * 100
      : 0

    // 4. Liquidity
    const spotPct = totalUSD > 0 ? (spotUSD / totalUSD) * 100 : 0
    const earnPct = totalUSD > 0 ? (earnUSD / totalUSD) * 100 : 0

    // 5. Set metrics
    setGauge('portfolio_total_value_usd', +totalUSD.toFixed(2))
    setGauge('portfolio_drawdown_pct', +drawdownPct.toFixed(2))
    setGauge('portfolio_cash_available_usd', +cashUSD.toFixed(2))
    setGauge('portfolio_liquidity_pct', +spotPct.toFixed(2), { type: 'spot' })
    setGauge('portfolio_liquidity_pct', +earnPct.toFixed(2), { type: 'earn' })

    resetLabels('portfolio_allocation_pct')
    for (const [asset, valueUSD] of Object.entries(assetValues)) {
      const pct = totalUSD > 0 ? (valueUSD / totalUSD) * 100 : 0
      if (pct >= 0.01) {
        setGauge('portfolio_allocation_pct', +pct.toFixed(2), { asset })
      }
    }

    const ms = Date.now() - t0
    console.log(
      `[${new Date().toISOString()}] OK` +
      ` | total=$${totalUSD.toFixed(2)}` +
      ` | dd=${drawdownPct.toFixed(2)}%` +
      ` | spot=${spotPct.toFixed(0)}% earn=${earnPct.toFixed(0)}%` +
      ` | cash=$${cashUSD.toFixed(2)}` +
      ` | ${ms}ms`
    )
  } catch (err) {
    console.error(`[${new Date().toISOString()}] ERROR:`, err.message)
  }
}

// ─── HTTP server ─────────────────────────────────────────────────────────────

const server = http.createServer(async (req, res) => {
  if (req.url === '/health') {
    res.writeHead(200, { 'Content-Type': 'text/plain' })
    return res.end('ok')
  }
  if (req.url !== '/metrics') {
    res.writeHead(404)
    return res.end('not found')
  }
  res.setHeader('Content-Type', 'text/plain; version=0.0.4')
  res.end(renderMetrics())
})

// ─── start ───────────────────────────────────────────────────────────────────

setInterval(collectMetrics, SCRAPE_MS)
collectMetrics()

server.listen(PORT, () => {
  console.log(`Portfolio exporter on :${PORT}/metrics (every ${SCRAPE_MS/1000}s)`)
})

process.on('SIGTERM', () => { server.close(); process.exit(0) })
process.on('SIGINT',  () => { server.close(); process.exit(0) })
