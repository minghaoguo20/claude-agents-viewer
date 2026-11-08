"""Cost statistics aggregation across agents. Read-only."""
from __future__ import annotations

from typing import Any

from .sources import AgentInfo
from .transcript import MODEL_FIELDS, TranscriptCache

EMPTY_COST: dict[str, Any] = {"cost_usd": 0.0, "lines_added": 0, "lines_removed": 0,
                              "api_duration_ms": 0, "models": {}}


def add_models(total: dict[str, dict[str, Any]], models: dict[str, dict[str, Any]]) -> None:
    """Accumulate per-model usage into total in place."""
    for name, usage in models.items():
        acc = total.setdefault(name, {k: 0 for k in MODEL_FIELDS})
        for key in MODEL_FIELDS:
            acc[key] += usage.get(key, 0)
        acc["cost_usd"] = float(acc["cost_usd"])


def agent_stats(agent: AgentInfo, cache: TranscriptCache) -> dict[str, Any]:
    """Stats row for one agent; zeros when there is no transcript or cost-state."""
    cost: dict[str, Any] = EMPTY_COST
    count = 0
    if agent.transcript is not None:
        tr = cache.get(agent.transcript)
        count = len(tr.messages)
        cost = tr.cost_state or EMPTY_COST
    return {"session_id": agent.session_id, "name": agent.name, "status": agent.status,
            "messages": count, **cost}


def compute_stats(agents: list[AgentInfo], cache: TranscriptCache) -> dict[str, Any]:
    rows = sorted((agent_stats(a, cache) for a in agents), key=lambda r: -r["cost_usd"])
    models: dict[str, dict[str, Any]] = {}
    for row in rows:
        add_models(models, row["models"])
    return {"agents": rows,
            "totals": {"cost_usd": sum(r["cost_usd"] for r in rows), "models": models}}
