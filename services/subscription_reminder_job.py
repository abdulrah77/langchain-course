"""
services/subscription_reminder_job.py

Background job: run once per day (e.g. via FastAPI startup lifespan or an APScheduler cron).
- Sends 3-day expiry reminder
- Sends 1-day expiry reminder (optional — wired if needed)
- Notifies expired stores and flips their status
"""
from __future__ import annotations

import logging

from routers.whatsapp import send_whatsapp_message
from services.subscription_service import SubscriptionService

logger = logging.getLogger(__name__)


async def _get_store_phones(store_id: str) -> list[str]:
    """Fetch all WhatsApp numbers linked to a store."""
    from dependencies import supabase
    resp = (
        supabase.table("whatsapp_user_links")
        .select("sender_phone")
        .eq("store_id", store_id)
        .limit(10)
        .execute()
    )
    return [r["sender_phone"] for r in (resp.data or [])]


async def run_subscription_reminders() -> None:
    """
    Main daily job entry point.
    Call this from FastAPI lifespan scheduler or an APScheduler cron.
    """
    logger.info("subscription_reminder_job_start")

    # ── 3-day reminder ────────────────────────────────────────────────────────
    stores_3day = SubscriptionService.get_stores_needing_reminder(days_before=3)
    for sub in stores_3day:
        store_id    = sub["store_id"]
        payment_url = sub.get("razorpay_short_url") or await SubscriptionService.get_or_create_payment_link(
            store_id=store_id,
            business_id=sub["business_id"],
            customer_phone="",  # will be fetched per phone below
        )
        phones = await _get_store_phones(store_id)
        for phone in phones:
            try:
                await send_whatsapp_message(
                    phone,
                    (
                        "⏰ *Reminder:* Your WhatsApp Inventory subscription expires in *3 days*.\n\n"
                        f"Subscribe now to keep all features active:\n{payment_url or 'Contact support'}\n\n"
                        "Plan: ₹99/month per store."
                    ),
                )
                logger.info("reminder_3day_sent store_id=%s phone=%s", store_id, phone)
            except Exception as exc:
                logger.warning("reminder_3day_failed store_id=%s phone=%s reason=%s", store_id, phone, exc)

        SubscriptionService.mark_reminder_sent(store_id, days_before=3)

    # ── Expired stores: notify + flip status ──────────────────────────────────
    expired = SubscriptionService.get_newly_expired_stores()
    for sub in expired:
        store_id = sub["store_id"]
        payment_url = sub.get("razorpay_short_url") or await SubscriptionService.get_or_create_payment_link(
            store_id=store_id,
            business_id=sub["business_id"],
            customer_phone="",
        )
        phones = await _get_store_phones(store_id)
        for phone in phones:
            try:
                await send_whatsapp_message(
                    phone,
                    (
                        "⚠️ Your WhatsApp Inventory subscription has *expired*.\n\n"
                        "Your data is safe, but the app is paused until you subscribe.\n\n"
                        f"👉 Subscribe here (₹99/month):\n{payment_url or 'Contact support'}"
                    ),
                )
                logger.info("expiry_notified store_id=%s phone=%s", store_id, phone)
            except Exception as exc:
                logger.warning("expiry_notify_failed store_id=%s phone=%s reason=%s", store_id, phone, exc)

        SubscriptionService.mark_expiry_notified(store_id)

    logger.info(
        "subscription_reminder_job_done 3day=%s expired=%s",
        len(stores_3day),
        len(expired),
    )
