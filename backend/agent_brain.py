"""Compatibility shim — re-exports brain_adapter public API under the old names.

main.py still imports from agent_brain; this shim makes that work without
changing main.py's import block.
"""
from brain_adapter import (  # noqa: F401
    ENGINE_NAME,
    ENTRY_PATTERN_NAME,
    MIN_CANDLES,
    brain_chat_summary,
    enrich_signal,
    entry_pattern_profile,
    evaluate_live_entry,
    is_scalp_timeframe,
    strategy_system_blurb,
    run_in_thread,
)
