#!/usr/bin/env python3
"""OKX new-listing announcement monitor with optional Claude analysis and TG push."""

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

from okx_client import extract_okx_symbol, get_new_listing_announcements, to_float

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "data" / "okx_listings.db"
ENV_FILES = [BASE_DIR / ".env.okx", BASE_DIR / ".env.oi"]

OKX_POLL_INTERVAL = int(os.getenv("OKX_ANNOUNCEMENT_POLL_INTERVAL", "60"))
ANTHROPIC_BASE_URL = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")

TRIGGER_KEYWORDS = ["will launch", "will list", "spot trading", "perpetual", "swap"]
EXCLUDE_KEYWORDS = ["delist", "migration", "maintenance", "support", "suspend", "convert"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("okx-listing")


def load_env_files():
    for path in ENV_FILES:
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


load_env_files()
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS listings (
        id TEXT PRIMARY KEY,
        symbol TEXT NOT NULL,
        title TEXT NOT NULL,
        url TEXT,
        published_ms INTEGER,
        tier TEXT,
        narrative TEXT,
        reason TEXT,
        raw_analysis TEXT,
        discovered_at TEXT,
        pushed_at TEXT
    )
    """)
    conn.commit()
    conn.close()


def listing_id(symbol: str, published_ms: int, title: str) -> str:
    raw = f"{symbol}:{published_ms}:{title}".encode()
    return hashlib.md5(raw).hexdigest()[:16]


def exists(item_id: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    found = conn.execute("SELECT 1 FROM listings WHERE id=?", (item_id,)).fetchone() is not None
    conn.close()
    return found


def save_listing(row: dict):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
    INSERT OR IGNORE INTO listings
    (id, symbol, title, url, published_ms, tier, narrative, reason, raw_analysis, discovered_at, pushed_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        row["id"], row["symbol"], row["title"], row.get("url", ""), row.get("published_ms", 0),
        row.get("tier", "B"), row.get("narrative", "unknown"), row.get("reason", ""),
        row.get("raw_analysis", ""), datetime.now(timezone.utc).isoformat(), row.get("pushed_at"),
    ))
    conn.commit()
    conn.close()


def is_relevant(title: str) -> bool:
    text = title.lower()
    if any(k in text for k in EXCLUDE_KEYWORDS):
        return False
    return any(k in text for k in TRIGGER_KEYWORDS)


async def fetch_coingecko(symbol: str) -> dict:
    result = {"found": False, "mcap": 0.0, "fdv": 0.0, "categories": [], "description": ""}
    try:
        async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "Mozilla/5.0"}) as client:
            search = await client.get("https://api.coingecko.com/api/v3/search", params={"query": symbol})
            if search.status_code != 200:
                return result
            coin_id = None
            for coin in search.json().get("coins", []):
                if coin.get("symbol", "").upper() == symbol.upper():
                    coin_id = coin.get("id")
                    break
            if not coin_id:
                return result
            detail = await client.get(
                f"https://api.coingecko.com/api/v3/coins/{coin_id}",
                params={"localization": "false", "tickers": "false", "market_data": "true", "community_data": "false", "developer_data": "false"},
            )
            if detail.status_code != 200:
                return result
            data = detail.json()
            market = data.get("market_data", {})
            result.update({
                "found": True,
                "mcap": to_float((market.get("market_cap") or {}).get("usd")),
                "fdv": to_float((market.get("fully_diluted_valuation") or {}).get("usd")),
                "categories": data.get("categories", []) or [],
                "description": (data.get("description") or {}).get("en", "")[:500],
            })
    except Exception as exc:
        logger.warning("CoinGecko failed for %s: %s", symbol, exc)
    return result


def rule_analysis(symbol: str, title: str, cg: dict) -> dict:
    cats = " ".join(cg.get("categories", [])).lower()
    narrative = "unknown"
    if "ai" in cats:
        narrative = "ai"
    elif "defi" in cats:
        narrative = "defi"
    elif "meme" in cats:
        narrative = "meme"
    elif "gaming" in cats or "gamefi" in cats:
        narrative = "gamefi"
    elif "rwa" in cats or "real world" in cats:
        narrative = "rwa"

    fdv = cg.get("fdv") or 0
    mcap = cg.get("mcap") or 0
    if mcap and mcap < 20_000_000:
        tier, reason = "A", "OKX新上线 + 小流通市值，适合重点跟踪"
    elif fdv and fdv < 300_000_000:
        tier, reason = "B", "OKX新上线 + FDV不算极端"
    else:
        tier, reason = "C", "OKX新上线，但估值/数据优势不明显"
    if narrative in {"ai", "defi", "rwa"} and tier != "A":
        tier, reason = "B", f"OKX新上线 + {narrative}叙事"
    return {"tier": tier, "narrative": narrative, "reason": reason}


async def claude_analysis(symbol: str, title: str, cg: dict) -> dict:
    fallback = rule_analysis(symbol, title, cg)
    if not ANTHROPIC_API_KEY:
        return fallback
    prompt = f"""分析 OKX 新上线公告，只返回 JSON。
代币: {symbol}
公告: {title}
CoinGecko分类: {', '.join(cg.get('categories', []))}
市值: {cg.get('mcap')} FDV: {cg.get('fdv')}
项目描述: {cg.get('description', '')[:300]}

返回格式:
{{"tier":"A|B|C","narrative":"ai|defi|rwa|gamefi|meme|infra|unknown","reason":"一句中文理由"}}
"""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{ANTHROPIC_BASE_URL.rstrip('/')}/v1/messages",
                headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": ANTHROPIC_MODEL, "max_tokens": 500, "temperature": 0, "messages": [{"role": "user", "content": prompt}]},
            )
        if resp.status_code != 200:
            logger.warning("Claude failed: HTTP %s", resp.status_code)
            return fallback
        text = "".join(block.get("text", "") for block in resp.json().get("content", []) if block.get("type") == "text").strip()
        if text.startswith("```"):
            text = "\n".join(text.splitlines()[1:-1])
        data = json.loads(text)
        return {"tier": data.get("tier", fallback["tier"]), "narrative": data.get("narrative", fallback["narrative"]), "reason": data.get("reason", fallback["reason"])}
    except Exception as exc:
        logger.warning("Claude analysis failed for %s: %s", symbol, exc)
        return fallback


async def send_tg(text: str) -> bool:
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        logger.warning("TG not configured, stdout only:\n%s", text)
        return False
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage", json={"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"})
    if resp.status_code != 200:
        logger.error("TG failed %s: %s", resp.status_code, resp.text[:200])
        return False
    return True


def fmt_message(row: dict, cg: dict) -> str:
    published = datetime.fromtimestamp(row["published_ms"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if row.get("published_ms") else "unknown"
    lines = [
        f"<b>OKX 新上线 · ${row['symbol']}</b>",
        f"评级: <b>{row['tier']}</b>",
        f"叙事: {row['narrative']}",
        f"理由: {row['reason']}",
        f"时间: {published}",
    ]
    if cg.get("mcap"):
        lines.append(f"流通市值: ${cg['mcap']/1e6:.1f}M")
    if cg.get("fdv"):
        lines.append(f"FDV: ${cg['fdv']/1e6:.1f}M")
    lines.extend(["", row["title"]])
    if row.get("url"):
        lines.append(row["url"])
    return "\n".join(lines)


async def scan_once() -> int:
    count = 0
    for item in get_new_listing_announcements():
        title = item.get("title", "")
        if not is_relevant(title):
            continue
        symbol = extract_okx_symbol(title)
        if not symbol:
            continue
        item_id = listing_id(symbol, item.get("published_ms", 0), title)
        if exists(item_id):
            continue
        cg = await fetch_coingecko(symbol)
        analysis = await claude_analysis(symbol, title, cg)
        row = {"id": item_id, "symbol": symbol, "title": title, "url": item.get("url", ""), "published_ms": item.get("published_ms", 0), **analysis, "raw_analysis": json.dumps(analysis, ensure_ascii=False)}
        if await send_tg(fmt_message(row, cg)):
            row["pushed_at"] = datetime.now(timezone.utc).isoformat()
        save_listing(row)
        logger.info("new OKX listing %s [%s] %s", symbol, row["tier"], title)
        count += 1
        await asyncio.sleep(1)
    return count


async def main():
    init_db()
    logger.info("OKX listing monitor started, interval=%ss, db=%s", OKX_POLL_INTERVAL, DB_PATH)
    while True:
        try:
            count = await scan_once()
            logger.info("scan complete, new=%s", count)
        except Exception as exc:
            logger.error("scan failed: %s", exc, exc_info=True)
        await asyncio.sleep(OKX_POLL_INTERVAL)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
