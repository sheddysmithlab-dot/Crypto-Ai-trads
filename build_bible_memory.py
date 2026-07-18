"""Build RAM-ready candlestick bible memory from raw PDF extract."""
from __future__ import annotations

import json
import re
from pathlib import Path

RAW = Path(r"c:\Users\PC\Desktop\FINAL_AGENT\DATA\_raw_candlestick_bible.txt")
OUT_JSON = Path(r"c:\Users\PC\Desktop\FINAL_AGENT\DATA\candlestick_bible_memory.json")
OUT_INDEX_MD = Path(r"c:\Users\PC\Desktop\FINAL_AGENT\DATA\CANDLESTICK_BIBLE_INDEX.md")

# TOC page ranges (start inclusive, end inclusive) from book Content pages.
SECTIONS: list[tuple[str, str, int, int, list[str]]] = [
    # (id, title, start_page, end_page, aliases)
    ("introduction", "Introduction", 4, 5, ["intro"]),
    ("overview", "Overview", 6, 7, []),
    ("history", "History of Candlesticks", 8, 10, ["homma", "munehisa"]),
    ("anatomy", "What is a Candlestick", 11, 13, ["candlestick anatomy", "body", "shadow", "wick"]),
    ("patterns_intro", "Candlestick Patterns", 14, 15, []),
    ("engulfing_bar", "The Engulfing Bar Candlestick", 16, 19, ["engulfing", "bullish engulfing", "bearish engulfing"]),
    ("doji", "The Doji Candlestick Pattern", 20, 21, ["doji"]),
    ("dragonfly_doji", "The Dragon Fly Doji Pattern", 22, 24, ["dragonfly", "dragon fly doji"]),
    ("gravestone_doji", "The Gravestone Doji Pattern", 25, 27, ["gravestone"]),
    ("morning_star", "The Morning Star", 28, 30, ["morning star"]),
    ("evening_star", "The Evening Star Candlestick Pattern", 31, 33, ["evening star"]),
    ("hammer", "The Hammer Candlestick Pattern", 34, 36, ["hammer"]),
    ("shooting_star", "The Shooting Star Candlestick Pattern", 37, 39, ["shooting star"]),
    ("harami", "The Harami Pattern", 40, 42, ["harami", "inside"]),
    ("tweezers", "The Tweezers Tops and Bottoms", 43, 46, ["tweezer", "tweezers top", "tweezers bottom"]),
    ("patterns_exercise", "Candlestick Patterns Exercise", 47, 50, ["exercise"]),
    ("market_structure", "The Market Structure", 51, 53, ["structure"]),
    ("trending_markets", "How to Trade Trending Markets", 54, 57, ["trend", "trending"]),
    ("support_resistance", "Support and Resistance Levels", 58, 60, ["support", "resistance", "s/r"]),
    ("trendlines", "How to Draw Trendlines", 61, 62, ["trendline", "trend line"]),
    ("ranging_market", "The Ranging Market", 63, 69, ["range", "ranging", "sideways"]),
    ("timeframes_topdown", "Time Frames and Top Down Analysis", 70, 78, ["timeframe", "top down", "mtf"]),
    ("strategies_intro", "Trading Strategies and Tactics", 79, 80, ["strategies"]),
    ("pin_bar_strategy", "The Pin Bar Candlestick Pattern Strategies", 81, 87, ["pin bar", "pinbar"]),
    ("pin_bar_with_trend", "Trading the Pin Bar Candle With The Trend", 88, 91, []),
    ("trading_tactics", "Trading Tactics", 92, 95, ["tactics"]),
    ("pin_bar_confluence", "Trading Pin Bars with Confluence", 96, 99, ["confluence"]),
    ("pin_bar_examples", "Pin Bar Trades Examples", 100, 102, []),
    ("pin_bar_range", "Trading Pin Bars in Range Bounds Markets", 103, 108, []),
    ("engulfing_strategy", "The Engulfing Bar Candlestick Pattern", 109, 111, []),
    ("engulfing_how_to_trade", "How to Trade the Engulfing Bar Price Action Signal", 112, 116, []),
    ("engulfing_ma", "Trading the Engulfing Bar with Moving Averages", 117, 119, ["moving average", "ma"]),
    ("engulfing_fib", "How to Trade the Engulfing Bar with Fibonacci Retracements", 120, 121, ["fibonacci", "fib"]),
    ("engulfing_trendlines", "Trading the Engulfing Bar with Trendlines", 122, 124, []),
    ("engulfing_sideways", "Trading the Engulfing Bar in Sideways Markets", 125, 129, []),
    ("engulfing_supply_demand", "The Engulfing Pattern with Supply and Demand Zones", 130, 132, ["supply", "demand"]),
    ("money_management_rules", "Money Management Trading Rules", 133, 136, ["risk", "money management"]),
    ("inside_bar", "The Inside Bar Candlestick Pattern", 137, 139, ["inside bar"]),
    ("inside_bar_psychology", "The Psychology Behind the Inside Bar Pattern Formation", 140, 142, []),
    ("inside_bar_sr", "How to Trade Inside Bars with Support and Resistance", 143, 145, []),
    ("inside_bar_tips", "Tips on Trading the Inside Bar Price Action Setup", 146, 147, []),
    ("inside_bar_false_breakout", "Trading the False Breakout of The Inside Bar Pattern", 148, 150, ["false breakout", "fakey"]),
    ("inside_bar_fb_examples", "Inside bar false breakouts trading examples", 151, 153, []),
    ("inside_bar_fb_fib", "Trading Inside Bar False Breakout with Fibonacci Retracements", 154, 157, []),
    ("trades_examples", "Trades Examples", 158, 161, ["examples"]),
    ("money_management_strategies", "Money Management Strategies", 162, 166, []),
    ("conclusion", "Conclusion", 167, 168, []),
]


def parse_pages(raw: str) -> dict[int, str]:
    pages: dict[int, str] = {}
    chunks = re.split(r"\n===== PAGE (\d+) =====\n", raw)
    # chunks: [preamble, num, text, num, text, ...]
    i = 1
    while i + 1 < len(chunks):
        num = int(chunks[i])
        text = chunks[i + 1]
        # strip repeating header / page numbers
        lines = []
        for line in text.splitlines():
            s = line.strip()
            if not s:
                continue
            if s.upper() == "THE CANDLESTICK TRADING BIBLE":
                continue
            if s.isdigit() and len(s) <= 3:
                continue
            lines.append(s)
        pages[num] = "\n".join(lines)
        i += 2
    return pages


def normalize_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def main() -> None:
    pages = parse_pages(RAW.read_text(encoding="utf-8"))
    entries = []
    alias_index: dict[str, str] = {}

    for sid, title, start, end, aliases in SECTIONS:
        body_parts = []
        for p in range(start, end + 1):
            if p in pages and pages[p].strip():
                body_parts.append(pages[p])
        body = "\n\n".join(body_parts).strip()
        # compact whitespace a bit
        body = re.sub(r"\n{3,}", "\n\n", body)

        entry = {
            "id": sid,
            "title": title,
            "pages": [start, end],
            "aliases": aliases,
            "text": body,
            "chars": len(body),
            "summary": body[:420].rsplit(" ", 1)[0] + "…" if len(body) > 420 else body,
        }
        entries.append(entry)

        for key in [sid, title, *aliases]:
            alias_index[normalize_key(key)] = sid

    memory = {
        "source": "The Candlestick Trading Bible (KohanFx.com)",
        "version": 1,
        "page_count": max(pages) if pages else 0,
        "section_count": len(entries),
        "total_chars": sum(e["chars"] for e in entries),
        "sections": {e["id"]: e for e in entries},
        "alias_index": alias_index,
        "toc": [{"id": e["id"], "title": e["title"], "pages": e["pages"]} for e in entries],
    }

    OUT_JSON.write_text(json.dumps(memory, ensure_ascii=False, indent=2), encoding="utf-8")

    md_lines = [
        "# Candlestick Trading Bible — Fast Memory Index",
        "",
        "Source: The Candlestick Trading Bible (KohanFx.com).",
        "",
        "Full book text is loaded into **RAM** at backend startup via",
        "`candlestick_bible_memory.py` (dict lookup = microsecond fetch).",
        "Do NOT paste the full book into every chat turn — call fetch by id/alias.",
        "",
        "## How to fetch (agent / code)",
        "- `fetch_bible('hammer')` → hammer section",
        "- `fetch_bible('pin bar')` → pin bar strategies",
        "- `fetch_bible('engulfing_fib')` → engulfing + Fibonacci",
        "- `list_bible_toc()` → all section ids",
        "- `search_bible('false breakout')` → matching sections",
        "",
        "## TOC (section ids)",
        "",
    ]
    for e in entries:
        md_lines.append(
            f"- `{e['id']}` — {e['title']} (pp. {e['pages'][0]}–{e['pages'][1]}, {e['chars']} chars)"
        )
    md_lines.append("")
    md_lines.append(
        f"**Loaded:** {len(entries)} sections · {memory['total_chars']} chars · "
        f"{memory['page_count']} pages"
    )
    OUT_INDEX_MD.write_text("\n".join(md_lines), encoding="utf-8")

    print(
        f"OK sections={len(entries)} chars={memory['total_chars']} "
        f"json={OUT_JSON.stat().st_size} index={OUT_INDEX_MD}"
    )


if __name__ == "__main__":
    main()
