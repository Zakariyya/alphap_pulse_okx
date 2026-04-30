from datetime import datetime, timezone

from backtest_engine import (
    Candle,
    FundingRate,
    aggregate_bars,
    calculate_metrics,
    discover_strategies,
    load_funding_rates,
    run_backtest_from_request,
    run_backtest,
    summarize_dataset,
)


class FlipLongStrategy:
    name = "flip_long"

    def generate_signals(self, bars, params):
        return [
            {
                "timestamp": bars[1].timestamp,
                "signal": 1,
                "confidence": 1,
                "sl_pct": 0.01,
                "tp_pct": 0.02,
                "meta": "test long",
            },
            {
                "timestamp": bars[-1].timestamp,
                "signal": 0,
                "confidence": 0,
                "meta": "flat",
            },
        ]


def _candle(minute: int, price: float) -> Candle:
    return Candle(
        timestamp=int(datetime(2026, 1, 1, 0, minute, tzinfo=timezone.utc).timestamp() * 1000),
        iso_time=f"2026-01-01T00:{minute:02d}:00Z",
        open=price,
        high=price + 0.5,
        low=price - 0.5,
        close=price,
        volume=10,
    )


def test_aggregate_bars_keeps_ohlcv_shape():
    candles = [_candle(0, 100), _candle(1, 101), _candle(2, 99)]
    bars = aggregate_bars(candles, minutes=60)

    assert len(bars) == 1
    assert bars[0].open == 100
    assert bars[0].high == 101.5
    assert bars[0].low == 98.5
    assert bars[0].close == 99
    assert bars[0].volume == 30


def test_run_backtest_returns_trade_and_core_metrics():
    candles = [_candle(i, 100 + i * 0.1) for i in range(60)]
    result = run_backtest(
        candles,
        FlipLongStrategy(),
        initial_capital=10_000,
        fee_rate=0.0005,
        slippage_rate=0.0002,
        offense_weight=0.3,
        signal_minutes=1,
    )

    assert result["metrics"]["trades"] == 1
    assert result["metrics"]["win_rate"] > 0
    assert result["metrics"]["net_pnl"] > 0
    assert result["trades"][0]["side"] == "long"
    assert result["trades"][0]["reason"] in {"止盈", "止损", "信号反转平仓", "回测结束平仓"}
    assert result["trades"][0]["entry_notional"] > 0
    assert result["trades"][0]["exit_notional"] > 0
    assert result["trades"][0]["account_equity_after_trade"] == result["metrics"]["final_equity"]


def test_run_backtest_applies_funding_rate_to_open_position():
    candles = [_candle(i, 100) for i in range(4)]
    funding = [FundingRate(timestamp=candles[2].timestamp, iso_time=candles[2].iso_time, rate=0.01)]

    long_result = run_backtest(
        candles,
        FlipLongStrategy(),
        initial_capital=10_000,
        fee_rate=0,
        slippage_rate=0,
        offense_weight=0.5,
        signal_minutes=1,
        funding_rates=funding,
    )

    assert long_result["metrics"]["funding_pnl"] == -50
    assert long_result["trades"][0]["funding_pnl"] == -50
    assert long_result["metrics"]["final_equity"] == 9950


def test_calculate_metrics_handles_empty_equity_curve():
    metrics = calculate_metrics([], [], initial_capital=1000)

    assert metrics["final_equity"] == 1000
    assert metrics["trades"] == 0
    assert metrics["max_drawdown_pct"] == 0


def test_discover_strategies_finds_repo_strategy_files():
    strategies = discover_strategies()

    assert "volatility_breakout" in strategies
    assert "false_break_reversal" in strategies
    assert "state_machine_spring" in strategies
    assert "zscore_mean_reversion" in strategies
    assert "small_barbell" in strategies
    assert "fixed_barbell" in strategies
    assert "defensive_barbell" in strategies


def test_summarize_dataset_ignores_data_csv_directories(tmp_path):
    month = tmp_path / "candles" / "year=2026" / "month=01"
    month.mkdir(parents=True)
    (month / "data.csv").mkdir()
    real_file = month / "real" / "data.csv"
    real_file.parent.mkdir()
    real_file.write_text(
        "ts,iso_time,dataset,instrument_id,bar,open,high,low,close,volume_quote\n"
        "1767225600000,2026-01-01T00:00:00Z,candles,BTC-USDT-SWAP,1m,100,101,99,100.5,10\n",
        encoding="utf-8",
    )

    summary = summarize_dataset(tmp_path)

    assert summary.candles == 1
    assert summary.files == [str(real_file)]


def test_load_funding_rates_reads_fixed_dataset_shape(tmp_path):
    month = tmp_path / "funding_rates" / "year=2026" / "month=01"
    month.mkdir(parents=True)
    path = month / "data.csv"
    path.write_text(
        "funding_time,iso_time,instrument_id,funding_rate,realized_rate,method,formula_type\n"
        "1767225600000,2026-01-01T00:00:00Z,BTC-USDT-SWAP,0.0001,0.0001,current_period,withRate\n",
        encoding="utf-8",
    )

    rows = load_funding_rates("2026-01-01", "2026-01-01", data_root=tmp_path)

    assert len(rows) == 1
    assert rows[0].rate == 0.0001


def test_run_backtest_from_request_supports_multi_strategy_combo():
    payload = {
        "start": "2026-03-01",
        "end": "2026-03-03",
        "initial_capital": 10000,
        "fee_rate": 0.0005,
        "slippage_rate": 0.0002,
        "signal_minutes": 60,
        "strategies": [
            {"name": "volatility_breakout", "weight": 0.4},
            {"name": "false_break_reversal", "weight": 0.3},
        ],
    }

    result = run_backtest_from_request(payload)

    assert result["strategy"] == "combined"
    assert len(result["combination"]) == 2
    assert result["combination"][0]["weight"] == 0.4
    assert result["metrics"]["initial_capital"] == 10000



def test_combined_single_strategy_trade_equity_matches_final_equity():
    payload = {
        "start": "2025-10-01",
        "end": "2026-02-28",
        "initial_capital": 100,
        "fee_rate": 0.0005,
        "slippage_rate": 0.0002,
        "signal_minutes": 5,
        "strategies": [
            {"name": "defensive_barbell", "weight": 1.0},
        ],
    }

    result = run_backtest_from_request(payload)

    assert result["strategy"] == "combined"
    assert result["trades"]
    assert result["trades"][-1]["account_equity_after_trade"] == result["metrics"]["final_equity"]
    assert round(result["equity_curve"][-1]["equity"], 2) == result["metrics"]["final_equity"]
