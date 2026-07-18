"""In-RAM ML Bitcoin trading paper memory — O(1) / microsecond fetch.

Loads DATA/ml_trading_memory.json once. Companion to candlestick_bible_memory.py.
Paper: arXiv:2606.00060v1 — Machine Learning-Based Bitcoin Trading Under Transaction Costs.
"""
from __future__ import annotations

import json
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

_DATA_DIR = Path(__file__).resolve().parent.parent / "DATA"
_MEMORY_PATH = _DATA_DIR / "ml_trading_memory.json"

_memory: dict[str, Any] | None = None
_load_ns: int = 0


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def ensure_loaded() -> dict[str, Any]:
    global _memory, _load_ns
    if _memory is not None:
        return _memory
    t0 = time.perf_counter_ns()
    if not _MEMORY_PATH.is_file():
        _memory = {
            "source": "missing",
            "sections": {},
            "alias_index": {},
            "toc": [],
            "section_count": 0,
            "total_chars": 0,
            "agent_takeaways": [],
        }
        _load_ns = time.perf_counter_ns() - t0
        return _memory
    with _MEMORY_PATH.open(encoding="utf-8") as f:
        _memory = json.load(f)
    _load_ns = time.perf_counter_ns() - t0
    return _memory


def memory_stats() -> dict[str, Any]:
    m = ensure_loaded()
    return {
        "source": m.get("source"),
        "arxiv_id": m.get("arxiv_id"),
        "section_count": m.get("section_count", len(m.get("sections") or {})),
        "total_chars": m.get("total_chars", 0),
        "aliases": len(m.get("alias_index") or {}),
        "load_ns": _load_ns,
        "path": str(_MEMORY_PATH),
        "takeaways": m.get("agent_takeaways") or [],
    }


def list_ml_toc() -> list[dict[str, Any]]:
    return list(ensure_loaded().get("toc") or [])


def resolve_ml_id(query: str) -> str | None:
    m = ensure_loaded()
    q = _norm(query)
    if not q:
        return None
    alias = m.get("alias_index") or {}
    if q in alias:
        return alias[q]
    hits = [k for k in alias if q in k or k in q]
    if hits:
        hits.sort(key=len, reverse=True)
        return alias[hits[0]]
    for sid, entry in (m.get("sections") or {}).items():
        if q == sid or q in _norm(entry.get("title", "")):
            return sid
    return None


def fetch_ml(query: str, *, max_chars: int | None = None) -> dict[str, Any]:
    t0 = time.perf_counter_ns()
    m = ensure_loaded()
    sid = resolve_ml_id(query)
    elapsed = time.perf_counter_ns() - t0
    if not sid:
        return {
            "ok": False,
            "query": query,
            "error": "section_not_found",
            "fetch_ns": elapsed,
            "hint": "Use list_ml_toc() or search_ml(query).",
        }
    entry = (m.get("sections") or {}).get(sid) or {}
    text = entry.get("text") or ""
    if max_chars is not None and max_chars > 0 and len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0] + "…"
    return {
        "ok": True,
        "query": query,
        "id": sid,
        "title": entry.get("title"),
        "aliases": entry.get("aliases") or [],
        "text": text,
        "chars": len(text),
        "fetch_ns": elapsed,
    }


def search_ml(query: str, *, limit: int = 8) -> list[dict[str, Any]]:
    t0 = time.perf_counter_ns()
    m = ensure_loaded()
    q = _norm(query)
    if not q:
        return []
    tokens = [t for t in q.split() if len(t) > 2] or [q]
    scored: list[tuple[int, dict[str, Any]]] = []
    for sid, entry in (m.get("sections") or {}).items():
        blob = _norm(
            f"{sid} {entry.get('title', '')} {' '.join(entry.get('aliases') or [])} "
            f"{(entry.get('text') or '')[:2500]}"
        )
        score = sum(3 if t in _norm(entry.get("title", "")) else 1 for t in tokens if t in blob)
        if score:
            scored.append(
                (
                    score,
                    {
                        "id": sid,
                        "title": entry.get("title"),
                        "score": score,
                        "summary": entry.get("summary") or "",
                    },
                )
            )
    scored.sort(key=lambda x: (-x[0], x[1]["id"]))
    out = [item for _, item in scored[: max(1, limit)]]
    if out:
        out[0] = {**out[0], "search_ns": time.perf_counter_ns() - t0}
    return out


@lru_cache(maxsize=1)
def ml_system_prompt_blurb(max_chars: int = 2800) -> str:
    m = ensure_loaded()
    lines = [
        "ML BITCOIN TRADING PAPER (in-RAM — fetch by id, do not invent results):",
        f"Source: {m.get('source')}",
        f"sections={m.get('section_count')} · chars={m.get('total_chars')}",
        "Key takeaways:",
    ]
    for t in m.get("agent_takeaways") or []:
        lines.append(f"- {t}")
    lines.append("TOC (fetch_ml / GET /agent/ml/fetch):")
    for row in (m.get("toc") or [])[:35]:
        lines.append(f"- {row.get('id')}: {row.get('title')}")
    if len(m.get("toc") or []) > 35:
        lines.append("…")
    text = "\n".join(lines)
    if len(text) > max_chars:
        return text[:max_chars].rsplit("\n", 1)[0] + "\n…"
    return text


ensure_loaded()
