from __future__ import annotations

from strategy._common import atr, moving_average


class DefensiveBarbellV2:
    name = "defensive_barbell_v2"
    title = "防御杠铃 V2"
    description = "防御型趋势过滤增强版：加入滞回、连续确认、最短持仓和冷却期，避免短周期状态抖动。"
    default_params = {
        "ma_window": 72,
        "atr_window": 24,
        "atr_limit": 0.012,
        "entry_buffer": 0.0015,
        "exit_buffer": 0.0005,
        "confirm_bars": 2,
        "exit_confirm_bars": 2,
        "min_hold_bars": 3,
        "cooldown_bars": 2,
        "atr_exit_multiplier": 1.35,
        "sl_pct": 0.025,
        "tp_pct": 0.05,
    }

    def generate_signals(self, bars, params):
        cfg = {**self.default_params, **(params or {})}
        ma_window = int(cfg["ma_window"])
        atr_window = int(cfg["atr_window"])
        confirm_bars = max(1, int(cfg["confirm_bars"]))
        exit_confirm_bars = max(1, int(cfg["exit_confirm_bars"]))
        min_hold_bars = max(1, int(cfg["min_hold_bars"]))
        cooldown_bars = max(0, int(cfg["cooldown_bars"]))
        if len(bars) < max(ma_window + confirm_bars + 1, atr_window + exit_confirm_bars + 1):
            return []

        closes = [bar.close for bar in bars]
        atrs = atr(bars, atr_window)
        signals = []
        in_long = False
        cooldown = 0
        held_bars = 0

        for i in range(2, len(bars)):
            if cooldown > 0 and not in_long:
                cooldown -= 1
                continue

            decision_idx = i - 1
            decision = bars[decision_idx]
            atr_ratio = atrs[decision_idx] / decision.close if decision.close else 0.0

            if not in_long:
                if _entry_confirmed(bars, closes, atrs, decision_idx, cfg, confirm_bars):
                    signals.append(
                        {
                            "timestamp": bars[i].timestamp,
                            "signal": 1,
                            "confidence": 0.56,
                            "sl_pct": float(cfg["sl_pct"]),
                            "tp_pct": float(cfg["tp_pct"]),
                            "meta": "防御杠铃 V2 确认后入场",
                        }
                    )
                    in_long = True
                    held_bars = 0
                continue

            held_bars += 1
            trend_exit = held_bars >= min_hold_bars and _trend_exit_confirmed(
                bars,
                closes,
                decision_idx,
                cfg,
                exit_confirm_bars,
            )
            extreme_vol_exit = held_bars >= min_hold_bars and atr_ratio > float(cfg["atr_limit"]) * float(cfg["atr_exit_multiplier"]) and decision.close < _ma_at(closes, ma_window, decision_idx)
            if trend_exit or extreme_vol_exit:
                signals.append(
                    {
                        "timestamp": bars[i].timestamp,
                        "signal": 0,
                        "confidence": 0.4,
                        "meta": "防御杠铃 V2 过滤退出" if trend_exit else "防御杠铃 V2 极端波动退出",
                    }
                )
                in_long = False
                cooldown = cooldown_bars
                held_bars = 0

        return signals


def _ma_at(closes, window: int, index: int) -> float:
    return moving_average(closes, window, max(0, index - 1))


def _entry_confirmed(bars, closes, atrs, decision_idx: int, cfg, confirm_bars: int) -> bool:
    start = decision_idx - confirm_bars + 1
    if start < 1:
        return False
    entry_buffer = float(cfg["entry_buffer"])
    atr_limit = float(cfg["atr_limit"])
    ma_window = int(cfg["ma_window"])
    for idx in range(start, decision_idx + 1):
        ma = _ma_at(closes, ma_window, idx)
        close = bars[idx].close
        atr_ratio = atrs[idx] / close if close else 0.0
        if close <= ma * (1 + entry_buffer):
            return False
        if atr_ratio > atr_limit:
            return False
    return True


def _trend_exit_confirmed(bars, closes, decision_idx: int, cfg, confirm_bars: int) -> bool:
    start = decision_idx - confirm_bars + 1
    if start < 1:
        return False
    exit_buffer = float(cfg["exit_buffer"])
    ma_window = int(cfg["ma_window"])
    for idx in range(start, decision_idx + 1):
        ma = _ma_at(closes, ma_window, idx)
        if bars[idx].close >= ma * (1 - exit_buffer):
            return False
    return True


def get_strategy():
    return DefensiveBarbellV2()
