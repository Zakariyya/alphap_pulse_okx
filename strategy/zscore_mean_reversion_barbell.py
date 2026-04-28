from __future__ import annotations

from statistics import mean, pstdev

from strategy._common import atr, donchian_widths, percentile_threshold


class ZScoreMeanReversion:
    name = "zscore_mean_reversion"
    title = "统计偏离回归"
    description = "低波动背景下等待价格偏离均值过深，出现回归迹象后入场。"
    default_params = {
        "compress_window": 24,
        "percentile_lookback": 120,
        "compress_quantile": 0.3,
        "z_window": 48,
        "z_entry": 1.8,
        "cooldown_bars": 5,
        "sl_atr": 1.0,
        "tp_atr": 1.4,
    }

    def generate_signals(self, bars, params):
        cfg = {**self.default_params, **(params or {})}
        if len(bars) < max(cfg["percentile_lookback"], cfg["z_window"]) + 2:
            return []
        widths = donchian_widths(bars, int(cfg["compress_window"]))
        atrs = atr(bars, int(cfg["compress_window"]))
        closes = [bar.close for bar in bars]
        signals = []
        cooldown = 0
        for i in range(int(cfg["z_window"]) + 1, len(bars)):
            if cooldown > 0:
                cooldown -= 1
                continue
            decision = bars[i - 1]
            width_threshold = percentile_threshold(widths, int(cfg["percentile_lookback"]), float(cfg["compress_quantile"]), i - 2)
            if widths[i - 2] > width_threshold:
                continue
            window = closes[i - 1 - int(cfg["z_window"]) : i - 1]
            avg = mean(window)
            dev = pstdev(window) or 1
            prev_z = (closes[i - 2] - avg) / dev
            curr_z = (closes[i - 1] - avg) / dev
            sl_pct, tp_pct = _risk_pct(atrs[i - 1], decision.close, cfg)
            if prev_z < -float(cfg["z_entry"]) and curr_z > prev_z:
                signals.append(_signal(bars[i], 1, sl_pct, tp_pct, "低位极端偏离后回归"))
                cooldown = int(cfg["cooldown_bars"])
            elif prev_z > float(cfg["z_entry"]) and curr_z < prev_z:
                signals.append(_signal(bars[i], -1, sl_pct, tp_pct, "高位极端偏离后回归"))
                cooldown = int(cfg["cooldown_bars"])
        return signals


def _risk_pct(atr_value, close, cfg):
    base = atr_value / close if close else 0.01
    return max(0.004, base * float(cfg["sl_atr"])), max(0.006, base * float(cfg["tp_atr"]))


def _signal(bar, side, sl_pct, tp_pct, meta):
    return {
        "timestamp": bar.timestamp,
        "signal": side,
        "confidence": 0.58,
        "sl_pct": sl_pct,
        "tp_pct": tp_pct,
        "meta": meta,
    }


def get_strategy():
    return ZScoreMeanReversion()
