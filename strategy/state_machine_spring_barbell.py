from __future__ import annotations

from strategy._common import atr, donchian_widths, moving_average, percentile_threshold


class StateMachineSpring:
    name = "state_machine_spring"
    title = "F 压缩-预释放-释放状态机"
    description = "把弹簧拆成状态机：压缩、预释放、释放、冷却。"
    default_params = {
        "compress_window": 24,
        "percentile_lookback": 120,
        "compress_quantile": 0.25,
        "breakout_window": 20,
        "volume_window": 20,
        "volume_multiplier": 1.0,
        "cooldown_bars": 10,
        "sl_atr": 1.1,
        "tp_atr": 2.0,
    }

    def generate_signals(self, bars, params):
        cfg = {**self.default_params, **(params or {})}
        if len(bars) < max(cfg["percentile_lookback"], cfg["breakout_window"]) + 2:
            return []
        widths = donchian_widths(bars, int(cfg["compress_window"]))
        atrs = atr(bars, int(cfg["compress_window"]))
        volumes = [bar.volume for bar in bars]
        state = "idle"
        cooldown = 0
        signals = []
        for i in range(2, len(bars)):
            if cooldown > 0:
                cooldown -= 1
                continue
            decision = bars[i - 1]
            threshold = percentile_threshold(widths, int(cfg["percentile_lookback"]), float(cfg["compress_quantile"]), i - 2)
            compressed = widths[i - 2] <= threshold
            if state == "idle" and compressed:
                state = "compressed"
                continue
            if state == "compressed" and widths[i - 1] > widths[i - 2] * 1.15:
                state = "pre_release"
                continue
            if state != "pre_release":
                continue
            start = max(0, i - 1 - int(cfg["breakout_window"]))
            prior = bars[start : i - 1]
            upper = max(bar.high for bar in prior)
            lower = min(bar.low for bar in prior)
            vol_ok = decision.volume >= moving_average(volumes, int(cfg["volume_window"]), i - 2) * float(cfg["volume_multiplier"])
            sl_pct, tp_pct = _risk_pct(atrs[i - 1], decision.close, cfg)
            if vol_ok and decision.close > upper:
                signals.append(_signal(bars[i], 1, sl_pct, tp_pct, "状态机进入向上释放"))
                state = "cooldown"
                cooldown = int(cfg["cooldown_bars"])
            elif vol_ok and decision.close < lower:
                signals.append(_signal(bars[i], -1, sl_pct, tp_pct, "状态机进入向下释放"))
                state = "cooldown"
                cooldown = int(cfg["cooldown_bars"])
            elif not compressed:
                state = "idle"
        return signals


def _risk_pct(atr_value, close, cfg):
    base = atr_value / close if close else 0.012
    return max(0.004, base * float(cfg["sl_atr"])), max(0.008, base * float(cfg["tp_atr"]))


def _signal(bar, side, sl_pct, tp_pct, meta):
    return {
        "timestamp": bar.timestamp,
        "signal": side,
        "confidence": 0.68,
        "sl_pct": sl_pct,
        "tp_pct": tp_pct,
        "meta": meta,
    }


def get_strategy():
    return StateMachineSpring()
