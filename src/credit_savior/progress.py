from __future__ import annotations

import math


def coverage(records: list[dict], duration: float) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """Union only platform-reported ranges; an ended player supplies no evidence."""
    if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
        raise ValueError('invalid_duration')
    ranges = []
    for record in records:
        start, end = record.get('start'), record.get('end')
        if (not isinstance(start, (int, float)) or not isinstance(end, (int, float))
                or not math.isfinite(start) or not math.isfinite(end)):
            raise ValueError('invalid_viewing_record')
        start, end = max(0, start), min(duration, end)
        if end > start:
            ranges.append((start, end))
    merged = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    gaps, cursor = [], 0.0
    for start, end in merged:
        if start > cursor:
            gaps.append((cursor, start))
        cursor = end
    if cursor < duration:
        gaps.append((cursor, duration))
    return merged, gaps
