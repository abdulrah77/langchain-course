from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from dependencies import supabase


class PublicLinkService:
    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def create_link(
        *,
        business_id: str,
        store_id: str | None,
        user_id: str,
        role: str,
        kind: str,
        base_url: str,
    ) -> Dict[str, Any]:
        token = secrets.token_urlsafe(32)
        token_hash = PublicLinkService._hash_token(token)
        owner_like = (role or "").lower() in {"owner", "admin"}
        ttl_hours = 6 if owner_like else 1
        expires_at = PublicLinkService._utc_now() + timedelta(hours=ttl_hours)
        scope = "inventory_business_view" if owner_like else "inventory_store_summary"
        if kind == "dashboard" and not owner_like:
            scope = "dashboard_store_summary"

        payload = {
            "token_hash": token_hash,
            "business_id": business_id,
            "store_id": store_id,
            "scope": scope,
            "expires_at": expires_at.isoformat(),
            "created_by_user_id": user_id,
            "created_via": "whatsapp",
            "max_uses": None,
        }
        supabase.table("public_share_links").insert(payload).execute()
        return {
            "url": f"{base_url}/public/inventory/{token}",
            "expires_in_minutes": ttl_hours * 60,
            "scope": scope,
        }

    @staticmethod
    def get_inventory_snapshot(token: str) -> Dict[str, Any]:
        token_hash = PublicLinkService._hash_token(token)
        link_resp = (
            supabase.table("public_share_links")
            .select("*")
            .eq("token_hash", token_hash)
            .is_("revoked_at", "null")
            .limit(1)
            .execute()
        )
        if not link_resp.data:
            raise ValueError("Invalid link token")
        link = link_resp.data[0]

        expires_at = datetime.fromisoformat(link["expires_at"].replace("Z", "+00:00"))
        if expires_at <= PublicLinkService._utc_now():
            raise ValueError("Link has expired")

        if link.get("max_uses") is not None and int(link.get("use_count", 0)) >= int(link["max_uses"]):
            raise ValueError("Link usage limit reached")

        query = (
            supabase.table("inventory")
            .select("stock_quantity,buy_price,selling_price,products!inner(product_name,sku_code)")
            .eq("business_id", link["business_id"])
            .eq("is_active", True)
        )
        if link.get("store_id"):
            query = query.eq("store_id", link["store_id"])
        inventory = query.execute().data or []

        low_stock = [
            {
                "product_name": row["products"]["product_name"],
                "sku_code": row["products"].get("sku_code"),
                "stock_quantity": row["stock_quantity"],
            }
            for row in inventory
            if (row.get("stock_quantity") or 0) < 10
        ][:20]

        total_stock_units = sum(float(row.get("stock_quantity") or 0) for row in inventory)
        total_retail_value = sum(
            float(row.get("stock_quantity") or 0) * float(row.get("selling_price") or 0)
            for row in inventory
        )

        update_payload = {
            "use_count": int(link.get("use_count", 0)) + 1,
            "last_used_at": PublicLinkService._utc_now().isoformat(),
        }
        supabase.table("public_share_links").update(update_payload).eq(
            "share_link_id", link["share_link_id"]
        ).execute()

        return {
            "scope": link["scope"],
            "expires_at": link["expires_at"],
            "business_id": link["business_id"],
            "store_id": link.get("store_id"),
            "summary": {
                "total_unique_items": len(inventory),
                "total_stock_units": round(total_stock_units, 3),
                "total_retail_value": round(total_retail_value, 2),
                "low_stock_alerts": len(low_stock),
                "low_stock_items": low_stock,
            },
        }
