from __future__ import annotations

from typing import Any, Dict

from dependencies import supabase


class ReturnService:
    @staticmethod
    def post_return(payload: Dict[str, Any]) -> Dict[str, Any]:
        if not payload.get("items"):
            raise ValueError("Sales return must include at least one item")
        rpc_response = supabase.rpc("post_sales_return_transactional", {"p_payload": payload}).execute()
        data = rpc_response.data
        if not data:
            raise ValueError("Transactional sales return post returned empty response")
        return data

    @staticmethod
    def _get_return_scoped(return_id: str, business_id: str, allowed_stores: list[str]) -> Dict[str, Any]:
        response = (
            supabase.table("sales_returns")
            .select("*, sales_return_items(*)")
            .eq("return_id", return_id)
            .eq("business_id", business_id)
            .in_("store_id", allowed_stores)
            .limit(1)
            .execute()
        )
        if not response.data:
            raise ValueError("Sales return not found in business scope or no access to store")
        return response.data[0]

    @staticmethod
    def list_returns(business_id: str, allowed_stores: list[str]) -> list[Dict[str, Any]]:
        response = (
            supabase.table("sales_returns")
            .select("*, sales_return_items(*)")
            .eq("business_id", business_id)
            .in_("store_id", allowed_stores)
            .order("created_at", desc=True)
            .execute()
        )
        return response.data
