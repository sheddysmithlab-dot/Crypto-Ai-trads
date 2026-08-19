"""Compatibility stub for ml_trading_memory.

The old document-memory ML layer is removed.  The brain.py ML model
(PriceDirectionModel) is now the live ML layer.  These stubs keep
main.py's import block and /agent/ml/* endpoints from crashing.
"""
from __future__ import annotations

_BLURB = (
    "ML bias is now provided by brain.py's PriceDirectionModel (logistic / random-forest). "
    "It annotates every signal with a next-bar directional probability."
)

_SECTIONS: list[dict] = [
    {
        "title": "Brain ML layer",
        "text": _BLURB,
        "tags": ["ml", "model", "brain"],
    }
]


def memory_stats() -> dict:
    return {
        "section_count": len(_SECTIONS),
        "total_chars": sum(len(s["text"]) for s in _SECTIONS),
        "load_ns": 0,
        "arxiv_id": "brain.py:PriceDirectionModel",
        "takeaways": [_BLURB],
    }


def list_ml_toc() -> list[dict]:
    return [{"index": i, "title": s["title"]} for i, s in enumerate(_SECTIONS)]


def fetch_ml(query: str = "", *, max_chars: int = 2000) -> dict:
    text = _BLURB[:max_chars]
    return {"ok": True, "title": "Brain ML layer", "text": text}


def search_ml(query: str, *, max_results: int = 3) -> list[dict]:
    return [{"title": _SECTIONS[0]["title"], "text": _SECTIONS[0]["text"][:500]}]


def ml_system_prompt_blurb() -> str:
    return _BLURB
