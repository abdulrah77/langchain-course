from __future__ import annotations

from typing import Any, Dict

from dependencies import supabase


class InvoiceService:
    @staticmethod
    def post_invoice(payload: Dict[str, Any]) -> Dict[str, Any]:
        if not payload.get("items"):
            raise ValueError("Invoice must include at least one item")
        rpc_response = supabase.rpc("post_invoice_transactional", {"p_payload": payload}).execute()
        data = rpc_response.data
        if not data:
            raise ValueError("Transactional invoice post returned empty response")
        return data

    @staticmethod
    def _get_invoice_scoped(invoice_id: str, business_id: str, allowed_stores: list[str]) -> Dict[str, Any]:
        response = (
            supabase.table("invoices")
            .select("invoice_id,business_id,store_id,total_amount,paid_amount,due_amount,payment_status")
            .eq("invoice_id", invoice_id)
            .eq("business_id", business_id)
            .in_("store_id", allowed_stores)
            .limit(1)
            .execute()
        )
        if not response.data:
            raise ValueError("Invoice not found in business scope or no access to store")
        return response.data[0]

    @staticmethod
    def add_payment(
        *,
        invoice_id: str,
        business_id: str,
        allowed_stores: list[str],
        amount: float,
        payment_mode: str,
        payment_date: str | None = None,
        created_by: str | None = None,
    ) -> Dict[str, Any]:
        invoice = InvoiceService._get_invoice_scoped(invoice_id, business_id, allowed_stores)
        payload = {
            "invoice_id": invoice["invoice_id"],
            "business_id": invoice["business_id"],
            "amount": amount,
            "payment_mode": payment_mode,
            "payment_date": payment_date,
            "created_by": created_by,
        }
        rpc_response = supabase.rpc("add_invoice_payment_transactional", {"p_payload": payload}).execute()
        if not rpc_response.data:
            raise ValueError("Payment transaction did not return response")
        return rpc_response.data

    @staticmethod
    def list_payments(invoice_id: str, business_id: str, allowed_stores: list[str]) -> Dict[str, Any]:
        invoice = InvoiceService._get_invoice_scoped(invoice_id, business_id, allowed_stores)
        payments = (
            supabase.table("payments")
            .select("*")
            .eq("invoice_id", invoice["invoice_id"])
            .eq("business_id", invoice["business_id"])
            .eq("store_id", invoice["store_id"])
            .order("payment_date", desc=False)
            .execute()
            .data
        )
        paid_amount = round(sum(float(p["amount"]) for p in payments), 2)
        total_amount = float(invoice["total_amount"])
        due_amount = round(max(total_amount - paid_amount, 0), 2)
        payment_status = "paid" if due_amount == 0 and paid_amount > 0 else ("partial" if paid_amount > 0 else "unpaid")
        return {
            "invoice_id": invoice["invoice_id"],
            "business_id": invoice["business_id"],
            "store_id": invoice["store_id"],
            "total_amount": total_amount,
            "paid_amount": paid_amount,
            "due_amount": due_amount,
            "payment_status": payment_status,
            "payments": payments,
        }
