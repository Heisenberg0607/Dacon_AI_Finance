from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SalaryProjectionConfig:
    decay: float = 0.08
    min_weight: float = 0.35
    first_block_weight: float = 1.0
    block_years: int = 3
    blending_validated: bool = False
    provisional: bool = True

    @classmethod
    def from_metadata(cls, metadata: dict[str, Any]) -> 'SalaryProjectionConfig':
        config = metadata.get('projection', {}).get('blending', {})
        return cls(
            decay=float(config.get('decay', cls.decay)),
            min_weight=float(config.get('min_weight', cls.min_weight)),
            first_block_weight=float(config.get('first_block_weight', cls.first_block_weight)),
            block_years=int(config.get('block_years', cls.block_years)),
            blending_validated=bool(config.get('blending_validated', cls.blending_validated)),
            provisional=bool(config.get('provisional', cls.provisional)),
        )

    def model_weight(self, years_from_now: int | float, *, first_block: bool = False) -> float:
        if first_block:
            return self.first_block_weight
        return max(self.min_weight, math.exp(-self.decay * float(years_from_now)))

    def as_dict(self) -> dict[str, Any]:
        return {
            'decay': self.decay,
            'min_weight': self.min_weight,
            'first_block_weight': self.first_block_weight,
            'block_years': self.block_years,
            'blending_validated': self.blending_validated,
            'provisional': self.provisional,
            'weight_formula': 'first block override; else max(min_weight, exp(-decay * years_from_now))',
        }
