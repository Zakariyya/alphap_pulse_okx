from __future__ import annotations


class FixedBarbell:
    name = "fixed_barbell"
    title = "固定杠铃"
    description = "固定仓位的基准策略，持续持有多头作为组合的稳定基线。"
    default_params = {"sl_pct": 0.06, "tp_pct": 0.12}

    def generate_signals(self, bars, params):
        cfg = {**self.default_params, **(params or {})}
        if len(bars) < 2:
            return []
        return [
            {
                "timestamp": bars[1].timestamp,
                "signal": 1,
                "confidence": 0.45,
                "sl_pct": float(cfg["sl_pct"]),
                "tp_pct": float(cfg["tp_pct"]),
                "meta": "固定杠铃基线建仓",
            }
        ]


def get_strategy():
    return FixedBarbell()
