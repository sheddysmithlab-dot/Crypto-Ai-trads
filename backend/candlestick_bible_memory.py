"""In-RAM Candlestick Trading Bible memory — O(1) / microsecond fetch.

Loads DATA/candlestick_bible_memory.json once at import (or first call).
Use fetch_bible() / search_bible() from the agent path instead of re-reading PDF.
"""
from __future__ import annotations

import json
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

_DATA_DIR = Path(__file__).resolve().parent.parent / "DATA"
_MEMORY_PATH = _DATA_DIR / "candlestick_bible_memory.json"

_memory: dict[str, Any] | None = None
_load_ns: int = 0


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def ensure_loaded() -> dict[str, Any]:
    """Load JSON into process RAM once. Subsequent calls are free."""
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
        "section_count": m.get("section_count", len(m.get("sections") or {})),
        "total_chars": m.get("total_chars", 0),
        "aliases": len(m.get("alias_index") or {}),
        "load_ns": _load_ns,
        "path": str(_MEMORY_PATH),
    }


def list_bible_toc() -> list[dict[str, Any]]:
    return list(ensure_loaded().get("toc") or [])


def resolve_bible_id(query: str) -> str | None:
    """Resolve free-text query → section id via exact alias, then fuzzy contains."""
    m = ensure_loaded()
    q = _norm(query)
    if not q:
        return None
    alias = m.get("alias_index") or {}
    if q in alias:
        return alias[q]
    # substring match on alias keys (longest first)
    hits = [k for k in alias if q in k or k in q]
    if hits:
        hits.sort(key=len, reverse=True)
        return alias[hits[0]]
    # title / id contains
    sections = m.get("sections") or {}
    for sid, entry in sections.items():
        if q == sid or q in _norm(entry.get("title", "")):
            return sid
    return None


def fetch_bible(query: str, *, max_chars: int | None = None) -> dict[str, Any]:
    """Microsecond in-RAM fetch by id or alias. Returns section payload."""
    t0 = time.perf_counter_ns()
    m = ensure_loaded()
    sid = resolve_bible_id(query)
    elapsed = time.perf_counter_ns() - t0
    if not sid:
        return {
            "ok": False,
            "query": query,
            "error": "section_not_found",
            "fetch_ns": elapsed,
            "hint": "Use list_bible_toc() or search_bible(query).",
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
        "pages": entry.get("pages"),
        "aliases": entry.get("aliases") or [],
        "text": text,
        "chars": len(text),
        "fetch_ns": elapsed,
    }


def search_bible(query: str, *, limit: int = 8) -> list[dict[str, Any]]:
    """Keyword search across section titles + bodies (still in-RAM)."""
    t0 = time.perf_counter_ns()
    m = ensure_loaded()
    q = _norm(query)
    if not q:
        return []
    tokens = [t for t in q.split() if len(t) > 2]
    if not tokens:
        tokens = [q]
    scored: list[tuple[int, dict[str, Any]]] = []
    for sid, entry in (m.get("sections") or {}).items():
        blob = _norm(
            f"{sid} {entry.get('title', '')} {' '.join(entry.get('aliases') or [])} "
            f"{(entry.get('text') or '')[:2000]}"
        )
        score = sum(3 if t in _norm(entry.get("title", "")) else 1 for t in tokens if t in blob)
        if score:
            scored.append(
                (
                    score,
                    {
                        "id": sid,
                        "title": entry.get("title"),
                        "pages": entry.get("pages"),
                        "score": score,
                        "summary": entry.get("summary") or "",
                    },
                )
            )
    scored.sort(key=lambda x: (-x[0], x[1]["id"]))
    out = [item for _, item in scored[: max(1, limit)]]
    # attach timing on first result for observability
    if out:
        out[0] = {**out[0], "search_ns": time.perf_counter_ns() - t0}
    return out


@lru_cache(maxsize=1)
def bible_system_prompt_blurb(max_chars: int = 3500) -> str:
    """Compact index for LLM system prompt — not the full book."""
    m = ensure_loaded()
    lines = [
        "CANDLESTICK TRADING BIBLE (in-RAM memory — fetch by id, do not invent rules):",
        f"Source: {m.get('source')} · sections={m.get('section_count')} · chars={m.get('total_chars')}",
        "When you need a pattern/strategy from the book, reason with section ids below.",
        "Backend can fetch full text instantly via fetch_bible(id_or_alias).",
        "TOC:",
    ]
    for row in m.get("toc") or []:
        lines.append(f"- {row.get('id')}: {row.get('title')} (pp. {row.get('pages')})")
    text = "\n".join(lines)
    if len(text) > max_chars:
        return text[:max_chars].rsplit("\n", 1)[0] + "\n…"
    return text


# Warm cache on import so first trade tick does not pay disk I/O.
ensure_loaded()
