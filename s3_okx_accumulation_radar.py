#!/usr/bin/env python3
"""Hourly OKX heat/OI/funding radar. Pure Python, no AI cost."""

import json
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

from okx_client import get_funding_rate, get_market_candles, get_open_interest_usd, get_swap_tickers

SCRIPT_DIR = Path(__file__).parent
ENV_FILES = [SCRIPT_DIR / ".env.okx", SCRIPT_DIR / ".env.oi"]
HEAT_HISTORY_FILE = SCRIPT_DIR / "okx_heat_history.json"
OI_HISTORY_FILE = SCRIPT_DIR / "okx_radar_oi_history.json"

VOL_SURGE_MULT = 2.5
MIN_VOL_USD = 20_000_000
MIN_OI_DELTA_PCT = 3.0
MIN_OI_USD = 2_000_000


def load_env_files():
    for path in ENV_FILES:
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


load_env_files()
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "")


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text()) if path.exists() else default
    except Exception:
        return default


def write_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def fmt_usd(value: float) -> str:
    if value >= 1e9:
        return f"${value/1e9:.1f}B"
    if value >= 1e6:
        return f"${value/1e6:.1f}M"
    if value >= 1e3:
        return f"${value/1e3:.0f}K"
    return f"${value:.0f}"


def send_telegram(text: str):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("\n[TG] 未配置，仅打印:\n")
        print(text)
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    for chunk in [text[i:i + 3900] for i in range(0, len(text), 3900)]:
        try:
            resp = requests.post(url, json={"chat_id": TG_CHAT_ID, "text": chunk, "parse_mode": "Markdown"}, timeout=10)
            if resp.status_code != 200:
                requests.post(url, json={"chat_id": TG_CHAT_ID, "text": chunk.replace("*", "")}, timeout=10)
        except Exception as exc:
            print(f"[TG] Error: {exc}")
        time.sleep(0.5)


def coingecko_trending() -> set[str]:
    try:
        resp = requests.get("https://api.coingecko.com/api/v3/search/trending", timeout=10)
        if resp.status_code == 200:
            return {item["item"]["symbol"].upper() for item in resp.json().get("coins", [])}
    except Exception as exc:
        print(f"CG Trending失败: {exc}")
    return set()


def daily_turnover(candles: list[list[str]]) -> list[float]:
    values = []
    for candle in candles:
        try:
            values.append(float(candle[7]))
        except (IndexError, TypeError, ValueError):
            values.append(0.0)
    return values


def update_oi_history(current: dict):
    history = read_json(OI_HISTORY_FILE, {})
    now = datetime.now().isoformat()
    deltas = {}
    for symbol, oi_value in current.items():
        rows = history.get(symbol, [])
        prev = rows[-1]["oi_usd"] if rows else None
        delta = ((oi_value - prev) / prev * 100) if prev else 0.0
        deltas[symbol] = delta
        rows.append({"ts": now, "oi_usd": oi_value})
        history[symbol] = rows[-168:]
    write_json(OI_HISTORY_FILE, history)
    return deltas


def main():
    print(f"OKX 热度/OI/费率雷达 — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    tickers = get_swap_tickers()
    ticker_map = {x["symbol"]: x for x in tickers}
    coin_to_symbol = {x["coin"]: x["symbol"] for x in tickers}

    cg = coingecko_trending()
    heat_map = {coin: max(50 - i * 3, 10) for i, coin in enumerate(cg)}
    print(f"CG Trending: {len(cg)}个币")

    vol_surge = set()
    top_by_vol = sorted(tickers, key=lambda x: x["volume_usd"], reverse=True)[:120]
    for i, ticker in enumerate(top_by_vol):
        if ticker["volume_usd"] < MIN_VOL_USD:
            continue
        try:
            candles = get_market_candles(ticker["symbol"], bar="1D", limit=8)
            vals = daily_turnover(candles)
            if len(vals) >= 5:
                avg_prev = sum(vals[:-1]) / max(1, len(vals) - 1)
                ratio = vals[-1] / avg_prev if avg_prev else 0
                if ratio >= VOL_SURGE_MULT:
                    vol_surge.add(ticker["coin"])
                    heat_map[ticker["coin"]] = heat_map.get(ticker["coin"], 0) + min(ratio * 10, 50)
        except Exception as exc:
            print(f"[WARN] 放量检测失败 {ticker['symbol']}: {exc}")
        if (i + 1) % 10 == 0:
            time.sleep(0.5)
    print(f"放量(≥{VOL_SURGE_MULT}x): {len(vol_surge)}个币")

    scan_symbols = {coin_to_symbol[c] for c in heat_map if c in coin_to_symbol}
    scan_symbols |= {x["symbol"] for x in top_by_vol[:80]}

    oi_current = {}
    funding = {}
    for i, symbol in enumerate(scan_symbols):
        ticker = ticker_map[symbol]
        try:
            oi_current[symbol] = get_open_interest_usd(symbol, ticker["price"])
            funding[symbol] = get_funding_rate(symbol) * 100
        except Exception as exc:
            print(f"[WARN] OKX OI/费率失败 {symbol}: {exc}")
        if (i + 1) % 15 == 0:
            time.sleep(0.5)
    oi_delta = update_oi_history(oi_current)
    print(f"OI扫描: {len(oi_current)}个币")

    coin_data = []
    for symbol, ticker in ticker_map.items():
        coin = ticker["coin"]
        d = {
            **ticker,
            "heat": heat_map.get(coin, 0),
            "in_cg": coin in cg,
            "vol_surge": coin in vol_surge,
            "oi_usd": oi_current.get(symbol, 0),
            "oi_delta": oi_delta.get(symbol, 0),
            "fr_pct": funding.get(symbol, 0),
        }
        coin_data.append(d)

    hot = sorted([x for x in coin_data if x["heat"] > 0], key=lambda x: x["heat"], reverse=True)
    heat_history = read_json(HEAT_HISTORY_FILE, {})
    now_ts = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    new_entries = []
    for item in hot:
        if item["coin"] not in heat_history:
            heat_history[item["coin"]] = {"first_seen": now_ts, "price_chg_24h": item["price_chg_24h"]}
            new_entries.append(item)
    cutoff = (datetime.now(timezone(timedelta(hours=8))) - timedelta(days=7)).strftime("%Y-%m-%d")
    heat_history = {k: v for k, v in heat_history.items() if v.get("first_seen", "9999") >= cutoff}
    write_json(HEAT_HISTORY_FILE, heat_history)

    chase = sorted(
        [x for x in coin_data if x["price_chg_24h"] > 3 and x["fr_pct"] < -0.005 and x["volume_usd"] > 1_000_000],
        key=lambda x: x["fr_pct"],
    )
    oi_alerts = sorted(
        [x for x in coin_data if abs(x["oi_delta"]) >= MIN_OI_DELTA_PCT and x["oi_usd"] >= MIN_OI_USD and x["heat"] == 0],
        key=lambda x: abs(x["oi_delta"]), reverse=True,
    )

    now = datetime.now(timezone(timedelta(hours=8)))
    lines = ["**OKX 热度/OI/费率雷达**", f"{now.strftime('%Y-%m-%d %H:%M')} CST"]

    if new_entries:
        lines.append("\n**[ 首次上榜 ]**")
        tbl = ["```", f"{'币种':<10} {'涨幅':>7} {'成交额':>10} 来源"]
        for x in new_entries[:10]:
            sources = []
            if x["in_cg"]: sources.append("CG")
            if x["vol_surge"]: sources.append("放量")
            tbl.append(f"{x['coin']:<10} {x['price_chg_24h']:>+6.1f}% {fmt_usd(x['volume_usd']):>10} {'/'.join(sources)}")
        tbl.append("```")
        lines.append("\n".join(tbl))

    lines.append("\n**[ 热度榜 ]**")
    if hot:
        tbl = ["```", f"{'币种':<10} {'涨幅':>7} {'OI':>10} 来源"]
        for x in hot[:10]:
            sources = []
            if x["in_cg"]: sources.append("CG")
            if x["vol_surge"]: sources.append("放量")
            if abs(x["oi_delta"]) >= 3: sources.append(f"OI{x['oi_delta']:+.1f}%")
            if x["fr_pct"] < -0.03: sources.append(f"费率{x['fr_pct']:.3f}%")
            tbl.append(f"{x['coin']:<10} {x['price_chg_24h']:>+6.1f}% {fmt_usd(x['oi_usd']):>10} {' '.join(sources)}")
        tbl.append("```")
        lines.append("\n".join(tbl))
    else:
        lines.append("暂无热点")

    lines.append("\n**[ 追多 ]** 涨了+费率负=空头燃料")
    if chase:
        tbl = ["```", f"{'币种':<10} {'费率':>10} {'涨幅':>7} {'成交额':>10}"]
        for x in chase[:8]:
            tbl.append(f"{x['coin']:<10} {x['fr_pct']:>+9.3f}% {x['price_chg_24h']:>+6.1f}% {fmt_usd(x['volume_usd']):>10}")
        tbl.append("```")
        lines.append("\n".join(tbl))
    else:
        lines.append("暂无符合条件的标的")

    if oi_alerts:
        lines.append("\n**[ OI异动 ]** 与上次运行快照相比")
        tbl = ["```", f"{'币种':<10} {'方向':>4} {'OI变化':>8} {'OI':>10}"]
        for x in oi_alerts[:8]:
            direction = "增仓" if x["oi_delta"] > 0 else "减仓"
            tbl.append(f"{x['coin']:<10} {direction:>4} {x['oi_delta']:>+7.1f}% {fmt_usd(x['oi_usd']):>10}")
        tbl.append("```")
        lines.append("\n".join(tbl))

    lines.append("\nCG=CoinGecko全球热度；放量=OKX SWAP日成交额较近几日放大")
    report = "\n".join(lines)
    send_telegram(report)
    print("\n完成")


if __name__ == "__main__":
    main()
