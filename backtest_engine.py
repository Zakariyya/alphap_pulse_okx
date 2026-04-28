#!/usr/bin/env python3
"""BTC spring-strategy backtest engine for local OKX minute datasets."""

from __future__ import annotations

import csv
import gzip
import importlib.util
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

BASE_DIR = Path(__file__).resolve().parent
DATA_ROOT = BASE_DIR / "fullDataExtractionForBTC" / "data" / "okx" / "BTC-USDT-SWAP"
STRATEGY_DIR = BASE_DIR / "strategy"


@dataclass(frozen=True)
class Candle:
    timestamp: int
    iso_time: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass(frozen=True)
class FundingRate:
    timestamp: int
    iso_time: str
    rate: float


@dataclass(frozen=True)
class DatasetSummary:
    candles: int
    start: str | None
    end: str | None
    files: list[str]


def _parse_date_to_ms(value: str | None, *, end_of_day: bool = False) -> int | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    if len(text) == 10:
        text += "T23:59:59Z" if end_of_day else "T00:00:00Z"
    if text.endswith("+0800"):
        text = text[:-5] + "+08:00"
    elif text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _csv_files(dataset: str = "candles", data_root: Path = DATA_ROOT) -> list[Path]:
    root = data_root / dataset
    if not root.exists():
        return []
    files = [path for path in sorted(root.rglob("data.csv.gz")) + sorted(root.rglob("data.csv")) if path.is_file()]
    seen: set[Path] = set()
    result: list[Path] = []
    for path in files:
        real = path.resolve()
        if real not in seen:
            seen.add(real)
            result.append(path)
    return result


def _open_csv(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", newline="")
    return path.open("r", encoding="utf-8", newline="")


def load_candles(
    start: str | None = None,
    end: str | None = None,
    *,
    dataset: str = "candles",
    data_root: Path = DATA_ROOT,
) -> list[Candle]:
    start_ms = _parse_date_to_ms(start)
    end_ms = _parse_date_to_ms(end, end_of_day=True)
    candles: list[Candle] = []
    seen_ts: set[int] = set()
    for path in _csv_files(dataset, data_root):
        with _open_csv(path) as fh:
            for row in csv.DictReader(fh):
                ts = int(float(row["ts"]))
                if start_ms is not None and ts < start_ms:
                    continue
                if end_ms is not None and ts > end_ms:
                    continue
                if ts in seen_ts:
                    continue
                seen_ts.add(ts)
                candles.append(
                    Candle(
                        timestamp=ts,
                        iso_time=row.get("iso_time") or _iso_from_ms(ts),
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=float(row.get("volume_quote") or row.get("volume_base") or 0),
                    )
                )
    return sorted(candles, key=lambda item: item.timestamp)


def load_funding_rates(
    start: str | None = None,
    end: str | None = None,
    *,
    data_root: Path = DATA_ROOT,
) -> list[FundingRate]:
    start_ms = _parse_date_to_ms(start)
    end_ms = _parse_date_to_ms(end, end_of_day=True)
    rates: list[FundingRate] = []
    seen_ts: set[int] = set()
    for path in _csv_files("funding_rates", data_root):
        with _open_csv(path) as fh:
            for row in csv.DictReader(fh):
                ts = int(float(row["funding_time"]))
                if start_ms is not None and ts < start_ms:
                    continue
                if end_ms is not None and ts > end_ms:
                    continue
                if ts in seen_ts:
                    continue
                seen_ts.add(ts)
                rates.append(
                    FundingRate(
                        timestamp=ts,
                        iso_time=row.get("iso_time") or _iso_from_ms(ts),
                        rate=float(row.get("realized_rate") or row.get("funding_rate") or 0),
                    )
                )
    return sorted(rates, key=lambda item: item.timestamp)


def summarize_dataset(data_root: Path = DATA_ROOT) -> DatasetSummary:
    files = _csv_files("candles", data_root)
    first: Candle | None = None
    last: Candle | None = None
    count = 0
    seen_ts: set[int] = set()
    for path in files:
        with _open_csv(path) as fh:
            for row in csv.DictReader(fh):
                ts = int(float(row["ts"]))
                if ts in seen_ts:
                    continue
                seen_ts.add(ts)
                count += 1
                item = Candle(
                    timestamp=ts,
                    iso_time=row.get("iso_time") or _iso_from_ms(ts),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume_quote") or row.get("volume_base") or 0),
                )
                if first is None or item.timestamp < first.timestamp:
                    first = item
                if last is None or item.timestamp > last.timestamp:
                    last = item
    return DatasetSummary(
        candles=count,
        start=first.iso_time if first else None,
        end=last.iso_time if last else None,
        files=[_display_path(path) for path in files],
    )


def aggregate_bars(candles: list[Candle], minutes: int = 60) -> list[Candle]:
    if not candles:
        return []
    bucket_ms = minutes * 60 * 1000
    buckets: dict[int, list[Candle]] = {}
    for candle in candles:
        bucket = candle.timestamp - (candle.timestamp % bucket_ms)
        buckets.setdefault(bucket, []).append(candle)
    bars: list[Candle] = []
    for bucket, rows in sorted(buckets.items()):
        rows = sorted(rows, key=lambda item: item.timestamp)
        bars.append(
            Candle(
                timestamp=bucket,
                iso_time=_iso_from_ms(bucket),
                open=rows[0].open,
                high=max(row.high for row in rows),
                low=min(row.low for row in rows),
                close=rows[-1].close,
                volume=sum(row.volume for row in rows),
            )
        )
    return bars


def discover_strategies(strategy_dir: Path = STRATEGY_DIR) -> dict[str, Any]:
    strategies: dict[str, Any] = {}
    if not strategy_dir.exists():
        return strategies
    for path in sorted(strategy_dir.glob("*.py")):
        if path.name.startswith("_"):
            continue
        spec = importlib.util.spec_from_file_location(f"strategy_{path.stem}", path)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        factory = getattr(module, "get_strategy", None)
        strategy = factory() if callable(factory) else getattr(module, "STRATEGY", None)
        if strategy is None:
            continue
        strategies[getattr(strategy, "name", path.stem)] = strategy
    return strategies


def run_backtest(
    candles: list[Candle],
    strategy: Any,
    *,
    initial_capital: float,
    fee_rate: float = 0.0005,
    slippage_rate: float = 0.0002,
    offense_weight: float = 0.3,
    signal_minutes: int = 60,
    funding_rates: list[FundingRate] | None = None,
    params: dict[str, Any] | None = None,
    downsample_max_points: int = 600,
) -> dict[str, Any]:
    params = params or {}
    bars = aggregate_bars(candles, signal_minutes)
    signals = strategy.generate_signals(bars, params)
    signal_by_ts = {int(item["timestamp"]): item for item in signals}
    position: dict[str, Any] | None = None
    equity = float(initial_capital)
    trades: list[dict[str, Any]] = []
    curve: list[dict[str, Any]] = []
    last_price = candles[0].close if candles else 0
    funding_rates = funding_rates or []
    funding_index = 0
    total_funding_pnl = 0.0

    for candle in candles:
        signal = signal_by_ts.get(candle.timestamp)
        if position:
            exit_reason = None
            exit_price = None
            if position["side"] == 1:
                if candle.low <= position["stop"]:
                    exit_price = position["stop"] * (1 - slippage_rate)
                    exit_reason = "stop_loss"
                elif candle.high >= position["take_profit"]:
                    exit_price = position["take_profit"] * (1 - slippage_rate)
                    exit_reason = "take_profit"
            else:
                if candle.high >= position["stop"]:
                    exit_price = position["stop"] * (1 + slippage_rate)
                    exit_reason = "stop_loss"
                elif candle.low <= position["take_profit"]:
                    exit_price = position["take_profit"] * (1 + slippage_rate)
                    exit_reason = "take_profit"
            if signal and signal.get("signal", 0) != position["side"]:
                exit_price = candle.close * (1 - slippage_rate * position["side"])
                exit_reason = "signal_flip"
            if exit_reason and exit_price is not None:
                equity = _close_position(position, candle, exit_price, exit_reason, equity, fee_rate, trades)
                position = None

        if signal and not position and signal.get("signal", 0) in (-1, 1):
            side = int(signal["signal"])
            entry_price = candle.close * (1 + slippage_rate * side)
            risk_capital = max(0.0, min(1.0, offense_weight)) * equity
            if risk_capital > 0 and entry_price > 0:
                sl_pct = float(signal.get("sl_pct") or params.get("sl_pct") or 0.012)
                tp_pct = float(signal.get("tp_pct") or params.get("tp_pct") or sl_pct * 2)
                position = {
                    "side": side,
                    "entry_time": candle.iso_time,
                    "entry_ts": candle.timestamp,
                    "entry_price": entry_price,
                    "notional": risk_capital,
                    "qty": risk_capital / entry_price,
                    "stop": entry_price * (1 - sl_pct * side),
                    "take_profit": entry_price * (1 + tp_pct * side),
                    "funding_pnl": 0.0,
                    "meta": signal.get("meta", ""),
                }
                equity -= risk_capital * fee_rate

        while funding_index < len(funding_rates) and funding_rates[funding_index].timestamp <= candle.timestamp:
            funding = funding_rates[funding_index]
            if position and funding.timestamp > position["entry_ts"]:
                funding_pnl = -position["side"] * position["notional"] * funding.rate
                position["funding_pnl"] += funding_pnl
                total_funding_pnl += funding_pnl
                equity += funding_pnl
            funding_index += 1

        mark_equity = equity
        if position:
            mark_equity += (candle.close - position["entry_price"]) * position["qty"] * position["side"]
        curve.append({"timestamp": candle.iso_time, "equity": mark_equity})
        last_price = candle.close

    if position and candles:
        exit_price = last_price * (1 - slippage_rate * position["side"])
        equity = _close_position(position, candles[-1], exit_price, "end_of_period", equity, fee_rate, trades)
        curve.append({"timestamp": candles[-1].iso_time, "equity": equity})

    metrics = calculate_metrics(curve, trades, initial_capital=initial_capital)
    return {
        "strategy": getattr(strategy, "name", strategy.__class__.__name__),
        "description": getattr(strategy, "description", ""),
        "parameters": getattr(strategy, "default_params", {}),
        "metrics": metrics,
        "trades": trades,
        "equity_curve": _downsample_curve(curve, downsample_max_points),
        "signals": len(signals),
        "bars": len(bars),
        "candles": len(candles),
        "funding_events": len(funding_rates),
    }


def run_combined_backtest(
    candles: list[Candle],
    selected: list[dict[str, Any]],
    strategy_pool: dict[str, Any],
    *,
    initial_capital: float,
    fee_rate: float,
    slippage_rate: float,
    signal_minutes: int,
    funding_rates: list[FundingRate],
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    params = params or {}
    if not selected:
        raise ValueError("至少选择一个策略")
    normalized: list[dict[str, Any]] = []
    total_weight = 0.0
    for item in selected:
        name = str(item.get("name", "")).strip()
        if name not in strategy_pool:
            raise ValueError(f"未知策略: {name}")
        weight = float(item.get("weight", 0))
        if weight <= 0:
            raise ValueError(f"策略 {name} 的占比必须大于 0")
        total_weight += weight
        normalized.append({"name": name, "weight": weight})
    if total_weight > 1.0:
        raise ValueError(f"策略总占比不能超过 1，当前为 {round(total_weight, 4)}")

    details: list[dict[str, Any]] = []
    combined_trades: list[dict[str, Any]] = []
    curve_by_timestamp: dict[str, float] = {}
    candle_timestamps = [candle.iso_time for candle in candles]
    idle_cash = initial_capital * (1 - total_weight)
    for timestamp in candle_timestamps:
        curve_by_timestamp[timestamp] = idle_cash

    for item in normalized:
        capital = initial_capital * item["weight"]
        strategy = strategy_pool[item["name"]]
        sub = run_backtest(
            candles,
            strategy,
            initial_capital=capital,
            fee_rate=fee_rate,
            slippage_rate=slippage_rate,
            offense_weight=1.0,
            signal_minutes=signal_minutes,
            funding_rates=funding_rates,
            params=params,
            downsample_max_points=max(1200, len(candles) + 2),
        )
        details.append(
            {
                "name": item["name"],
                "title": getattr(strategy, "title", item["name"]),
                "weight": round(item["weight"], 4),
                "metrics": sub["metrics"],
                "trades": sub["metrics"]["trades"],
                "signals": sub["signals"],
            }
        )
        for trade in sub["trades"]:
            row = dict(trade)
            row["strategy"] = item["name"]
            combined_trades.append(row)
        latest_equity = capital
        equity_points = sub["equity_curve"]
        point_by_ts = {row["timestamp"]: float(row["equity"]) for row in equity_points}
        for timestamp in candle_timestamps:
            if timestamp in point_by_ts:
                latest_equity = point_by_ts[timestamp]
            curve_by_timestamp[timestamp] += latest_equity

    combined_curve = [{"timestamp": ts, "equity": curve_by_timestamp[ts]} for ts in candle_timestamps]
    combined_trades.sort(key=lambda row: row["entry_time"])
    combined_trades.sort(key=lambda row: row["exit_time"])
    running_equity = initial_capital
    for trade in combined_trades:
        running_equity += float(trade.get("pnl", 0))
        trade["account_equity_after_trade"] = round(running_equity, 2)
    metrics = calculate_metrics(combined_curve, combined_trades, initial_capital=initial_capital)
    return {
        "strategy": "combined",
        "description": "多策略组合回测",
        "combination": normalized,
        "combination_details": details,
        "metrics": metrics,
        "trades": combined_trades,
        "equity_curve": _downsample_curve(combined_curve, 600),
        "signals": sum(detail["signals"] for detail in details),
        "bars": len(aggregate_bars(candles, signal_minutes)),
        "candles": len(candles),
        "funding_events": len(funding_rates),
    }


def calculate_metrics(
    equity_curve: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    *,
    initial_capital: float,
) -> dict[str, float | int]:
    if not equity_curve:
        return {
            "initial_capital": round(initial_capital, 2),
            "final_equity": round(initial_capital, 2),
            "net_pnl": 0,
            "funding_pnl": 0,
            "return_pct": 0,
            "trades": 0,
            "win_rate": 0,
            "profit_factor": 0,
            "expectancy": 0,
            "max_drawdown_pct": 0,
            "sharpe": 0,
            "sortino": 0,
            "calmar": 0,
        }
    equities = [float(row["equity"]) for row in equity_curve]
    final_equity = equities[-1]
    pnl_values = [float(trade["pnl"]) for trade in trades]
    funding_pnl = sum(float(trade.get("funding_pnl", 0)) for trade in trades)
    wins = [pnl for pnl in pnl_values if pnl > 0]
    losses = [pnl for pnl in pnl_values if pnl < 0]
    returns = [(equities[i] / equities[i - 1] - 1) for i in range(1, len(equities)) if equities[i - 1] > 0]
    max_dd = _max_drawdown(equities)
    annual_factor = math.sqrt(365 * 24 * 60)
    avg_ret = _mean(returns)
    std_ret = _std(returns)
    downside = _std([value for value in returns if value < 0])
    total_return = final_equity / initial_capital - 1 if initial_capital else 0
    return {
        "initial_capital": round(initial_capital, 2),
        "final_equity": round(final_equity, 2),
        "net_pnl": round(final_equity - initial_capital, 2),
        "funding_pnl": round(funding_pnl, 2),
        "return_pct": round(total_return * 100, 2),
        "trades": len(trades),
        "win_rate": round(len(wins) / len(trades) * 100, 2) if trades else 0,
        "profit_factor": round(sum(wins) / abs(sum(losses)), 4) if losses else round(sum(wins), 4) if wins else 0,
        "expectancy": round(_mean(pnl_values), 2) if pnl_values else 0,
        "avg_win": round(_mean(wins), 2) if wins else 0,
        "avg_loss": round(_mean(losses), 2) if losses else 0,
        "max_drawdown_pct": round(max_dd * 100, 2),
        "sharpe": round(avg_ret / std_ret * annual_factor, 4) if std_ret else 0,
        "sortino": round(avg_ret / downside * annual_factor, 4) if downside else 0,
        "calmar": round(total_return / max_dd, 4) if max_dd else 0,
    }


def _close_position(
    position: dict[str, Any],
    candle: Candle,
    exit_price: float,
    reason: str,
    equity: float,
    fee_rate: float,
    trades: list[dict[str, Any]],
) -> float:
    gross = (exit_price - position["entry_price"]) * position["qty"] * position["side"]
    exit_fee = position["notional"] * fee_rate
    funding_pnl = float(position.get("funding_pnl", 0))
    # Funding pnl has already been applied to equity at each funding timestamp.
    # For equity bookkeeping here, only apply close-time trading pnl once.
    close_pnl = gross - exit_fee
    pnl = close_pnl + funding_pnl
    next_equity = equity + close_pnl
    entry_notional = position["qty"] * position["entry_price"]
    exit_notional = position["qty"] * exit_price
    trades.append(
        {
            "side": "long" if position["side"] == 1 else "short",
            "entry_time": position["entry_time"],
            "exit_time": candle.iso_time,
            "entry_price": round(position["entry_price"], 2),
            "exit_price": round(exit_price, 2),
            "entry_notional": round(entry_notional, 2),
            "exit_notional": round(exit_notional, 2),
            "pnl": round(pnl, 2),
            "funding_pnl": round(funding_pnl, 2),
            "return_pct": round(pnl / position["notional"] * 100, 4) if position["notional"] else 0,
            "account_equity_after_trade": round(next_equity, 2),
            "reason": _reason_to_cn(reason),
            "meta": position.get("meta", ""),
        }
    )
    return next_equity


def _max_drawdown(values: list[float]) -> float:
    peak = values[0] if values else 0
    max_dd = 0.0
    for value in values:
        peak = max(peak, value)
        if peak:
            max_dd = max(max_dd, (peak - value) / peak)
    return max_dd


def _mean(values: Iterable[float]) -> float:
    rows = list(values)
    return sum(rows) / len(rows) if rows else 0.0


def _std(values: Iterable[float]) -> float:
    rows = list(values)
    if len(rows) < 2:
        return 0.0
    avg = _mean(rows)
    return math.sqrt(sum((value - avg) ** 2 for value in rows) / (len(rows) - 1))


def _downsample_curve(curve: list[dict[str, Any]], max_points: int) -> list[dict[str, Any]]:
    if len(curve) <= max_points:
        return curve
    step = max(1, math.ceil(len(curve) / max_points))
    result = curve[::step]
    if result[-1] != curve[-1]:
        result.append(curve[-1])
    return result


def _iso_from_ms(ts: int) -> str:
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(BASE_DIR))
    except ValueError:
        return str(path)


def _reason_to_cn(reason: str) -> str:
    mapping = {
        "stop_loss": "止损",
        "take_profit": "止盈",
        "signal_flip": "信号反转平仓",
        "end_of_period": "回测结束平仓",
    }
    return mapping.get(reason, reason)


def run_backtest_from_request(payload: dict[str, Any]) -> dict[str, Any]:
    strategies = discover_strategies()
    candles = load_candles(payload.get("start"), payload.get("end"))
    if not candles:
        raise ValueError("所选时间段没有可用 candles 数据")
    funding_rates = load_funding_rates(payload.get("start"), payload.get("end"))
    initial_capital = float(payload.get("initial_capital") or 10000)
    fee_rate = float(payload.get("fee_rate") or 0.0005)
    slippage_rate = float(payload.get("slippage_rate") or 0.0002)
    signal_minutes = int(payload.get("signal_minutes") or 60)
    params = payload.get("params") or {}
    if isinstance(payload.get("strategies"), list):
        return run_combined_backtest(
            candles,
            payload["strategies"],
            strategies,
            initial_capital=initial_capital,
            fee_rate=fee_rate,
            slippage_rate=slippage_rate,
            signal_minutes=signal_minutes,
            funding_rates=funding_rates,
            params=params,
        )
    strategy_name = payload.get("strategy") or "volatility_breakout"
    if strategy_name not in strategies:
        raise ValueError(f"未知策略: {strategy_name}")
    return run_backtest(
        candles,
        strategies[strategy_name],
        initial_capital=initial_capital,
        fee_rate=fee_rate,
        slippage_rate=slippage_rate,
        offense_weight=float(payload.get("offense_weight") or 0.3),
        signal_minutes=signal_minutes,
        funding_rates=funding_rates,
        params=params,
    )
