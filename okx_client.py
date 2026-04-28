#!/usr/bin/env python3
"""Small OKX V5 public API helpers used by the monitors."""

import re
import time
from typing import Any, Optional

import requests

OKX_BASE_URL = "https://www.okx.com"
HEADERS = {"User-Agent": "Mozilla/5.0"}


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def okx_get(path: str, params: Optional[dict] = None, timeout: int = 6, attempts: int = 3) -> dict:
    url = f"{OKX_BASE_URL}{path}"
    last_error = None
    for attempt in range(attempts):
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == "0":
                    return data
                last_error = RuntimeError(f"OKX code={data.get('code')} msg={data.get('msg')}")
            else:
                last_error = RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        except Exception as exc:  # network edge, retry then surface empty
            last_error = exc
        time.sleep(0.3 * (attempt + 1))
    raise RuntimeError(f"OKX request failed {path}: {last_error}")


def coin_from_inst_id(inst_id: str) -> str:
    return inst_id.split("-")[0].upper() if inst_id else ""


def extract_okx_symbol(title: str) -> Optional[str]:
    if not title:
        return None
    match = re.search(r"\(([A-Z0-9]{2,15})\)", title)
    if match:
        return match.group(1)
    match = re.search(r"launch\s+([A-Z0-9]{2,15})(?:/|-)", title, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    match = re.search(r"list\s+(?:[A-Za-z0-9 ]+\s+)?\(([A-Z0-9]{2,15})\)", title, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    return None


def normalize_ticker(raw: dict) -> dict:
    inst_id = raw.get("instId", "")
    price = to_float(raw.get("last"))
    open_24h = to_float(raw.get("open24h"))
    change = ((price - open_24h) / open_24h * 100) if open_24h else 0.0
    vol_ccy = to_float(raw.get("volCcy24h"))
    vol_usd = vol_ccy * price if vol_ccy and price else to_float(raw.get("vol24h")) * price
    return {
        "symbol": inst_id,
        "coin": coin_from_inst_id(inst_id),
        "price": price,
        "price_chg_24h": change,
        "volume_usd": vol_usd,
        "raw": raw,
    }


def oi_usd(raw: dict, price: float = 0.0) -> float:
    value = to_float(raw.get("oiUsd"))
    if value:
        return value
    return to_float(raw.get("oiCcy")) * price


def open_interest_map(rows: list[dict], price_by_symbol: Optional[dict[str, float]] = None) -> dict[str, float]:
    price_by_symbol = price_by_symbol or {}
    result = {}
    for row in rows:
        inst_id = row.get("instId", "")
        if inst_id:
            result[inst_id] = oi_usd(row, price_by_symbol.get(inst_id, 0.0))
    return result


def parse_okx_announcement_items(payload: dict) -> list[dict]:
    items = []
    for group in payload.get("data", []) or []:
        for detail in group.get("details", []) or []:
            ptime = detail.get("pTime") or detail.get("businessPTime") or 0
            try:
                published_ms = int(ptime)
            except (TypeError, ValueError):
                published_ms = 0
            items.append({
                "title": detail.get("title", ""),
                "published_ms": published_ms,
                "url": detail.get("url", ""),
            })
    return items


def get_swap_tickers() -> list[dict]:
    data = okx_get("/api/v5/market/tickers", {"instType": "SWAP"})
    return [normalize_ticker(x) for x in data.get("data", []) if x.get("instId", "").endswith("-USDT-SWAP")]


def get_funding_rate(inst_id: str) -> float:
    data = okx_get("/api/v5/public/funding-rate", {"instId": inst_id}, timeout=3, attempts=1)
    rows = data.get("data", [])
    return to_float(rows[0].get("fundingRate")) if rows else 0.0


def get_funding_history(inst_id: str, limit: int = 5) -> list[float]:
    data = okx_get("/api/v5/public/funding-rate-history", {"instId": inst_id, "limit": str(limit)})
    rows = list(reversed(data.get("data", []) or []))
    return [to_float(x.get("fundingRate")) for x in rows]


def get_open_interest_usd(inst_id: str, price: float = 0.0) -> float:
    data = okx_get("/api/v5/public/open-interest", {"instType": "SWAP", "instId": inst_id})
    rows = data.get("data", [])
    return oi_usd(rows[0], price) if rows else 0.0


def get_open_interest_map(price_by_symbol: Optional[dict[str, float]] = None) -> dict[str, float]:
    data = okx_get("/api/v5/public/open-interest", {"instType": "SWAP"})
    return open_interest_map(data.get("data", []) or [], price_by_symbol)


def get_mark_candles(inst_id: str, bar: str = "1H", limit: int = 6) -> list[list[str]]:
    data = okx_get("/api/v5/market/mark-price-candles", {"instId": inst_id, "bar": bar, "limit": str(limit)})
    return list(reversed(data.get("data", []) or []))


def get_market_candles(inst_id: str, bar: str = "1H", limit: int = 8) -> list[list[str]]:
    data = okx_get("/api/v5/market/candles", {"instId": inst_id, "bar": bar, "limit": str(limit)})
    return list(reversed(data.get("data", []) or []))


def get_new_listing_announcements(page: int = 1) -> list[dict]:
    data = okx_get("/api/v5/support/announcements", {
        "annType": "announcements-new-listings",
        "page": str(page),
    })
    return parse_okx_announcement_items(data)
