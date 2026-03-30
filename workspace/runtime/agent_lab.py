#!/usr/bin/env python3
"""
Agent Lab — Distributed multi-agent reasoning for Tenazas runtime.

Runs multiple reasoning agents in SEQUENCE (NAS has limited compute).
Agents: analysis_agent, verification_agent, planner_agent, critic_agent, research_agent.
Each agent is a prompt template sent to Ollama. Coordinates multi-agent sessions.

Logs to logs/agent_lab.log.
"""

import json
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).parent
LOG_FILE = BASE_DIR / "logs" / "agent_lab.log"

OLLAMA_URL = "http://192.168.2.76:11434"
GENERATE_ENDPOINT = f"{OLLAMA_URL}/api/generate"
DEFAULT_MODEL = "qwen2.5:7b"
FAST_MODEL = "qwen2.5:3b"
REQUEST_TIMEOUT = 60

# ── Agent Definitions ────────────────────────────────────────────

AGENTS = {
    "analysis_agent": {
        "description": "Analyzes a problem or situation in depth",
        "model": DEFAULT_MODEL,
        "system_prompt": (
            "You are an infrastructure analysis agent for a home NAS system. "
            "Analyze the given situation thoroughly. Focus on root causes, "
            "dependencies, and potential cascading effects. Be concise."
        ),
        "prompt_template": "Analyze this situation:\n\n{input}\n\nContext: {context}",
    },
    "verification_agent": {
        "description": "Verifies claims and checks for inconsistencies",
        "model": FAST_MODEL,
        "system_prompt": (
            "You are a verification agent. Check the given analysis for "
            "logical errors, unsupported claims, or missing considerations. "
            "Be critical but constructive. Be concise."
        ),
        "prompt_template": "Verify this analysis:\n\n{input}\n\nOriginal problem: {context}",
    },
    "planner_agent": {
        "description": "Creates actionable plans from analysis",
        "model": DEFAULT_MODEL,
        "system_prompt": (
            "You are a planning agent for infrastructure management. "
            "Given an analysis, create a concrete action plan. Each step must "
            "be safe and reversible. Never suggest destructive actions. Be concise."
        ),
        "prompt_template": "Create an action plan based on:\n\n{input}\n\nConstraints: {context}",
    },
    "critic_agent": {
        "description": "Reviews plans for safety and feasibility",
        "model": FAST_MODEL,
        "system_prompt": (
            "You are a safety critic for infrastructure actions. "
            "Review the proposed plan for risks, safety issues, and feasibility. "
            "Flag anything that could cause downtime or data loss. Be concise."
        ),
        "prompt_template": "Review this plan for safety:\n\n{input}\n\nContext: {context}",
    },
    "research_agent": {
        "description": "Gathers relevant context and background information",
        "model": FAST_MODEL,
        "system_prompt": (
            "You are a research agent. Given a topic, provide relevant technical "
            "context, common solutions, and best practices. Focus on Docker, "
            "NAS infrastructure, VPNs, and media server stacks. Be concise."
        ),
        "prompt_template": "Research this topic:\n\n{input}\n\nFocus areas: {context}",
    },
}

# ── Standard pipelines (agent sequences) ─────────────────────────

PIPELINES = {
    "full_analysis": ["research_agent", "analysis_agent", "verification_agent",
                      "planner_agent", "critic_agent"],
    "quick_analysis": ["analysis_agent", "planner_agent"],
    "safety_review": ["analysis_agent", "critic_agent"],
    "deep_research": ["research_agent", "analysis_agent", "verification_agent"],
}


def _now():
    return datetime.now().astimezone()


def _log(entry: dict):
    """Append a JSON line to agent_lab.log."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _call_ollama(model: str, system_prompt: str, prompt: str) -> dict:
    """
    Call Ollama generate API. Returns {ok, response, error}.
    Does NOT stream — waits for full response.
    """
    payload = json.dumps({
        "model": model,
        "system": system_prompt,
        "prompt": prompt,
        "stream": False,
    }).encode()

    try:
        req = Request(
            GENERATE_ENDPOINT,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
            return {"ok": True, "response": data.get("response", ""), "error": None}
    except (URLError, json.JSONDecodeError, OSError, TimeoutError) as e:
        return {"ok": False, "response": "", "error": str(e)}


def run_agent(agent_name: str, input_text: str, context: str = "") -> dict:
    """
    Run a single agent with the given input.

    Args:
        agent_name: One of the defined agents
        input_text: The main input/query for the agent
        context: Additional context string

    Returns:
        dict with: agent, ok, response, error, duration_ms
    """
    agent = AGENTS.get(agent_name)
    if not agent:
        return {"agent": agent_name, "ok": False, "response": "",
                "error": f"Unknown agent: {agent_name}"}

    prompt = agent["prompt_template"].format(input=input_text, context=context)
    start = _now()
    result = _call_ollama(agent["model"], agent["system_prompt"], prompt)
    duration_ms = int((_now() - start).total_seconds() * 1000)

    entry = {
        "agent": agent_name,
        "ok": result["ok"],
        "response": result["response"][:200] if result["response"] else "",
        "error": result.get("error"),
        "duration_ms": duration_ms,
    }

    _log({"ts": _now().isoformat(), "action": "run_agent", **entry})

    return {
        "agent": agent_name,
        "ok": result["ok"],
        "response": result["response"],
        "error": result.get("error"),
        "duration_ms": duration_ms,
    }


def run_pipeline(pipeline_name: str, input_text: str, context: str = "") -> dict:
    """
    Run a named pipeline — a sequence of agents where each agent's output
    feeds into the next agent's input.

    Args:
        pipeline_name: One of the defined pipelines (full_analysis, quick_analysis, etc.)
        input_text: The initial problem/query
        context: Additional context for all agents

    Returns:
        dict with: pipeline, agents_run, results[], final_output, ok
    """
    agent_sequence = PIPELINES.get(pipeline_name)
    if not agent_sequence:
        return {"pipeline": pipeline_name, "ok": False,
                "error": f"Unknown pipeline: {pipeline_name}", "results": []}

    results = []
    current_input = input_text
    all_ok = True

    for agent_name in agent_sequence:
        result = run_agent(agent_name, current_input, context)
        results.append(result)

        if result["ok"] and result["response"]:
            current_input = result["response"]
        else:
            all_ok = False
            # Continue with original input if an agent fails
            _log({"ts": _now().isoformat(), "action": "pipeline_agent_failed",
                  "pipeline": pipeline_name, "agent": agent_name,
                  "error": result.get("error")})

    final_output = results[-1]["response"] if results and results[-1]["ok"] else ""

    _log({
        "ts": _now().isoformat(),
        "action": "run_pipeline",
        "pipeline": pipeline_name,
        "agents_run": len(results),
        "all_ok": all_ok,
    })

    return {
        "pipeline": pipeline_name,
        "agents_run": len(results),
        "results": results,
        "final_output": final_output,
        "ok": all_ok,
    }


def run_custom_sequence(agent_names: list, input_text: str, context: str = "") -> dict:
    """
    Run a custom sequence of agents.

    Args:
        agent_names: List of agent names to run in order
        input_text: The initial input
        context: Additional context

    Returns:
        Same format as run_pipeline
    """
    valid_agents = [a for a in agent_names if a in AGENTS]
    if not valid_agents:
        return {"pipeline": "custom", "ok": False,
                "error": "No valid agents in sequence", "results": []}

    results = []
    current_input = input_text

    for agent_name in valid_agents:
        result = run_agent(agent_name, current_input, context)
        results.append(result)
        if result["ok"] and result["response"]:
            current_input = result["response"]

    return {
        "pipeline": "custom",
        "agents_run": len(results),
        "results": results,
        "final_output": results[-1]["response"] if results and results[-1]["ok"] else "",
        "ok": all(r["ok"] for r in results),
    }


def list_agents() -> dict:
    """Return all available agents with descriptions."""
    return {name: agent["description"] for name, agent in AGENTS.items()}


def list_pipelines() -> dict:
    """Return all available pipelines with their agent sequences."""
    return {name: agents for name, agents in PIPELINES.items()}
