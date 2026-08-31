from __future__ import annotations

import json
import re
from typing import Any


def raw_amount(value: Any) -> str:
    """Format an internal monetary numeric value without converting its scale or appending a unit."""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return '-'
    if n.is_integer():
        return f"{int(n):,}"
    return f"{n:,.2f}".rstrip('0').rstrip('.')


def won_amount(value: Any) -> str:
    """Convert an internal 만원-scale monetary value to the actual KRW numeric amount.

    Example: 5000 -> "50,000,000"
    """
    try:
        n = float(value)
    except (TypeError, ValueError):
        return '-'
    return f"{round(n * 10000):,}"


def contains_converted_money_unit(value: Any) -> bool:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    return bool(re.search(r'\d[\d,.]*\s*(?:억\s*원|억원|만\s*원|만원)', text))
