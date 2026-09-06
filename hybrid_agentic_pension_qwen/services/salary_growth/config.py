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
    method: str = 'legacy'
    weights: dict[str, float] | None = None
    validated_horizon_max: int = 0

    def __post_init__(self) -> None:
        if self.method not in {'legacy', 'groups', 'exponential', 'reciprocal'}:
            raise ValueError('Unsupported salary blending method')
        if not math.isfinite(self.decay) or self.decay < 0 or self.block_years != 3:
            raise ValueError('Invalid decay or block size for the 3-year model')
        if self.method == 'groups':
            for key in ('1_3', '4_6', '7_10', '11_plus'):
                w = float((self.weights or {})[key])
                if not math.isfinite(w) or not 0 <= w <= 1:
                    raise ValueError('Blend weights must be between 0 and 1')

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
            method=str(config.get('method', cls.method)),
            weights=config.get('weights'),
            validated_horizon_max=int(config.get('validated_horizon_max', 0)),
        )

    def model_weight(self, years_from_now: int | float, *, first_block: bool = False) -> float:
        if self.method == 'groups':
            h = float(years_from_now) + 1
            key = '1_3' if h <= 3 else '4_6' if h <= 6 else '7_10' if h <= 10 else '11_plus'
            weight = float((self.weights or {})[key])
            if not math.isfinite(weight) or not 0 <= weight <= 1:
                raise ValueError('Blend weights must be finite and between 0 and 1')
            return weight
        if self.method in {'exponential', 'reciprocal'}:
            h = float(years_from_now) + 1
            return math.exp(-self.decay * h) if self.method == 'exponential' else 1 / (1 + self.decay * h)
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
            'method': self.method,
            'weights': self.weights,
            'validated_horizon_max': self.validated_horizon_max,
            'weight_formula': {
                'legacy': 'first block override; else max(min_weight, exp(-decay * years_from_now))',
                'groups': 'group weight at block start h=years_from_now+1, held for the 3-year block',
                'exponential': 'exp(-decay * (years_from_now+1)), held for the 3-year block',
                'reciprocal': '1/(1+decay * (years_from_now+1)), held for the 3-year block',
            }[self.method],
        }
