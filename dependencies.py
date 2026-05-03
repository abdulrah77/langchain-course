"""
dependencies.py  — upgraded for production SaaS
- JWT validation (Supabase Auth)
- Business + store context resolution
- Subscription enforcement (REST API gate)
- Permission matrix (can_manage_inventory etc.)
- Role-based access control (RBAC)
- Request tracing via X-Request-ID header
"""
import os
import uuid
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from supabase import Client, create_client

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# ── Supabase client ───────────────────────────────────────────────────────────
url = os.getenv("NEXT_PUBLIC_SUPABASE_URL")
key = os.getenv("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY")
if not url or not key:
    raise ValueError("Missing SUPABASE_URL or SUPABASE_KEY in environment variables.")

supabase: Client = create_client(url, key)
security = HTTPBearer()

# ── Read-only route paths (subscription not enforced) ─────────────────────────
_READ_ONLY_PREFIXES = ("/public/", "/health", "/onboarding", "/webhooks")


# ── Request ID ────────────────────────────────────────────────────────────────
async def get_request_id(x_request_id: Optional[str] = Header(default=None)) -> str:
    """Return client-supplied request ID or generate one."""
    return x_request_id or str(uuid.uuid4())


# ── JWT → User ────────────────────────────────────────────────────────────────
async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Validates the JWT token against Supabase Auth and returns the user object."""
    token = credentials.credentials
    try:
        auth_response = supabase.auth.get_user(token)
        user = auth_response.user
        if not user:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        return user
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Authentication failed: {str(e)}")


# ── User context (business + store + role + permissions) ─────────────────────
async def get_user_context(
    request: Request,
    user=Depends(get_current_user),
):
    """
    Resolves:
      - business_id, role
      - allowed_stores
      - permission matrix per store
      - subscription gate (blocks mutation APIs on expired stores)
    """
    try:
        # 1. Business + role
        biz_response = (
            supabase.table("user_businesses")
            .select("business_id, role")
            .eq("user_id", user.id)
            .execute()
        )
        biz_data = biz_response.data
        if not biz_data:
            raise HTTPException(status_code=403, detail="User does not belong to any business")

        business_id = biz_data[0]["business_id"]
        role = biz_data[0]["role"]

        # 2. Store access + permissions
        store_response = (
            supabase.table("user_store_access")
            .select(
                "store_id, role, "
                "can_manage_inventory, can_manage_ledger, "
                "can_view_reports, can_post_invoices, can_manage_purchases"
            )
            .eq("user_id", user.id)
            .execute()
        )
        store_rows = store_response.data or []
        allowed_stores = [r["store_id"] for r in store_rows]

        # Permission map: store_id → dict of booleans
        permissions: dict[str, dict] = {
            r["store_id"]: {
                "can_manage_inventory": r.get("can_manage_inventory", True),
                "can_manage_ledger": r.get("can_manage_ledger", False),
                "can_view_reports": r.get("can_view_reports", True),
                "can_post_invoices": r.get("can_post_invoices", True),
                "can_manage_purchases": r.get("can_manage_purchases", False),
            }
            for r in store_rows
        }

        # 3. Subscription gate (skip on read-only / public / webhook paths)
        path = request.url.path
        is_read_only_path = any(path.startswith(p) for p in _READ_ONLY_PREFIXES)
        is_safe_method = request.method in {"GET", "HEAD", "OPTIONS"}

        if not is_read_only_path and allowed_stores:
            # Lazy import avoids circular dependency
            from services.subscription_service import SubscriptionService
            # Check the first store in context (main store). Fine for single-store users.
            # Multi-store: the specific route should call verify_store_subscription().
            primary_store = allowed_stores[0]
            if not SubscriptionService.is_access_allowed(primary_store) and not is_safe_method:
                sub = SubscriptionService.get_subscription(primary_store)
                payment_url = sub.get("razorpay_short_url") if sub else None
                raise HTTPException(
                    status_code=402,
                    detail={
                        "error": "subscription_expired",
                        "message": "Your subscription has expired. Please renew to continue.",
                        "payment_url": payment_url,
                    },
                )

        return {
            "user_id": user.id,
            "business_id": business_id,
            "role": role,
            "allowed_stores": allowed_stores,
            "permissions": permissions,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Store-level subscription check (explicit, per-route) ─────────────────────
def verify_store_subscription(store_id: str) -> None:
    """Call this in any route that uses a specific store_id to block expired stores."""
    from services.subscription_service import SubscriptionService
    if not SubscriptionService.is_access_allowed(store_id):
        sub = SubscriptionService.get_subscription(store_id)
        payment_url = sub.get("razorpay_short_url") if sub else None
        raise HTTPException(
            status_code=402,
            detail={
                "error": "subscription_expired",
                "message": "Store subscription has expired.",
                "payment_url": payment_url,
            },
        )


# ── Convenience dependencies ──────────────────────────────────────────────────
async def get_current_business_id(context: dict = Depends(get_user_context)) -> str:
    return context["business_id"]


async def verify_store_access(store_id: str, context: dict = Depends(get_user_context)) -> str:
    """Validates user has access to the given store_id."""
    if store_id not in context.get("allowed_stores", []):
        raise HTTPException(status_code=403, detail="Access denied for this store")
    return store_id


def require_role(allowed_roles: list[str]):
    """Dependency factory: blocks request unless user has one of the given roles."""
    def role_checker(context: dict = Depends(get_user_context)):
        if context["role"] not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail=f"Action requires one of these roles: {allowed_roles}",
            )
        return context["business_id"]
    return role_checker


def require_permission(permission: str):
    """
    Dependency factory: blocks request unless user has a specific permission
    on at least one of their stores.
    permission: 'can_manage_inventory' | 'can_manage_ledger' | 'can_view_reports' |
                'can_post_invoices' | 'can_manage_purchases'
    """
    def perm_checker(context: dict = Depends(get_user_context)):
        perms = context.get("permissions", {})
        has_perm = any(
            store_perms.get(permission, False)
            for store_perms in perms.values()
        )
        if not has_perm:
            raise HTTPException(
                status_code=403,
                detail=f"Missing permission: {permission}",
            )
        return context
    return perm_checker