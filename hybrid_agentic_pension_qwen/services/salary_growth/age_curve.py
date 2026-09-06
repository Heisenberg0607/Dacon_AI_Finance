from __future__ import annotations

from bisect import bisect_left
from typing import Any


class AgeGrowthCurve:
    def __init__(self, payload: dict[str, Any]):
        self.payload = payload
        rows = payload.get('curve') or []
        if not rows:
            raise ValueError('age_growth_curve.json has no curve rows')
        self.rows = sorted(rows, key=lambda row: int(row['age']))
        self.ages = [int(row['age']) for row in self.rows]
        self.global_mean = float(payload.get('global_train_mean', 0.0))

    @property
    def min_age(self) -> int:
        return self.ages[0]

    @property
    def max_age(self) -> int:
        return self.ages[-1]

    def growth_for_age(self, age: int | float) -> dict[str, Any]:
        requested_age = int(round(float(age)))
        idx = bisect_left(self.ages, requested_age)
        if idx <= 0:
            row = self.rows[0]
        elif idx >= len(self.ages):
            row = self.rows[-1]
        elif self.ages[idx] == requested_age:
            row = self.rows[idx]
        else:
            before = self.rows[idx - 1]
            after = self.rows[idx]
            row = before if abs(int(before['age']) - requested_age) <= abs(int(after['age']) - requested_age) else after

        return {
            'requested_age': requested_age,
            'matched_age': int(row['age']),
            'growth': float(row.get('smoothed_mean', row.get('mean', self.global_mean))),
            'n': int(row.get('n', 0)),
            'source': 'age_growth_curve_smoothed_mean',
            'clamped': requested_age < self.min_age or requested_age > self.max_age,
        }
