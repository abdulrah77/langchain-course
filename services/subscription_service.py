"""
services/subscription_service.py

Per-store subscription management with Razorpay payment links.
- 7-day free trial from store creation
- ₹99/month per store
- Razorpay Payment Links API for billing
- 3-day and 1-day expiry reminders
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx

from dependencies import supabase

logger = logging.getLogger(__name__)

SUBSCRIPTION_AMOUNT_PAISE = int(os.getenv("SUBSCRIPTION_AMOUNT_PAISE", "9900"))  # ₹99
SUBSCRIPTION_PERIOD_DAYS  = int(os.getenv("SUBSCRIPTION_PERIOD_DAYS", "30"))
RAZORPAY_API_BASE         = "https://api.razorpay.com/v1"


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _razorpay_auth() -> tuple[str, str]:
    key    = os.getenv("RAZORPAY_KEY_ID", "rzp_test_DUMMY_KEY")
    secret = os.getenv("RAZORPAY_KEY_SECRET", "DUMMY_SECRET")
    return key, secret


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

class SubscriptionService:

    # ── Read ──────────────────────────────────────────────────────────────────

    @staticmethod
    def get_subscription(store_id: str) -> Optional[Dict[str, Any]]:
        """Fetch the store_subscriptions row for a store, or None."""
        resp = (
            supabase.table("store_subscriptions")
            .select("*")
            .eq("store_id", store_id)
            .limit(1)
            .execute()
        )
        return resp.data[0] if resp.data else None

    @staticmethod
    def is_access_allowed(store_id: str) -> bool:
        """
        Return True if the store can use the app.
        Access is granted when:
          - status = 'trial'  AND trial_end has not passed
          - status = 'active' AND current_period_end has not passed
        """
        sub = SubscriptionService.get_subscription(store_id)
        if not sub:
            # No subscription row → treat as not allowed (admin must create it)
            return False
        now = _now_utc()
        status = sub["status"]
        if status == "trial":
            trial_end = datetime.fromisoformat(sub["trial_end"])
            return now <= trial_end
        if status == "active":
            period_end = datetime.fromisoformat(sub["current_period_end"])
            return now <= period_end
        return False

    @staticmethod
    def days_until_expiry(store_id: str) -> Optional[int]:
        """Return days remaining in trial or active period. None if no subscription."""
        sub = SubscriptionService.get_subscription(store_id)
        if not sub:
            return None
        now = _now_utc()
        if sub["status"] == "trial":
            end = datetime.fromisoformat(sub["trial_end"])
        elif sub["status"] == "active":
            end = datetime.fromisoformat(sub["current_period_end"])
        else:
            return 0
        delta = (end - now).days
        return max(0, delta)

    # ── Create / seed ─────────────────────────────────────────────────────────

    @staticmethod
    def seed_trial(store_id: str, business_id: str) -> Dict[str, Any]:
        """
        Create a trial subscription row for a new store.
        Call this whenever a new store is created.
        """
        existing = SubscriptionService.get_subscription(store_id)
        if existing:
            return existing
        resp = (
            supabase.table("store_subscriptions")
            .insert({
                "store_id": store_id,
                "business_id": business_id,
                "status": "trial",
                "plan": "basic",
            })
            .execute()
        )
        logger.info("subscription_trial_seeded store_id=%s", store_id)
        return resp.data[0]

    # ── Activate on payment ───────────────────────────────────────────────────

    @staticmethod
    def activate(
        store_id: str,
        razorpay_payment_id: str,
        period_days: int = SUBSCRIPTION_PERIOD_DAYS,
    ) -> Dict[str, Any]:
        """Mark a store subscription as active after successful payment."""
        now = _now_utc()
        period_end = now.replace(tzinfo=None)  # store as naive UTC for Supabase
        from datetime import timedelta
        period_end_dt = now + timedelta(days=period_days)

        resp = (
            supabase.table("store_subscriptions")
            .update({
                "status": "active",
                "razorpay_payment_id": razorpay_payment_id,
                "current_period_start": now.isoformat(),
                "current_period_end": period_end_dt.isoformat(),
                # Clear reminder flags so they fire again next cycle
                "reminder_3day_sent_at": None,
                "reminder_1day_sent_at": None,
                "expiry_notified_at": None,
            })
            .eq("store_id", store_id)
            .execute()
        )
        logger.info("subscription_activated store_id=%s payment_id=%s", store_id, razorpay_payment_id)
        return resp.data[0] if resp.data else {}

    @staticmethod
    def mark_expired(store_id: str) -> None:
        """Flip status to 'expired'. Called by cron or lazily on access check."""
        supabase.table("store_subscriptions").update({"status": "expired"}).eq("store_id", store_id).execute()
        logger.info("subscription_expired store_id=%s", store_id)

    # ── Razorpay Payment Link ─────────────────────────────────────────────────

    @staticmethod
    async def create_razorpay_payment_link(
        store_id: str,
        business_id: str,
        customer_phone: str,
        store_name: str = "Your Store",
    ) -> Optional[str]:
        """
        Create a Razorpay Payment Link for ₹99 and store the link ID.
        Returns the short URL on success, None on failure.
        """
        key, secret = _razorpay_auth()
        api_base = os.getenv("APP_BASE_URL", "https://your-api.onrender.com")
        payload = {
            "amount": SUBSCRIPTION_AMOUNT_PAISE,
            "currency": "INR",
            "description": f"WhatsApp Inventory — {store_name} Monthly Subscription",
            "customer": {"contact": customer_phone},
            "notify": {"sms": True, "email": False},
            "reminder_enable": True,
            "callback_url": f"{api_base}/webhooks/razorpay",
            "callback_method": "get",
            "notes": {
                "store_id": store_id,
                "business_id": business_id,
            },
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{RAZORPAY_API_BASE}/payment_links",
                    auth=(key, secret),
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                link_id   = data["id"]
                short_url = data["short_url"]
                # Persist on the subscription row
                supabase.table("store_subscriptions").update({
                    "razorpay_payment_link_id": link_id,
                    "razorpay_short_url": short_url,
                }).eq("store_id", store_id).execute()
                logger.info("razorpay_link_created store_id=%s link_id=%s", store_id, link_id)
                return short_url
        except Exception as exc:
            logger.error("razorpay_link_failed store_id=%s reason=%s", store_id, exc)
            return None

    @staticmethod
    async def get_or_create_payment_link(
        store_id: str,
        business_id: str,
        customer_phone: str,
        store_name: str = "Your Store",
    ) -> Optional[str]:
        """
        Return an existing payment link URL if already created,
        otherwise create a fresh one.
        """
        sub = SubscriptionService.get_subscription(store_id)
        if sub and sub.get("razorpay_short_url"):
            return sub["razorpay_short_url"]
        return await SubscriptionService.create_razorpay_payment_link(
            store_id=store_id,
            business_id=business_id,
            customer_phone=customer_phone,
            store_name=store_name,
        )

    # ── Webhook signature verification ────────────────────────────────────────

    @staticmethod
    def verify_razorpay_webhook(raw_body: bytes, signature: str) -> bool:
        """
        Verify Razorpay webhook HMAC-SHA256 signature.
        Returns True if signature is valid.
        """
        secret = os.getenv("RAZORPAY_WEBHOOK_SECRET", "DUMMY_WEBHOOK_SECRET")
        expected = hmac.new(
            secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    # ── Reminder tracking ─────────────────────────────────────────────────────

    @staticmethod
    def get_stores_needing_reminder(days_before: int) -> list[Dict[str, Any]]:
        """
        Return stores whose trial/active period expires in exactly `days_before` days
        and where the reminder has not yet been sent.
        Uses Postgres date arithmetic for accuracy.
        """
        from datetime import timedelta
        now = _now_utc()
        # Window: between N days and N-1 days from now
        window_start = (now + timedelta(days=days_before - 1)).date().isoformat()
        window_end   = (now + timedelta(days=days_before)).date().isoformat()
        col_flag = f"reminder_{days_before}day_sent_at"

        # Trial stores
        trial_resp = (
            supabase.table("store_subscriptions")
            .select("subscription_id,store_id,business_id,razorpay_short_url,trial_end")
            .eq("status", "trial")
            .gte("trial_end", window_start)
            .lt("trial_end", window_end)
            .is_(col_flag, "null")
            .execute()
        )
        # Active stores near renewal
        active_resp = (
            supabase.table("store_subscriptions")
            .select("subscription_id,store_id,business_id,razorpay_short_url,current_period_end")
            .eq("status", "active")
            .gte("current_period_end", window_start)
            .lt("current_period_end", window_end)
            .is_(col_flag, "null")
            .execute()
        )
        return (trial_resp.data or []) + (active_resp.data or [])

    @staticmethod
    def mark_reminder_sent(store_id: str, days_before: int) -> None:
        col = f"reminder_{days_before}day_sent_at"
        supabase.table("store_subscriptions").update(
            {col: _now_utc().isoformat()}
        ).eq("store_id", store_id).execute()

    @staticmethod
    def get_newly_expired_stores() -> list[Dict[str, Any]]:
        """Return stores that have just expired and haven't been notified yet."""
        now = _now_utc().isoformat()
        trial_resp = (
            supabase.table("store_subscriptions")
            .select("subscription_id,store_id,business_id,razorpay_short_url")
            .eq("status", "trial")
            .lt("trial_end", now)
            .is_("expiry_notified_at", "null")
            .execute()
        )
        active_resp = (
            supabase.table("store_subscriptions")
            .select("subscription_id,store_id,business_id,razorpay_short_url")
            .eq("status", "active")
            .lt("current_period_end", now)
            .is_("expiry_notified_at", "null")
            .execute()
        )
        return (trial_resp.data or []) + (active_resp.data or [])

    @staticmethod
    def mark_expiry_notified(store_id: str) -> None:
        supabase.table("store_subscriptions").update({
            "status": "expired",
            "expiry_notified_at": _now_utc().isoformat(),
        }).eq("store_id", store_id).execute()
