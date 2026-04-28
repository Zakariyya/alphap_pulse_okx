from __future__ import annotations

from strategy._common import atr, donchian_widths, moving_average, percentile_threshold


class VolatilityBreakout:
    name = "volatility_breakout"
    title = "压缩突破"
    description = "寻找低波动压缩后的 Donchian 区间突破，适合趋势释放行情。"
    default_params = {
        "compress_window": 24,
        "percentile_lookback": 120,
        "compress_quantile": 0.2,
        "breakout_window": 20,
        "volume_window": 20,
        "volume_multiplier": 1.05,
        "cooldown_bars": 6,
        "sl_atr": 1.2,
        "tp_atr": 2.4,
    }

    def generate_signals(self, bars, params):
        cfg = {**self.default_params, **(params or {})}
        if len(bars) < max(cfg["percentile_lookback"], cfg["breakout_window"]) + 2:
            return []
        widths = donchian_widths(bars, int(cfg["compress_window"]))
        atrs = atr(bars, int(cfg["compress_window"]))
        volumes = [bar.volume for bar in bars]
        signals = []
        cooldown = 0
        for i in range(2, len(bars)):
            if cooldown > 0:
                cooldown -= 1
                continue
            decision = bars[i - 1]
            width_threshold = percentile_threshold(
                widths,
                int(cfg["percentile_lookback"]),
                float(cfg["compress_quantile"]),
                i - 2,
            )
            compressed = widths[i - 2] <= width_threshold
            start = max(0, i - 1 - int(cfg["breakout_window"]))
            prior = bars[start : i - 1]
            upper = max(bar.high for bar in prior)
            lower = min(bar.low for bar in prior)
            vol_ok = decision.volume >= moving_average(volumes, int(cfg["volume_window"]), i - 2) * float(cfg["volume_multiplier"])
            if compressed and vol_ok and decision.close > upper:
                sl_pct, tp_pct = _risk_pct(atrs[i - 1], decision.close, cfg)
                signals.append(_signal(bars[i], 1, sl_pct, tp_pct, "压缩后向上突破区间"))
                cooldown = int(cfg["cooldown_bars"])
            elif compressed and vol_ok and decision.close < lower:
                sl_pct, tp_pct = _risk_pct(atrs[i - 1], decision.close, cfg)
                signals.append(_signal(bars[i], -1, sl_pct, tp_pct, "压缩后向下突破区间"))
                cooldown = int(cfg["cooldown_bars"])
        return signals


def _risk_pct(atr_value, close, cfg):
    base = atr_value / close if close else 0.012
    return max(0.004, base * float(cfg["sl_atr"])), max(0.008, base * float(cfg["tp_atr"]))


def _signal(bar, side, sl_pct, tp_pct, meta):
    return {
        "timestamp": bar.timestamp,
        "signal": side,
        "confidence": 0.72,
        "sl_pct": sl_pct,
        "tp_pct": tp_pct,
        "meta": meta,
    }


def get_strategy():
    return VolatilityBreakout()
