from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict

from dependencies import supabase
from services.finance_utils import compute_cash_totals


ENTRY_DIRECTION = {
    "sale_collection": "in",
    "sale_cash": "in",
    "sale_bank": "in",
    "expense": "out",
    "cash_draw": "out",
    "cash_deposit_bank": "out",
    "cash_withdraw_bank": "in",
    "opening_balance": "in",
    "closing_adjustment": "in",
}


class CashLedgerService:
    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def add_entry(payload: Dict[str, Any]) -> Dict[str, Any]:
        entry_type = payload["entry_type"]
        if entry_type not in ENTRY_DIRECTION:
            raise ValueError("Invalid entry_type")
        amount = float(payload["amount"])
        if amount <= 0:
            raise ValueError("amount must be positive")

        row = {
            "business_id": payload["business_id"],
            "store_id": payload["store_id"],
            "entry_type": entry_type,
            "amount": amount,
            "direction": ENTRY_DIRECTION[entry_type],
            "actor_user_id": payload.get("actor_user_id"),
            "actor_phone": payload.get("actor_phone"),
            "note": payload.get("note"),
            "reference_type": payload.get("reference_type"),
            "reference_id": payload.get("reference_id"),
            "occurred_at": payload.get("occurred_at", CashLedgerService._utc_now()),
        }
        return supabase.table("cash_ledger").insert(row).execute().data[0]

    @staticmethod
    def record_invoice_collection(
        business_id: str,
        store_id: str,
        invoice_id: str,
        collected_amount: float,
        actor_phone: str | None = None,
    ) -> Dict[str, Any] | None:
        if collected_amount <= 0:
            return None
        return CashLedgerService.add_entry(
            {
                "business_id": business_id,
                "store_id": store_id,
                "entry_type": "sale_cash",
                "amount": collected_amount,
                "actor_phone": actor_phone,
                "reference_type": "invoice",
                "reference_id": invoice_id,
                "note": "Cash collected from invoice",
            }
        )

    @staticmethod
    def get_current_totals(business_id: str, store_id: str) -> Dict[str, float]:
        response = (
            supabase.table("cash_ledger")
            .select("entry_type,direction,amount")
            .eq("business_id", business_id)
            .eq("store_id", store_id)
            .execute()
        )

        return compute_cash_totals(response.data)

    @staticmethod
    def daily_close(
        business_id: str,
        store_id: str,
        as_of_date: date,
        closing_by_user_id: str | None = None,
    ) -> Dict[str, Any]:
        totals = CashLedgerService.get_current_totals(business_id, store_id)
        payload = {
            "business_id": business_id,
            "store_id": store_id,
            "as_of_date": as_of_date.isoformat(),
            "cash_in_hand": totals["cash_in_hand"],
            "cash_in_bank": totals["cash_in_bank"],
            "closing_by_user_id": closing_by_user_id,
            "closed_at": CashLedgerService._utc_now(),
        }
        response = (
            supabase.table("cash_balances")
            .upsert(payload, on_conflict="business_id,store_id,as_of_date")
            .execute()
        )
        return response.data[0]
