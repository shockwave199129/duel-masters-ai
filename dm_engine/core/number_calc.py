"""
core/number_calc.py — Rule 108.2 number calculation order.

When calculations involving numbers are performed in a Duel Masters game,
they are processed in the following order: addition, subtraction,
multiplication, and division.
"""

from __future__ import annotations

from enum import Enum, auto


class CalculationOp(Enum):
    """Rule 108.2 calculation operations, processed in declaration order."""
    ADDITION       = auto()
    SUBTRACTION    = auto()
    MULTIPLICATION = auto()
    DIVISION       = auto()


def calculate_dm(base: int, modifiers: list[tuple[int, CalculationOp]]) -> int:
    """
    Rule 108.2: apply all additions, then subtractions, then
    multiplications, then divisions (integer division, round down).
    """
    value = base
    for amount, op in modifiers:
        if op == CalculationOp.ADDITION:
            value += amount

    for amount, op in modifiers:
        if op == CalculationOp.SUBTRACTION:
            value -= amount

    for amount, op in modifiers:
        if op == CalculationOp.MULTIPLICATION:
            value *= amount

    for amount, op in modifiers:
        if op == CalculationOp.DIVISION:
            if amount != 0:
                value = int(value / amount)

    return value
