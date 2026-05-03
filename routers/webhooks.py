"""
routers/webhooks.py

Razorpay payment webhook handler.
POST /webhooks/razorpay  — called by Razorpay after successful payment.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Header, HTTPException, Request

from routers.whatsapp import send_whatsapp_message
from services.subscription_service import SubscriptionService

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
logger = logging.getLogger(__name__)


@router.post("/razorpay")
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: str = Header(default=""),
):
    """
    Razorpay sends this after a successful payment.
    Events handled:
      - payment_link.paid   → activate subscription
    """
    raw_body = await request.body()

    # ── 1. Verify signature ───────────────────────────────────────────────────
    if not SubscriptionService.verify_razorpay_webhook(raw_body, x_razorpay_signature):
        logger.warning("razorpay_webhook_bad_signature")
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    # ── 2. Parse event ────────────────────────────────────────────────────────
    try:
        event = json.loads(raw_body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    event_type = event.get("event")
    logger.info("razorpay_webhook_received event=%s", event_type)

    if event_type != "payment_link.paid":
        # Acknowledge but do nothing for other event types
        return {"status": "ignored", "event": event_type}

    # ── 3. Extract payment details ────────────────────────────────────────────
    try:
        payload      = event["payload"]
        payment_link = payload["payment_link"]["entity"]
        payment      = payload["payment"]["entity"]
        notes        = payment_link.get("notes", {})
        store_id     = notes.get("store_id")
        razorpay_payment_id = payment.get("id")
    except (KeyError, TypeError) as exc:
        logger.error("razorpay_webhook_parse_error reason=%s", exc)
        raise HTTPException(status_code=400, detail="Unexpected payload shape")

    if not store_id:
        logger.warning("razorpay_webhook_missing_store_id notes=%s", notes)
        return {"status": "ignored", "reason": "no store_id in notes"}

    # ── 4. Activate the subscription ──────────────────────────────────────────
    try:
        SubscriptionService.activate(
            store_id=store_id,
            razorpay_payment_id=razorpay_payment_id,
        )
        logger.info("subscription_activated_via_webhook store_id=%s", store_id)
    except Exception as exc:
        logger.error("subscription_activate_failed store_id=%s reason=%s", store_id, exc)
        raise HTTPException(status_code=500, detail="Activation failed")

    # ── 5. Notify the store owner on WhatsApp ─────────────────────────────────
    try:
        # Look up the owner's WhatsApp number from whatsapp_user_links
        links_resp = (
            __import__("dependencies", fromlist=["supabase"]).supabase
            .table("whatsapp_user_links")
            .select("sender_phone")
            .eq("store_id", store_id)
            .limit(5)
            .execute()
        )
        import datetime
        period_end = (
            datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(days=30)
        ).strftime("%d %b %Y")

        for row in links_resp.data or []:
            phone = row["sender_phone"]
            await send_whatsapp_message(
                phone,
                (
                    f"✅ Payment received! Your subscription is now active.\n\n"
                    f"📅 Valid until: {period_end}\n"
                    f"💰 Amount: ₹99\n\n"
                    f"Thank you for subscribing! 🙏"
                ),
            )
    except Exception as exc:
        logger.warning("razorpay_webhook_whatsapp_notify_failed reason=%s", exc)
        # Don't fail the webhook response — payment was already activated

    return {"status": "ok"}
