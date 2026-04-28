from __future__ import annotations

from strategy._common import moving_average


class SmallBarbell:
    name = "small_barbell"
    title = "小仓位杠铃"
    description = "轻仓趋势策略，只有价格高于中期均线才入场。"
    default_params = {"ma_window": 48, "sl_pct": 0.03, "tp_pct": 0.06}

    def generate_signals(self, bars, params):
        cfg = {**self.default_params, **(params or {})}
        if len(bars) < int(cfg["ma_window"]) + 1:
            return []
        closes = [bar.close for bar in bars]
        signals = []
        in_long = False
        for i in range(2, len(bars)):
            decision = bars[i - 1]
            ma = moving_average(closes, int(cfg["ma_window"]), i - 2)
            if not in_long and decision.close > ma:
                signals.append(
                    {
                        "timestamp": bars[i].timestamp,
                        "signal": 1,
                        "confidence": 0.42,
                        "sl_pct": float(cfg["sl_pct"]),
                        "tp_pct": float(cfg["tp_pct"]),
                        "meta": "小仓位杠铃趋势入场",
                    }
                )
                in_long = True
            elif in_long and decision.close < ma:
                signals.append(
                    {
                        "timestamp": bars[i].timestamp,
                        "signal": 0,
                        "confidence": 0.3,
                        "meta": "小仓位杠铃趋势退出",
                    }
                )
                in_long = False
        return signals


def get_strategy():
    return SmallBarbell()
