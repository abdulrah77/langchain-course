from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from dependencies import supabase


class WhatsAppSessionService:
    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def get_active_link(sender_phone: str) -> Optional[Dict[str, Any]]:
        response = (
            supabase.table("whatsapp_user_links")
            .select("sender_phone,business_id,store_id,is_active,linked_at")
            .eq("sender_phone", sender_phone)
            .eq("is_active", True)
            .order("linked_at", desc=True)
            .limit(1)
            .execute()
        )
        return response.data[0] if response.data else None

    @staticmethod
    def validate_business_store_link(business_id: str, store_id: str) -> bool:
        response = (
            supabase.table("stores")
            .select("store_id")
            .eq("store_id", store_id)
            .eq("business_id", business_id)
            .eq("is_active", True)
            .limit(1)
            .execute()
        )
        return bool(response.data)

    @staticmethod
    def upsert_user_link(sender_phone: str, business_id: str, store_id: str) -> Dict[str, Any]:
        payload = {
            "sender_phone": sender_phone,
            "business_id": business_id,
            "store_id": store_id,
            "is_active": True,
            "linked_at": WhatsAppSessionService._utc_now(),
        }
        response = (
            supabase.table("whatsapp_user_links")
            .upsert(payload, on_conflict="sender_phone,business_id,store_id")
            .execute()
        )
        return response.data[0]

    @staticmethod
    def get_session(sender_phone: str, business_id: str | None = None, store_id: str | None = None) -> Optional[Dict[str, Any]]:
        query = supabase.table("whatsapp_sessions").select("*").eq("sender_phone", sender_phone)
        if business_id is None:
            query = query.is_("business_id", "null")
        else:
            query = query.eq("business_id", business_id)
        if store_id is None:
            query = query.is_("store_id", "null")
        else:
            query = query.eq("store_id", store_id)
        response = query.limit(1).execute()
        return response.data[0] if response.data else None

    @staticmethod
    def upsert_session(
        sender_phone: str,
        *,
        business_id: str | None,
        store_id: str | None,
        feature_mode: str = "inventory",
        state: str | None = None,
        draft_payload: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        payload = {
            "sender_phone": sender_phone,
            "business_id": business_id,
            "store_id": store_id,
            "feature_mode": feature_mode,
            "state": state,
            "draft_payload": draft_payload,
            "last_interaction_at": WhatsAppSessionService._utc_now(),
            "updated_at": WhatsAppSessionService._utc_now(),
        }
        response = (
            supabase.table("whatsapp_sessions")
            .upsert(payload, on_conflict="sender_phone,business_id,store_id")
            .execute()
        )
        return response.data[0]

    @staticmethod
    def set_mode(sender_phone: str, business_id: str, store_id: str, feature_mode: str) -> Dict[str, Any]:
        payload = {
            "feature_mode": feature_mode,
            "last_interaction_at": WhatsAppSessionService._utc_now(),
            "updated_at": WhatsAppSessionService._utc_now(),
        }
        response = (
            supabase.table("whatsapp_sessions")
            .update(payload)
            .eq("sender_phone", sender_phone)
            .eq("business_id", business_id)
            .eq("store_id", store_id)
            .execute()
        )
        if response.data:
            return response.data[0]
        return WhatsAppSessionService.upsert_session(
            sender_phone,
            business_id=business_id,
            store_id=store_id,
            feature_mode=feature_mode,
        )

    @staticmethod
    def get_identity(sender_phone: str, business_id: str) -> Optional[Dict[str, Any]]:
        response = (
            supabase.table("whatsapp_user_identities")
            .select("sender_phone,user_id,business_id,store_id,is_active")
            .eq("sender_phone", sender_phone)
            .eq("business_id", business_id)
            .eq("is_active", True)
            .limit(1)
            .execute()
        )
        return response.data[0] if response.data else None

    @staticmethod
    def get_user_role(user_id: str, business_id: str) -> Optional[str]:
        response = (
            supabase.table("user_businesses")
            .select("role")
            .eq("user_id", user_id)
            .eq("business_id", business_id)
            .limit(1)
            .execute()
        )
        if not response.data:
            return None
        return response.data[0].get("role")

    @staticmethod
    def resolve_identity_context(
        sender_phone: str, business_id: str, fallback_store_id: str | None = None
    ) -> Optional[Dict[str, Any]]:
        identity = WhatsAppSessionService.get_identity(sender_phone, business_id)
        if not identity:
            return None
        role = WhatsAppSessionService.get_user_role(identity["user_id"], business_id)
        if not role:
            return None
        return {
            "user_id": identity["user_id"],
            "business_id": business_id,
            "store_id": identity.get("store_id") or fallback_store_id,
            "role": role,
        }
