#!/usr/bin/env python3
"""OKX SWAP scanner: funding just turned negative while open interest is rising."""

import json
import os
import time
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import requests

from okx_client import get_funding_history, get_funding_rate, get_open_interest_map, get_swap_tickers

SCRIPT_DIR = Path(__file__).parent
ENV_FILES = [SCRIPT_DIR / ".env.okx", SCRIPT_DIR / ".env.oi"]
FR_SNAPSHOT_FILE = SCRIPT_DIR / "okx_fr_snapshot.json"
OI_HISTORY_FILE = SCRIPT_DIR / "okx_oi_history.json"
ALERT_HISTORY_FILE = SCRIPT_DIR / "okx_oi_funding_alerts.json"

MIN_OI_CHANGE_PCT = 1.0
MIN_VOLUME_USD = 1_000_000
MIN_FR_PERIODS_POSITIVE = 2
DEDUP_HOURS = 24
FUNDING_WORKERS = int(os.getenv("OKX_FUNDING_WORKERS", "20"))


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


def send_tg(text: str):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("[TG] 未配置，仅打印:\n" + text)
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    for chunk in [text[i:i + 3900] for i in range(0, len(text), 3900)]:
        try:
            resp = requests.post(url, json={"chat_id": TG_CHAT_ID, "text": chunk, "parse_mode": "Markdown"}, timeout=10)
            if resp.status_code != 200:
                requests.post(url, json={"chat_id": TG_CHAT_ID, "text": chunk}, timeout=10)
        except Exception as exc:
            print(f"[TG] 发送失败: {exc}")


def is_duplicate(symbol: str, history: dict) -> bool:
    last = history.get(symbol)
    if not last:
        return False
    return (datetime.now() - datetime.fromisoformat(last)).total_seconds() < DEDUP_HOURS * 3600


def mark_alerted(symbol: str, history: dict) -> dict:
    history[symbol] = datetime.now().isoformat()
    cutoff = datetime.now() - timedelta(hours=DEDUP_HOURS * 2)
    return {k: v for k, v in history.items() if datetime.fromisoformat(v) > cutoff}


def previous_oi(symbol: str, history: dict):
    rows = history.get(symbol, [])
    return rows[-1]["oi_usd"] if rows else None


def update_oi_history(current: dict):
    history = read_json(OI_HISTORY_FILE, {})
    now = datetime.now().isoformat()
    for symbol, value in current.items():
        rows = history.setdefault(symbol, [])
        rows.append({"ts": now, "oi_usd": value})
        history[symbol] = rows[-288:]
    write_json(OI_HISTORY_FILE, history)
    return history


def collect_funding_rates(tickers: list[dict], rate_getter=get_funding_rate, max_workers: int = FUNDING_WORKERS) -> dict:
    rates = {}
    total = len(tickers)
    workers = max(1, min(max_workers, total or 1))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {executor.submit(rate_getter, ticker["symbol"]): ticker["symbol"] for ticker in tickers}
        for done_count, future in enumerate(as_completed(future_map), 1):
            symbol = future_map[future]
            try:
                rates[symbol] = future.result()
            except Exception as exc:
                print(f"[WARN] {symbol} OKX费率失败: {exc}", flush=True)
            if done_count % 25 == 0 or done_count == total:
                print(f"[进度] 已读取OKX费率 {done_count}/{total}", flush=True)
    return rates


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="OKX费率转负 + OI上涨扫描器")
    parser.add_argument(
        "--funding-workers",
        type=int,
        default=FUNDING_WORKERS,
        help=f"并发读取OKX funding rate的线程数，默认读取OKX_FUNDING_WORKERS或{FUNDING_WORKERS}",
    )
    return parser.parse_args(argv)


def scan(funding_workers: int = FUNDING_WORKERS):
    start = time.time()
    tickers = [x for x in get_swap_tickers() if x["volume_usd"] >= MIN_VOLUME_USD]
    ticker_map = {x["symbol"]: x for x in tickers}

    print(f"[{datetime.now().strftime('%H:%M:%S')}] 开始读取OKX费率: {len(tickers)}个合约，并发={funding_workers}", flush=True)
    fr_current = collect_funding_rates(tickers, max_workers=funding_workers)

    prev_fr = read_json(FR_SNAPSHOT_FILE, {})
    write_json(FR_SNAPSHOT_FILE, fr_current)
    price_by_symbol = {symbol: ticker["price"] for symbol, ticker in ticker_map.items()}
    prev_oi_history = read_json(OI_HISTORY_FILE, {})
    try:
        oi_current = get_open_interest_map(price_by_symbol)
    except Exception as exc:
        print(f"[WARN] OKX批量OI失败: {exc}", flush=True)
        oi_current = {}
    update_oi_history(oi_current)

    if not prev_fr:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 首次运行，保存OKX费率/OI快照，下次开始对比", flush=True)
        return []

    just_turned_negative = [
        symbol for symbol, curr_fr in fr_current.items()
        if prev_fr.get(symbol) is not None and prev_fr[symbol] >= 0 and curr_fr < 0
    ]
    if not just_turned_negative:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] OKX扫描完成: {len(tickers)}币/{time.time()-start:.1f}s, 无新转负", flush=True)
        return []

    print(f"[{datetime.now().strftime('%H:%M:%S')}] 发现刚转负 {len(just_turned_negative)} 个，开始校验OI上涨", flush=True)

    alerts = []
    alert_history = read_json(ALERT_HISTORY_FILE, {})
    for symbol in just_turned_negative:
        curr_fr = fr_current[symbol]
        old_fr = prev_fr[symbol]
        if is_duplicate(symbol, alert_history):
            continue
        ticker = ticker_map.get(symbol)
        if not ticker:
            continue
        old_oi = previous_oi(symbol, prev_oi_history)
        curr_oi = oi_current.get(symbol, 0)
        oi_change = ((curr_oi - old_oi) / old_oi * 100) if old_oi else 0
        if oi_change < MIN_OI_CHANGE_PCT:
            continue
        hist = get_funding_history(symbol, limit=MIN_FR_PERIODS_POSITIVE + 1)
        prev_periods = hist[-(MIN_FR_PERIODS_POSITIVE + 1):-1] if len(hist) >= MIN_FR_PERIODS_POSITIVE + 1 else []
        if len(prev_periods) < MIN_FR_PERIODS_POSITIVE or not all(x >= 0 for x in prev_periods):
            continue
        alert_history = mark_alerted(symbol, alert_history)
        alerts.append({**ticker, "prev_fr": old_fr, "current_fr": curr_fr, "oi_change": oi_change, "oi_usd": curr_oi})
        time.sleep(0.2)

    write_json(ALERT_HISTORY_FILE, alert_history)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] OKX扫描完成: {len(tickers)}币/{time.time()-start:.1f}s, 信号: {len(alerts)}")
    return alerts


def fmt_usd(value: float) -> str:
    if value >= 1e9:
        return f"${value/1e9:.2f}B"
    if value >= 1e6:
        return f"${value/1e6:.1f}M"
    return f"${value:,.0f}"


def format_alert(signals):
    if not signals:
        return ""
    signals.sort(key=lambda x: (-x["oi_change"], x["current_fr"]))
    lines = [f"*[ OKX费率刚转负 + OI上涨 ]* {datetime.now().strftime('%m-%d %H:%M')}\n"]
    for s in signals:
        lines.extend([
            "```",
            s["symbol"],
            f"  价格: {s['price']:.6g}  24h: {s['price_chg_24h']:+.1f}%",
            f"  费率: {s['prev_fr']:+.4%} -> {s['current_fr']:+.4%}",
            f"  OI: {fmt_usd(s['oi_usd'])} ({s['oi_change']:+.2f}%)",
            f"  成交额: {fmt_usd(s['volume_usd'])}",
            "```",
        ])
    return "\n".join(lines)


def main(argv=None):
    args = parse_args(argv)
    signals = scan(funding_workers=args.funding_workers)
    if signals:
        msg = format_alert(signals)
        send_tg(msg)
        print(f"  推送 {len(signals)} 个OKX信号")
    else:
        print("  无信号")


if __name__ == "__main__":
    main()
