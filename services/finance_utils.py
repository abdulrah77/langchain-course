from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable


@dataclass
class InvoiceComputation:
    subtotal: float
    total_amount: float
    paid_amount: float
    due_amount: float
    payment_status: str


def compute_payment(subtotal: float, discount: float, tax: float, paid_amount: float) -> InvoiceComputation:
    total_amount = max(0.0, round(subtotal - discount + tax, 2))
    paid = max(0.0, round(paid_amount, 2))
    due = max(0.0, round(total_amount - paid, 2))
    if paid <= 0:
        status = "unpaid"
    elif due <= 0:
        status = "paid"
        due = 0.0
    else:
        status = "partial"
    return InvoiceComputation(subtotal, total_amount, paid, due, status)


def compute_cash_totals(rows: Iterable[Dict[str, object]]) -> Dict[str, float]:
    cash_in_hand = 0.0
    cash_in_bank = 0.0
    for row in rows:
        amount = float(row["amount"])
        direction = row["direction"]
        entry_type = row["entry_type"]
        sign = 1 if direction == "in" else -1
        if entry_type == "cash_deposit_bank":
            cash_in_hand -= amount
            cash_in_bank += amount
        elif entry_type == "cash_withdraw_bank":
            cash_in_hand += amount
            cash_in_bank -= amount
        elif entry_type == "sale_bank":
            cash_in_bank += sign * amount
        else:
            cash_in_hand += sign * amount
    return {"cash_in_hand": round(cash_in_hand, 2), "cash_in_bank": round(cash_in_bank, 2)}
