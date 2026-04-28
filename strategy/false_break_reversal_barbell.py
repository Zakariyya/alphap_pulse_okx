from __future__ import annotations

from strategy._common import atr, donchian_widths, percentile_threshold


class FalseBreakReversal:
    name = "false_break_reversal"
    title = "假突破反转"
    description = "低波动压缩后先扫区间边界，随后收回区间内反向入场。"
    default_params = {
        "compress_window": 24,
        "percentile_lookback": 120,
        "compress_quantile": 0.25,
        "range_window": 18,
        "reclaim_bars": 3,
        "cooldown_bars": 8,
        "sl_atr": 0.9,
        "tp_atr": 1.6,
    }

    def generate_signals(self, bars, params):
        cfg = {**self.default_params, **(params or {})}
        if len(bars) < max(cfg["percentile_lookback"], cfg["range_window"]) + cfg["reclaim_bars"]:
            return []
        widths = donchian_widths(bars, int(cfg["compress_window"]))
        atrs = atr(bars, int(cfg["compress_window"]))
        signals = []
        cooldown = 0
        for i in range(int(cfg["range_window"]) + 2, len(bars)):
            if cooldown > 0:
                cooldown -= 1
                continue
            decision = bars[i - 1]
            width_threshold = percentile_threshold(widths, int(cfg["percentile_lookback"]), float(cfg["compress_quantile"]), i - 2)
            if widths[i - 2] > width_threshold:
                continue
            start = i - 1 - int(cfg["range_window"])
            prior = bars[start : i - 1]
            upper = max(bar.high for bar in prior)
            lower = min(bar.low for bar in prior)
            reclaim_start = max(start, i - 1 - int(cfg["reclaim_bars"]))
            recent = bars[reclaim_start:i]
            swept_high = any(bar.high > upper for bar in recent)
            swept_low = any(bar.low < lower for bar in recent)
            sl_pct, tp_pct = _risk_pct(atrs[i - 1], decision.close, cfg)
            if swept_high and decision.close < upper:
                signals.append(_signal(bars[i], -1, sl_pct, tp_pct, "上沿假突破后收回区间"))
                cooldown = int(cfg["cooldown_bars"])
            elif swept_low and decision.close > lower:
                signals.append(_signal(bars[i], 1, sl_pct, tp_pct, "下沿假突破后收回区间"))
                cooldown = int(cfg["cooldown_bars"])
        return signals


def _risk_pct(atr_value, close, cfg):
    base = atr_value / close if close else 0.01
    return max(0.0035, base * float(cfg["sl_atr"])), max(0.006, base * float(cfg["tp_atr"]))


def _signal(bar, side, sl_pct, tp_pct, meta):
    return {
        "timestamp": bar.timestamp,
        "signal": side,
        "confidence": 0.64,
        "sl_pct": sl_pct,
        "tp_pct": tp_pct,
        "meta": meta,
    }


def get_strategy():
    return FalseBreakReversal()
