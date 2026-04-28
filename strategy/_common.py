from __future__ import annotations

from statistics import mean


def atr(bars, window: int) -> list[float]:
    values: list[float] = []
    for index, bar in enumerate(bars):
        if index == 0:
            tr = bar.high - bar.low
        else:
            prev = bars[index - 1].close
            tr = max(bar.high - bar.low, abs(bar.high - prev), abs(bar.low - prev))
        values.append(tr)
    result: list[float] = []
    for index in range(len(values)):
        start = max(0, index - window + 1)
        result.append(mean(values[start : index + 1]))
    return result


def donchian_widths(bars, window: int) -> list[float]:
    widths: list[float] = []
    for index, bar in enumerate(bars):
        start = max(0, index - window + 1)
        rows = bars[start : index + 1]
        high = max(item.high for item in rows)
        low = min(item.low for item in rows)
        widths.append((high - low) / bar.close if bar.close else 0)
    return widths


def percentile_threshold(values: list[float], lookback: int, q: float, index: int) -> float:
    rows = values[max(0, index - lookback + 1) : index + 1]
    if not rows:
        return 0
    rows = sorted(rows)
    pos = min(len(rows) - 1, max(0, int((len(rows) - 1) * q)))
    return rows[pos]


def moving_average(values: list[float], window: int, index: int) -> float:
    rows = values[max(0, index - window + 1) : index + 1]
    return mean(rows) if rows else 0
