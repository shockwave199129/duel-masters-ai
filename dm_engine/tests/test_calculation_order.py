#!/usr/bin/env python3
"""Tests for Rule 108.2 number calculation order."""

import sys
sys.path.insert(0, "dm_engine")

from core.number_calc import CalculationOp, calculate_dm


def check(name: str, condition: bool, detail: str = "") -> bool:
    status = "✅ PASS" if condition else "❌ FAIL"
    print(f"{status}: {name}" + (f" - {detail}" if detail else ""))
    return condition


print("=" * 70)
print("Testing Rule 108.2 Calculation Order")
print("=" * 70)

# (10 + 5 - 3) * 2 / 4 = 6 in DM order, not PEMDAS (which would differ)
result = calculate_dm(10, [
    (5, CalculationOp.ADDITION),
    (3, CalculationOp.SUBTRACTION),
    (2, CalculationOp.MULTIPLICATION),
    (4, CalculationOp.DIVISION),
])
check("DM order: ((10+5)-3)*2/4 = 6", result == 6, f"got {result}")

# All additions before subtractions: 10 + 2 + 3 - 5 = 10
result2 = calculate_dm(10, [
    (2, CalculationOp.ADDITION),
    (5, CalculationOp.SUBTRACTION),
    (3, CalculationOp.ADDITION),
])
check("Adds before subs: 10+2+3-5 = 10", result2 == 10, f"got {result2}")

# Multiplication phase after add/sub: (10 - 2) * 3 = 24
result3 = calculate_dm(10, [
    (2, CalculationOp.SUBTRACTION),
    (3, CalculationOp.MULTIPLICATION),
])
check("Mult after add/sub: (10-2)*3 = 24", result3 == 24, f"got {result3}")

print("\n" + "=" * 70)
