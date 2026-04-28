from __future__ import annotations

from strategy._common import atr, moving_average


class DefensiveBarbell:
    name = "defensive_barbell"
    title = "防御杠铃"
    description = "防御型过滤策略：趋势为正且波动不过高时才持有多头。"
    default_params = {"ma_window": 72, "atr_window": 24, "atr_limit": 0.012, "sl_pct": 0.025, "tp_pct": 0.05}

    def generate_signals(self, bars, params):
        cfg = {**self.default_params, **(params or {})}
        if len(bars) < int(cfg["ma_window"]) + 1:
            return []
        closes = [bar.close for bar in bars]
        atrs = atr(bars, int(cfg["atr_window"]))
        signals = []
        in_long = False
        for i in range(2, len(bars)):
            decision = bars[i - 1]
            ma = moving_average(closes, int(cfg["ma_window"]), i - 2)
            atr_ratio = atrs[i - 1] / decision.close if decision.close else 0
            risk_ok = atr_ratio <= float(cfg["atr_limit"])
            if not in_long and decision.close > ma and risk_ok:
                signals.append(
                    {
                        "timestamp": bars[i].timestamp,
                        "signal": 1,
                        "confidence": 0.5,
                        "sl_pct": float(cfg["sl_pct"]),
                        "tp_pct": float(cfg["tp_pct"]),
                        "meta": "防御杠铃过滤后入场",
                    }
                )
                in_long = True
            elif in_long and (decision.close < ma or not risk_ok):
                signals.append(
                    {
                        "timestamp": bars[i].timestamp,
                        "signal": 0,
                        "confidence": 0.35,
                        "meta": "防御杠铃过滤退出",
                    }
                )
                in_long = False
        return signals


def get_strategy():
    return DefensiveBarbell()
