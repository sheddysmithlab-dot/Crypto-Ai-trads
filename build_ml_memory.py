"""Build RAM-ready memory pack from arXiv ML Bitcoin trading paper HTML extract."""
from __future__ import annotations

import json
import re
from pathlib import Path

SRC = Path(
    r"C:\Users\PC\.cursor\projects\c-Users-PC-Desktop-FINAL-AGENT\agent-tools\815e3eda-7578-40fd-b581-71ae88a048c2.txt"
)
OUT_JSON = Path(r"c:\Users\PC\Desktop\FINAL_AGENT\DATA\ml_trading_memory.json")
OUT_INDEX = Path(r"c:\Users\PC\Desktop\FINAL_AGENT\DATA\ML_TRADING_PAPER_INDEX.md")
OUT_RAW = Path(r"c:\Users\PC\Desktop\FINAL_AGENT\DATA\_raw_machine_learning.txt")

# Extra aliases keyed by section id after auto-split.
EXTRA_ALIASES: dict[str, list[str]] = {
    "abstract": ["abstract", "summary", "paper abstract"],
    "1_introduction": ["introduction", "intro", "emh"],
    "2_related_literature": ["literature", "related work"],
    "2_1_return_predictability_and_financial_machine_learning": [
        "xgboost intro",
        "lstm intro",
        "itransformer",
        "predictability",
    ],
    "2_2_transaction_costs_and_the_prediction_to_trading_gap": [
        "transaction costs",
        "prediction to trading",
        "turnover",
    ],
    "2_3_walk_forward_evaluation_and_statistical_inference": [
        "walk forward",
        "wfo",
        "bootstrap",
    ],
    "3_data_and_empirical_design": ["data", "sample", "btc usdt data"],
    "3_1_data_source_and_sample": ["binance", "ohlcv", "data source"],
    "3_2_preprocessing_and_target_variable": ["log return", "target", "preprocessing"],
    "3_4_walk_forward_empirical_design": ["27 fold", "walk-forward design"],
    "4_1_trading_strategy_and_cost_aware_execution": [
        "cost aware",
        "cost-aware",
        "execution filter",
        "lambda",
        "trading rule",
        "sign based",
        "long only",
        "long short",
    ],
    "4_2_walk_forward_optimisation": ["walk-forward optimisation", "optimisation"],
    "4_3_feature_engineering": [
        "features",
        "technical indicators",
        "egarch",
        "feature engineering",
    ],
    "4_4_forecasting_models": ["models", "xgboost", "lstm", "architecture"],
    "4_5_hyperparameter_optimisation": ["hyperparameters", "tuning"],
    "4_6_performance_metrics": ["sharpe", "metrics", "drawdown", "performance metrics"],
    "4_7_model_selection_criteria": ["model selection", "selector"],
    "5_1_h1_transaction_costs_and_naive_machine_learning_trading": [
        "h1",
        "naive strategy",
        "ten basis points",
        "10 bp",
    ],
    "5_2_h2_cost_aware_execution": ["h2", "cost aware results"],
    "5_3_h3_feature_enrichment": ["h3", "feature enrichment results"],
    "5_4_h4_model_architecture_comparison": ["h4", "architecture comparison"],
    "5_5_h5_loss_function_comparison": ["h5", "mse", "mae", "loss function"],
    "5_6_h6_model_selection_criterion": ["h6"],
    "6_1_cost_aware_threshold_sensitivity": ["lambda sensitivity", "threshold"],
    "6_2_transaction_cost_sensitivity": ["cost sensitivity"],
    "7_conclusion": ["conclusion", "takeaways", "findings"],
}


def slugify(title: str) -> str:
    s = title.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")[:80] or "section"


def normalize_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def split_sections(text: str) -> list[tuple[str, str]]:
    """Split markdown-ish extract on ## / ### headings. Keep abstract separately."""
    sections: list[tuple[str, str]] = []

    # Abstract block
    abs_m = re.search(
        r"###### Abstract\s*(.*?)(?=\n###### keywords:|\n## 1 Introduction)",
        text,
        flags=re.S | re.I,
    )
    if abs_m:
        body = abs_m.group(1).strip()
        kw_m = re.search(r"###### keywords:\s*(.*?)(?=\n†|\n## )", text, flags=re.S | re.I)
        if kw_m:
            body = body + "\n\nKeywords: " + kw_m.group(1).strip()
        sections.append(("Abstract", body))

    # Heading splits
    parts = re.split(r"\n(?=##+\s)", text)
    for part in parts:
        part = part.strip()
        if not part.startswith("#"):
            continue
        lines = part.splitlines()
        heading = lines[0].lstrip("#").strip()
        if heading.lower().startswith("abstract") or heading.lower().startswith("keywords"):
            continue
        # skip junk arxiv chrome
        if "arxiv is now" in heading.lower():
            continue
        body = "\n".join(lines[1:]).strip()
        if len(body) < 40 and not heading.startswith(("1 ", "2 ", "3 ", "4 ", "5 ", "6 ", "7 ")):
            # keep short appendix headers if they have some body
            if len(body) < 20:
                continue
        sections.append((heading, body))
    return sections


def main() -> None:
    raw = SRC.read_text(encoding="utf-8")
    # Persist clean source for rebuilds (not gitignored name with _raw for ML - update gitignore)
    OUT_RAW.write_text(raw, encoding="utf-8")

    pairs = split_sections(raw)
    entries = []
    alias_index: dict[str, str] = {}
    used_ids: set[str] = set()

    for title, body in pairs:
        body = re.sub(r"\n{3,}", "\n\n", body).strip()
        # drop remaining arxiv UI noise lines
        cleaned_lines = []
        for line in body.splitlines():
            if "arxiv is now an independent" in line.lower():
                continue
            cleaned_lines.append(line)
        body = "\n".join(cleaned_lines).strip()
        if not body:
            continue

        sid = slugify(title)
        if sid in used_ids:
            sid = f"{sid}_{len(used_ids)}"
        used_ids.add(sid)

        aliases = EXTRA_ALIASES.get(sid, [])
        # also alias bare numbers like "4.1"
        m = re.match(r"^(\d+(?:\.\d+)*)\s+", title)
        if m:
            aliases = [*aliases, m.group(1), title]

        entry = {
            "id": sid,
            "title": title,
            "pages": None,
            "aliases": aliases,
            "text": body,
            "chars": len(body),
            "summary": (body[:420].rsplit(" ", 1)[0] + "…") if len(body) > 420 else body,
        }
        entries.append(entry)
        for key in [sid, title, *aliases]:
            alias_index[normalize_key(key)] = sid

    memory = {
        "source": (
            "Machine Learning-Based Bitcoin Trading Under Transaction Costs: "
            "Evidence From Walk-Forward Forecasting (arXiv:2606.00060v1)"
        ),
        "arxiv_id": "2606.00060v1",
        "authors": "Andrei Bysik, Robert Ślepaczuk",
        "version": 1,
        "section_count": len(entries),
        "total_chars": sum(e["chars"] for e in entries),
        "sections": {e["id"]: e for e in entries},
        "alias_index": alias_index,
        "toc": [{"id": e["id"], "title": e["title"], "chars": e["chars"]} for e in entries],
        "agent_takeaways": [
            "Naive sign-based ML trades fail at ~10 bp transaction costs due to turnover.",
            "Cost-aware filter: trade only when |forecast| exceeds λ × transaction-cost threshold.",
            "Strongest reported setup: long-only XGBoost with cost-aware execution (>65% ann., Sharpe>1) — regime-dependent.",
            "Walk-forward (27-fold) evaluation required; avoid random CV leakage.",
            "Execution discipline often matters more than model architecture / feature enrichment.",
        ],
    }

    OUT_JSON.write_text(json.dumps(memory, ensure_ascii=False, indent=2), encoding="utf-8")

    md = [
        "# ML Bitcoin Trading Paper — Fast Memory Index",
        "",
        f"Source: {memory['source']}",
        f"Authors: {memory['authors']}",
        "",
        "Full paper text is in RAM via `ml_trading_memory.py` (microsecond fetch).",
        "Do NOT paste the full paper into every LLM turn — fetch by id/alias.",
        "",
        "## Agent takeaways (always relevant)",
        "",
    ]
    for t in memory["agent_takeaways"]:
        md.append(f"- {t}")
    md += [
        "",
        "## How to fetch",
        "- `fetch_ml('cost aware')` → cost-aware execution filter",
        "- `fetch_ml('xgboost')` / `fetch_ml('h2')` / `fetch_ml('walk forward')`",
        "- `search_ml('transaction costs')`",
        "- API: `GET /agent/ml/fetch?q=cost-aware`",
        "",
        "## TOC",
        "",
    ]
    for e in entries:
        md.append(f"- `{e['id']}` — {e['title']} ({e['chars']} chars)")
    md.append("")
    md.append(f"**Loaded:** {len(entries)} sections · {memory['total_chars']} chars")
    OUT_INDEX.write_text("\n".join(md), encoding="utf-8")
    print(f"OK sections={len(entries)} chars={memory['total_chars']} -> {OUT_JSON}")


if __name__ == "__main__":
    main()
