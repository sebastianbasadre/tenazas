#!/usr/bin/env python3
"""
Multi-Agent Reasoning — Cooperative analysis using local models.

Workflow:
  1. Qwen (7b) analyzes the problem
  2. Phi (4-mini) critiques/verifies the reasoning
  3. Tenazas synthesizes a final answer

Invoked when:
  - confidence < 0.6
  - problem classified as complex
  - diagnostics ambiguous

Safety: Read-only analysis. Never executes infrastructure actions.
"""

import json
from datetime import datetime
from pathlib import Path

import requests

BASE_DIR = Path(__file__).parent
TELEMETRY_LOG = BASE_DIR / "logs" / "multi_agent.log"

# Model endpoints
OLLAMA_URL = "http://192.168.2.6:11434"
ANALYSIS_MODEL = "qwen3:8b"
VERIFICATION_MODEL = "qwen2.5:3b"
SYNTHESIS_MODEL = "qwen2.5:3b"

# Timeouts
ANALYSIS_TIMEOUT = 90
VERIFICATION_TIMEOUT = 60
SYNTHESIS_TIMEOUT = 60


def _now():
    return datetime.now().astimezone()


def _log(entry: dict):
    TELEMETRY_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = _now().isoformat()
    with open(TELEMETRY_LOG, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _generate(model: str, prompt: str, timeout: int = 60,
              temperature: float = 0.3) -> str | None:
    """Call Ollama generate. Returns response text or None on failure."""
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": temperature, "num_predict": 512},
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json().get("response", "").strip()
    except Exception as e:
        _log({"type": "model_error", "model": model, "error": str(e)})
        return None


def _ollama_available() -> bool:
    """Quick check if Ollama is reachable."""
    try:
        requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
        return True
    except Exception:
        return False


# ── Agents ───────────────────────────────────────────────────────


def _analyze(query: str, context: str) -> str | None:
    """Step 1: Qwen analyzes the problem."""
    prompt = f"""Sos un agente de analisis de infraestructura.
Analiza el siguiente problema y contexto. Proporciona:
1. Descripcion del problema
2. Posibles causas (max 3)
3. Impacto estimado
4. Datos adicionales necesarios

Contexto: {context[:1500]}

Problema: {query}

Analisis:"""

    return _generate(ANALYSIS_MODEL, prompt, ANALYSIS_TIMEOUT, temperature=0.3)


def _verify(query: str, analysis: str) -> str | None:
    """Step 2: Phi verifies/critiques the analysis."""
    prompt = f"""Sos un agente de verificacion.
Revisa el siguiente analisis de un problema de infraestructura.
Indica si el razonamiento es correcto, si falta algo, o si hay errores.
Se breve y directo.

Problema original: {query}

Analisis a verificar:
{analysis[:1000]}

Verificacion:"""

    return _generate(VERIFICATION_MODEL, prompt, VERIFICATION_TIMEOUT, temperature=0.2)


def _synthesize(query: str, analysis: str, verification: str) -> str | None:
    """Step 3: Synthesize final answer from analysis + verification."""
    prompt = f"""Sos Tenazas, asistente de infraestructura.
Sintetiza una respuesta final basada en el analisis y verificacion.
Incluye: diagnostico, causa probable, y acciones sugeridas.
Responde en espanol, breve y directo.

Problema: {query}

Analisis: {analysis[:800]}

Verificacion: {verification[:500]}

Respuesta final:"""

    return _generate(SYNTHESIS_MODEL, prompt, SYNTHESIS_TIMEOUT, temperature=0.3)


# ── Main API ─────────────────────────────────────────────────────


def reason(query: str, context: str = "") -> dict:
    """Run multi-agent cooperative reasoning.

    Args:
        query: the problem or question
        context: additional context (RAG results, diagnostic data, etc.)

    Returns:
        {
            "analysis": str,     # Qwen's analysis
            "verification": str, # Phi's critique
            "conclusion": str,   # Synthesized answer
            "agents_used": list,
            "success": bool,
            "fallback_used": bool,
        }
    """
    result = {
        "analysis": None,
        "verification": None,
        "conclusion": None,
        "agents_used": [],
        "success": False,
        "fallback_used": False,
    }

    if not _ollama_available():
        result["conclusion"] = "Ollama no disponible — no se puede ejecutar razonamiento multi-agente."
        result["fallback_used"] = True
        _log({"type": "reasoning", "status": "ollama_offline"})
        return result

    # Step 1: Analysis (Qwen 7b)
    analysis = _analyze(query, context)
    if analysis:
        result["analysis"] = analysis
        result["agents_used"].append(ANALYSIS_MODEL)
    else:
        # Fallback: skip to single-model response
        result["conclusion"] = "No se pudo completar el analisis multi-agente."
        result["fallback_used"] = True
        _log({"type": "reasoning", "status": "analysis_failed"})
        return result

    # Step 2: Verification (Phi)
    verification = _verify(query, analysis)
    if verification:
        result["verification"] = verification
        result["agents_used"].append(VERIFICATION_MODEL)
    else:
        # Continue without verification
        verification = "Verificacion no disponible."
        result["fallback_used"] = True

    # Step 3: Synthesis
    conclusion = _synthesize(query, analysis, verification)
    if conclusion:
        result["conclusion"] = conclusion
        result["agents_used"].append(SYNTHESIS_MODEL)
        result["success"] = True
    else:
        # Use analysis as conclusion if synthesis fails
        result["conclusion"] = analysis
        result["fallback_used"] = True
        result["success"] = True

    _log({
        "type": "reasoning",
        "status": "complete" if result["success"] else "partial",
        "agents_used": result["agents_used"],
        "fallback_used": result["fallback_used"],
        "query": query[:100],
    })

    return result


def format_reasoning(result: dict) -> str:
    """Format multi-agent reasoning for output."""
    lines = []

    if result.get("analysis"):
        lines.append("Analisis:")
        lines.append(result["analysis"])
        lines.append("")

    if result.get("verification"):
        lines.append("Verificacion:")
        lines.append(result["verification"])
        lines.append("")

    if result.get("conclusion"):
        lines.append("Conclusion:")
        lines.append(result["conclusion"])

    if result.get("fallback_used"):
        lines.append("\n(Razonamiento parcial — algunos agentes no disponibles)")

    return "\n".join(lines) if lines else "Sin resultado."
