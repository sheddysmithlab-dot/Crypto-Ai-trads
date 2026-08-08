"""Importable wrapper for ``1min.py`` (module name cannot start with a digit)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_path = Path(__file__).resolve().with_name("1min.py")
_spec = importlib.util.spec_from_file_location("strategy_1min", _path)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Cannot load 1min strategy from {_path}")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

ENTRY_PATTERN_NAME = _mod.ENTRY_PATTERN_NAME
ENGINE_NAME = _mod.ENGINE_NAME
MAX_OPEN = _mod.MAX_OPEN
BATCH_PROFIT_PCT = _mod.BATCH_PROFIT_PCT
SIZE_FRAC = _mod.SIZE_FRAC
LOOKBACK = _mod.LOOKBACK
entry_pattern_profile = _mod.entry_pattern_profile
is_min1_timeframe = _mod.is_min1_timeframe
is_min1_trade = _mod.is_min1_trade
evaluate_1min = _mod.evaluate_1min
batch_target_usd = _mod.batch_target_usd
detect_fade_signal = _mod.detect_fade_signal
exit_policy_summary = _mod.exit_policy_summary
fire_exit_policy_summary = _mod.fire_exit_policy_summary
batch_status = _mod.batch_status
