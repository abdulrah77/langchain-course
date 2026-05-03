# routers/whatsapp.py
import logging
import os
from datetime import date
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from dependencies import supabase
from services.ai_service import AIService, InvoiceExtraction, LedgerExtraction
from services.cash_ledger_service import CashLedgerService
from services.inventory_service import InventoryService
from services.invoice_pdf_service import generate_invoice_pdf
from services.invoice_service import InvoiceService
from services.public_link_service import PublicLinkService
from services.whatsapp_parsing import (
    can_execute_ledger,
    extract_onboarding_ids,
    is_exit_mode_command,
    is_mode_timed_out,
    parse_ledger_command,
    parse_ledger_mode_shorthand,
    parse_mode_command,
    parse_public_link_command,
)
from services.whatsapp_session_service import WhatsAppSessionService
from services.subscription_service import SubscriptionService

router = APIRouter(prefix="/api/whatsapp", tags=["WhatsApp"])
logger = logging.getLogger("ice_breaker.whatsapp")

# This is a password you make up. Meta will use it to verify your server.
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN")

@router.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge")
):
    """
    Phase 1: Meta's Security Check.
    When you paste your ngrok URL into the Meta Dashboard, Meta will hit this route.
    """
    if hub_mode == "subscribe" and hub_verify_token == WHATSAPP_VERIFY_TOKEN:
        logger.info("whatsapp_webhook_verified")
        # Meta requires the challenge to be returned as plain text
        return PlainTextResponse(content=hub_challenge)
    
    raise HTTPException(status_code=403, detail="Invalid verification token")

async def send_whatsapp_message(to_phone: str, text: str):
    """Helper function to send a reply via WhatsApp"""
    token = os.getenv("WHATSAPP_ACCESS_TOKEN")
    phone_id = os.getenv("WHATSAPP_PHONE_ID")
    url = f"https://graph.facebook.com/v18.0/{phone_id}/messages"
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    data = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "text",
        "text": {"body": text}
    }
    
    logger.info("whatsapp_send_attempt to=%s", to_phone)
    
    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=data)
        
        if response.status_code != 200:
            logger.warning("whatsapp_send_failed status=%s body=%s", response.status_code, response.text)
        else:
            logger.info("whatsapp_send_ok status=%s", response.status_code)


async def send_whatsapp_document(
    to_phone: str,
    pdf_bytes: bytes,
    filename: str,
    caption: str = "",
):
    """Upload a PDF to WhatsApp media and send it as a document message."""
    token = os.getenv("WHATSAPP_ACCESS_TOKEN")
    phone_id = os.getenv("WHATSAPP_PHONE_ID")
    auth_headers = {"Authorization": f"Bearer {token}"}

    # Step 1: Upload the file to WhatsApp
    upload_url = f"https://graph.facebook.com/v18.0/{phone_id}/media"
    async with httpx.AsyncClient(timeout=30) as client:
        upload_resp = await client.post(
            upload_url,
            headers=auth_headers,
            data={"messaging_product": "whatsapp"},
            files={"file": (filename, pdf_bytes, "application/pdf")},
        )
        if upload_resp.status_code != 200:
            logger.warning("whatsapp_upload_failed status=%s body=%s", upload_resp.status_code, upload_resp.text)
            return
        media_id = upload_resp.json().get("id")
        if not media_id:
            logger.warning("whatsapp_upload_no_media_id body=%s", upload_resp.text)
            return

        # Step 2: Send as document
        msg_url = f"https://graph.facebook.com/v18.0/{phone_id}/messages"
        send_resp = await client.post(
            msg_url,
            headers={**auth_headers, "Content-Type": "application/json"},
            json={
                "messaging_product": "whatsapp",
                "to": to_phone,
                "type": "document",
                "document": {
                    "id": media_id,
                    "filename": filename,
                    "caption": caption,
                },
            },
        )
        if send_resp.status_code != 200:
            logger.warning("whatsapp_doc_send_failed status=%s body=%s", send_resp.status_code, send_resp.text)
        else:
            logger.info("whatsapp_doc_send_ok media_id=%s", media_id)
# Temporary in-memory memory for user sessions.
pending_sessions: Dict[str, Dict[str, Any]] = {}


def _build_confirmation_message(extracted_data: Dict[str, Any], is_existing: bool) -> str:
    action_text = "Updating existing data" if is_existing else "Adding new data"
    brand = extracted_data.get("brand")
    variants = extracted_data.get("variants", [])
    product_name = extracted_data.get("product_name", "Unknown product")
    total_quantity = extracted_data.get("total_quantity", 0)
    base_price = extracted_data.get("base_price", 0)

    brand_text = f"Brand: {brand}\n" if brand else ""
    variant_text = ""
    if variants:
        lines = []
        for variant in variants:
            variant_price = (
                variant.get("price")
                if variant.get("price") is not None
                else base_price
            )
            lines.append(
                f"- {variant.get('quantity', 0)}x {variant.get('description', '').title()} (${variant_price})"
            )
        variant_text = "\nVariants:\n" + "\n".join(lines) + "\n"

    return (
        f"{action_text}\n\n"
        f"Product: {product_name}\n"
        f"{brand_text}"
        f"Total Quantity: {total_quantity}\n"
        f"Base Price: ${base_price}\n"
        f"{variant_text}\n"
        "Reply YES to save, or type corrections."
    )


def _set_pending_session(sender_phone: str, mode: str, data: Dict[str, Any]) -> None:
    pending_sessions[sender_phone] = {"mode": mode, "data": data}


def _clear_pending_session(sender_phone: str) -> None:
    if sender_phone in pending_sessions:
        del pending_sessions[sender_phone]


async def _resolve_business_id(sender_phone: str) -> str | None:
    link = WhatsAppSessionService.get_active_link(sender_phone)
    if not link:
        return None
    return link["business_id"]


def _format_currency(value: float) -> str:
    return f"{value:.2f}"


async def _handle_ledger_command(
    sender_phone: str,
    link: Dict[str, Any],
    identity_context: Dict[str, Any],
    command: Dict[str, Any],
):
    role = identity_context["role"]
    store_id = identity_context.get("store_id") or link["store_id"]
    if not store_id:
        await send_whatsapp_message(sender_phone, "No store is linked to your identity.")
        return

    action = command["action"]
    if action == "entry":
        if not can_execute_ledger(role, action, command.get("entry_type")):
            await send_whatsapp_message(sender_phone, "You are not allowed to post this ledger entry.")
            return
        CashLedgerService.add_entry(
            {
                "business_id": link["business_id"],
                "store_id": store_id,
                "entry_type": command["entry_type"],
                "amount": command["amount"],
                "note": command.get("note"),
                "actor_user_id": identity_context["user_id"],
                "actor_phone": sender_phone,
            }
        )
        totals = CashLedgerService.get_current_totals(link["business_id"], store_id)
        entry_label = command["entry_type"].replace("_", " ")
        note_suffix = f" ({command['note']})" if command.get("note") else ""
        await send_whatsapp_message(
            sender_phone,
            (
                f"Logged {entry_label} {_format_currency(command['amount'])}{note_suffix}. "
                f"New cash_in_hand: {_format_currency(totals['cash_in_hand'])}"
            ),
        )
        return

    if action == "totals":
        if not can_execute_ledger(role, action):
            await send_whatsapp_message(sender_phone, "You are not allowed to view ledger totals.")
            return
        totals = CashLedgerService.get_current_totals(link["business_id"], store_id)
        await send_whatsapp_message(
            sender_phone,
            (
                f"Cash in hand: {_format_currency(totals['cash_in_hand'])}, "
                f"Cash in bank: {_format_currency(totals['cash_in_bank'])}"
            ),
        )
        return

    if action == "close":
        if not can_execute_ledger(role, action):
            await send_whatsapp_message(sender_phone, "Daily close is owner/admin only.")
            return
        close_date: date = command["as_of_date"]
        close = CashLedgerService.daily_close(
            business_id=link["business_id"],
            store_id=store_id,
            as_of_date=close_date,
            closing_by_user_id=identity_context["user_id"],
        )
        await send_whatsapp_message(
            sender_phone,
            (
                f"Daily close done for {close['as_of_date']}. "
                f"cash_in_hand={_format_currency(close['cash_in_hand'])}, "
                f"cash_in_bank={_format_currency(close['cash_in_bank'])}"
            ),
        )
        return


async def _handle_public_link_command(
    sender_phone: str,
    link: Dict[str, Any],
    identity_context: Dict[str, Any],
    link_command: Dict[str, str],
):
    base_url = os.getenv("PUBLIC_WEB_BASE_URL", "http://127.0.0.1:5173")
    link_payload = PublicLinkService.create_link(
        business_id=link["business_id"],
        store_id=identity_context.get("store_id") or link["store_id"],
        user_id=identity_context["user_id"],
        role=identity_context["role"],
        kind=link_command["kind"],
        base_url=base_url,
    )
    await send_whatsapp_message(
        sender_phone,
        f"Open inventory (expires in {link_payload['expires_in_minutes']} min): {link_payload['url']}",
    )


def _build_invoice_summary_message(draft: Dict[str, Any], label: str = "Final Bill") -> str:
    """Format a readable invoice summary for WhatsApp confirmation."""
    lines = [f"📋 *{label}*\n"]
    subtotal = 0.0
    for item in draft.get("items", []):
        qty = item.get("qty", 0)
        unit = item.get("unit_price") or 0
        line_total = qty * unit
        subtotal += line_total
        lines.append(f"  • {item.get('product_name', 'Item')} x{qty} @ {unit:.2f} = {line_total:.2f}")

    discount = draft.get("discount", 0) or 0
    tax = draft.get("tax", 0) or 0
    total = subtotal - discount + tax
    paid = draft.get("paid_amount", 0) or 0
    due = max(0, total - paid)

    lines.append(f"\nSubtotal:  {subtotal:.2f}")
    if discount > 0:
        lines.append(f"Discount:  -{discount:.2f}")
    if tax > 0:
        lines.append(f"Tax:       +{tax:.2f}")
    lines.append(f"*Total:    {total:.2f}*")
    if draft.get("customer_name"):
        lines.append(f"Customer:  {draft['customer_name']}")
    lines.append(f"Paid:      {paid:.2f}")
    if due > 0:
        lines.append(f"Due:       {due:.2f}")
    lines.append("\nReply *YES* to confirm and post, or send corrections.")
    return "\n".join(lines)


async def _route_invoice_text(
    sender_phone: str,
    link: Dict[str, Any],
    user_text: str,
):
    session = WhatsAppSessionService.get_session(
        sender_phone, link["business_id"], link["store_id"]
    )
    draft = session.get("draft_payload") if session else None
    state = session.get("state") if session else None
    text_lower = user_text.strip().lower()

    # ── State: awaiting discount response ─────────────────────────────────────
    if state == "awaiting_discount_confirmation" and draft:
        if text_lower in {"no", "n", "none", "nope", "0"}:
            # No discount — move straight to final confirmation
            WhatsAppSessionService.upsert_session(
                sender_phone,
                business_id=link["business_id"],
                store_id=link["store_id"],
                feature_mode="invoice",
                state="awaiting_invoice_confirmation",
                draft_payload=draft,
            )
            await send_whatsapp_message(
                sender_phone,
                _build_invoice_summary_message(draft),
            )
            return

        # Try to parse "yes 100" or just "100"
        parts = user_text.strip().split()
        amount_token = parts[-1] if parts else ""
        try:
            discount_amount = float(amount_token)
        except ValueError:
            await send_whatsapp_message(
                sender_phone,
                "Please reply with the discount amount (e.g. *yes 50*) or *no* for no discount.",
            )
            return

        draft["discount"] = discount_amount
        WhatsAppSessionService.upsert_session(
            sender_phone,
            business_id=link["business_id"],
            store_id=link["store_id"],
            feature_mode="invoice",
            state="awaiting_invoice_confirmation",
            draft_payload=draft,
        )
        await send_whatsapp_message(
            sender_phone,
            _build_invoice_summary_message(draft),
        )
        return

    # ── State: awaiting final YES confirmation ─────────────────────────────────
    if state == "awaiting_invoice_confirmation" and text_lower in {"yes", "y"} and draft:
        # Calculate totals for the PDF
        items_with_totals = []
        subtotal = 0.0
        for item in draft["items"]:
            qty = item.get("qty", 0)
            unit = item.get("unit_price") or 0
            line_total = qty * unit
            subtotal += line_total
            items_with_totals.append({**item, "line_total": line_total})

        discount = draft.get("discount", 0) or 0
        tax = draft.get("tax", 0) or 0
        total = subtotal - discount + tax
        paid = draft.get("paid_amount", 0) or 0
        due = max(0, total - paid)
        status = "paid" if due <= 0 else ("partial" if paid > 0 else "unpaid")

        result = InvoiceService.post_invoice(
            {
                "business_id": link["business_id"],
                "store_id": link["store_id"],
                "customer_name": draft.get("customer_name"),
                "items": draft["items"],
                "paid_amount": paid,
                "discount": discount,
                "tax": tax,
                "source_channel": "whatsapp",
            }
        )
        invoice = result["invoice"]
        WhatsAppSessionService.upsert_session(
            sender_phone,
            business_id=link["business_id"],
            store_id=link["store_id"],
            feature_mode="invoice",
            state=None,
            draft_payload=None,
        )
        await send_whatsapp_message(
            sender_phone,
            f"✅ Invoice posted! Status: {invoice['payment_status'].upper()}. Due: {invoice['due_amount']:.2f}\n📄 Generating your bill PDF...",
        )

        # Generate and send PDF
        try:
            # Fetch business + store names for the PDF header
            biz_resp = supabase.table("businesses").select("name").eq("business_id", link["business_id"]).limit(1).execute()
            store_resp = supabase.table("stores").select("store_name").eq("store_id", link["store_id"]).limit(1).execute()
            business_name = (biz_resp.data or [{}])[0].get("name", "Business")
            store_name = (store_resp.data or [{}])[0].get("store_name", "Store")

            pdf_bytes = generate_invoice_pdf(
                invoice_id=invoice.get("invoice_id", "N/A"),
                business_name=business_name,
                store_name=store_name,
                customer_name=draft.get("customer_name"),
                items=items_with_totals,
                subtotal=subtotal,
                discount=discount,
                tax=tax,
                total=total,
                paid_amount=paid,
                due_amount=due,
                payment_status=invoice.get("payment_status", status),
            )
            filename = f"invoice_{str(invoice.get('invoice_id', 'bill'))[:8]}.pdf"
            await send_whatsapp_document(
                sender_phone,
                pdf_bytes,
                filename=filename,
                caption=f"Invoice #{str(invoice.get('invoice_id', ''))[:8]} — Total: {total:.2f}",
            )
        except Exception as pdf_err:
            logger.warning("invoice_pdf_failed reason=%s", pdf_err)
            await send_whatsapp_message(sender_phone, "⚠️ Bill PDF could not be generated, but the invoice was posted successfully.")
        return

    # ── Fresh extraction from text ─────────────────────────────────────────────
    extraction: InvoiceExtraction = await AIService.extract_invoice_from_text(user_text)
    if extraction.missing_core_data:
        WhatsAppSessionService.upsert_session(
            sender_phone,
            business_id=link["business_id"],
            store_id=link["store_id"],
            feature_mode="invoice",
            state="awaiting_invoice_missing_data",
            draft_payload=extraction.model_dump(),
        )
        await send_whatsapp_message(
            sender_phone,
            extraction.ai_question or "Please provide sold items and quantities.",
        )
        return

    resolved_items = []
    for item in extraction.items:
        product_resp = (
            supabase.table("products")
            .select("product_id,product_name")
            .eq("business_id", link["business_id"])
            .ilike("product_name", item.product_name)
            .limit(1)
            .execute()
        )
        if not product_resp.data:
            await send_whatsapp_message(
                sender_phone, f"Product '{item.product_name}' not found. Please correct and resend."
            )
            return
        resolved_items.append(
            {
                "product_id": product_resp.data[0]["product_id"],
                "product_name": item.product_name,
                "qty": item.qty,
                "unit_price": item.unit_price,
            }
        )

    draft_payload = {
        "customer_name": extraction.customer_name,
        "items": resolved_items,
        "paid_amount": extraction.paid_amount,
        "discount": extraction.discount or 0,
        "tax": extraction.tax or 0,
    }

    # ── Ask about discount before final confirmation ───────────────────────────
    WhatsAppSessionService.upsert_session(
        sender_phone,
        business_id=link["business_id"],
        store_id=link["store_id"],
        feature_mode="invoice",
        state="awaiting_discount_confirmation",
        draft_payload=draft_payload,
    )
    item_lines = "\n".join(
        f"  • {it['product_name']} x{it['qty']}" for it in resolved_items
    )
    await send_whatsapp_message(
        sender_phone,
        f"Got {len(resolved_items)} item(s):\n{item_lines}\n\n🏷️ Any discount on this bill?\nReply *yes <amount>* (e.g. yes 50) or *no*",
    )


async def _handle_text_for_existing_session(sender_phone: str, user_text: str):
    session = pending_sessions[sender_phone]
    mode = session["mode"]
    current_data = session["data"]
    is_yes = user_text.strip().lower() in ["yes", "y", "correct", "looks good"]

    if mode == "awaiting_confirmation" and is_yes:
        link = WhatsAppSessionService.get_active_link(sender_phone)
        if not link:
            await send_whatsapp_message(
                sender_phone,
                "Your phone number is not linked to any business account.",
            )
            _clear_pending_session(sender_phone)
            return

        InventoryService.save_whatsapp_entry(link["business_id"], link["store_id"], current_data)
        await send_whatsapp_message(sender_phone, "Saved successfully to your database.")
        _clear_pending_session(sender_phone)
        return

    # Any text in missing-data mode, or non-YES text in confirmation mode,
    # is treated as a correction/extra detail and merged by AI.
    updated_data = await AIService.correct_extraction(current_data, user_text)
    updated_dict = updated_data.model_dump()

    if updated_data.missing_core_data:
        _set_pending_session(sender_phone, "awaiting_missing_data", updated_dict)
        question = updated_data.ai_question or "Please provide the missing product name, quantity, and price."
        await send_whatsapp_message(sender_phone, question)
        return

    business_id = await _resolve_business_id(sender_phone)
    if not business_id:
        await send_whatsapp_message(sender_phone, "Phone number not linked to a business.")
        _clear_pending_session(sender_phone)
        return

    prod_req = (
        supabase.table("products")
        .select("product_id")
        .eq("business_id", business_id)
        .ilike("product_name", updated_data.product_name)
        .execute()
    )
    confirm_msg = _build_confirmation_message(updated_dict, len(prod_req.data) > 0)
    _set_pending_session(sender_phone, "awaiting_confirmation", updated_dict)
    await send_whatsapp_message(sender_phone, confirm_msg)

async def _handle_ledger_mode_text(
    sender_phone: str,
    link: dict,
    identity_context: dict,
    user_text: str,
):
    """
    Handle any text sent while the user is in 'ledger' sticky mode.
    Parses shorthand commands and auto-shows running totals after every entry.
    """
    role = identity_context["role"]
    store_id = identity_context.get("store_id") or link["store_id"]
    business_id = link["business_id"]

    if not store_id:
        await send_whatsapp_message(sender_phone, "No store is linked to your identity.")
        return

    command = parse_ledger_mode_shorthand(user_text)

    if command is None:
        await send_whatsapp_message(
            sender_phone,
            (
                "❓ Unrecognised. In ledger mode you can send:\n"
                "  expense 200 tea\n"
                "  bank deposit 5000\n"
                "  bank withdrawal 1000\n"
                "  total\n"
                "  close YYYY-MM-DD"
            ),
        )
        return

    if command.get("error"):
        await send_whatsapp_message(sender_phone, command["error"])
        return

    action = command["action"]

    # ── totals ────────────────────────────────────────────────────────────────
    if action == "totals":
        if not can_execute_ledger(role, "totals"):
            await send_whatsapp_message(sender_phone, "You are not allowed to view ledger totals.")
            return
        totals = CashLedgerService.get_current_totals(business_id, store_id)
        await send_whatsapp_message(
            sender_phone,
            (
                f"💵 Cash in hand:  {_format_currency(totals['cash_in_hand'])}\n"
                f"🏦 Cash in bank:  {_format_currency(totals['cash_in_bank'])}"
            ),
        )
        return

    # ── daily close ───────────────────────────────────────────────────────────
    if action == "close":
        if not can_execute_ledger(role, "close"):
            await send_whatsapp_message(sender_phone, "Daily close is owner/admin only.")
            return
        close = CashLedgerService.daily_close(
            business_id=business_id,
            store_id=store_id,
            as_of_date=command["as_of_date"],
            closing_by_user_id=identity_context["user_id"],
        )
        await send_whatsapp_message(
            sender_phone,
            (
                f"✅ Daily close done for {close['as_of_date']}.\n"
                f"💵 Cash in hand: {_format_currency(close['cash_in_hand'])}\n"
                f"🏦 Cash in bank: {_format_currency(close['cash_in_bank'])}"
            ),
        )
        return

    # ── entry (expense / deposit / withdrawal) ────────────────────────────────
    if action == "entry":
        entry_type = command["entry_type"]
        if not can_execute_ledger(role, "entry", entry_type):
            await send_whatsapp_message(sender_phone, "You are not allowed to post this ledger entry.")
            return
        CashLedgerService.add_entry(
            {
                "business_id": business_id,
                "store_id": store_id,
                "entry_type": entry_type,
                "amount": command["amount"],
                "note": command.get("note"),
                "actor_user_id": identity_context["user_id"],
                "actor_phone": sender_phone,
            }
        )
        # Auto-show running totals after every entry — user never calculates manually
        totals = CashLedgerService.get_current_totals(business_id, store_id)
        label = entry_type.replace("cash_", "").replace("_", " ")
        note_suffix = f" ({command['note']})" if command.get("note") else ""
        await send_whatsapp_message(
            sender_phone,
            (
                f"✅ {label.title()} {_format_currency(command['amount'])}{note_suffix} logged.\n\n"
                f"💵 Cash in hand: {_format_currency(totals['cash_in_hand'])}\n"
                f"🏦 Cash in bank: {_format_currency(totals['cash_in_bank'])}"
            ),
        )
        return


async def _handle_ledger_media_result(
    sender_phone: str,
    link: dict,
    identity_context: dict,
    extraction: "LedgerExtraction",
):
    """Process a LedgerExtraction returned from voice/image media in ledger mode."""
    if extraction.missing_core_data:
        await send_whatsapp_message(
            sender_phone,
            extraction.ai_question or "Could not read the amount or type. Is this an expense, bank deposit, or withdrawal?",
        )
        return

    # Validate entry_type
    valid_types = {"expense", "cash_deposit_bank", "cash_withdraw_bank"}
    entry_type = (extraction.entry_type or "").strip().lower()
    if entry_type not in valid_types:
        await send_whatsapp_message(
            sender_phone,
            f"Entry type '{extraction.entry_type}' not recognised. Please say: expense, bank deposit, or bank withdrawal.",
        )
        return

    # Reuse the ledger mode text handler by building a synthetic command dict
    command = {
        "action": "entry",
        "entry_type": entry_type,
        "amount": extraction.amount,
        "note": extraction.note,
    }
    role = identity_context["role"]
    store_id = identity_context.get("store_id") or link["store_id"]
    business_id = link["business_id"]

    if not can_execute_ledger(role, "entry", entry_type):
        await send_whatsapp_message(sender_phone, "You are not allowed to post this ledger entry.")
        return

    CashLedgerService.add_entry({
        "business_id": business_id,
        "store_id": store_id,
        "entry_type": entry_type,
        "amount": extraction.amount,
        "note": extraction.note,
        "actor_user_id": identity_context["user_id"],
        "actor_phone": sender_phone,
    })
    totals = CashLedgerService.get_current_totals(business_id, store_id)
    label = entry_type.replace("cash_", "").replace("_", " ")
    note_suffix = f" ({extraction.note})" if extraction.note else ""
    await send_whatsapp_message(
        sender_phone,
        (
            f"✅ {label.title()} {_format_currency(extraction.amount)}{note_suffix} logged.\n\n"
            f"💵 Cash in hand: {_format_currency(totals['cash_in_hand'])}\n"
            f"🏦 Cash in bank: {_format_currency(totals['cash_in_bank'])}"
        ),
    )


async def _handle_invoice_media_result(
    sender_phone: str,
    link: dict,
    extraction: "InvoiceExtraction",
):
    """Process an InvoiceExtraction returned from voice/image media in invoice mode."""
    if extraction.missing_core_data:
        WhatsAppSessionService.upsert_session(
            sender_phone,
            business_id=link["business_id"],
            store_id=link["store_id"],
            feature_mode="invoice",
            state="awaiting_invoice_missing_data",
            draft_payload=extraction.model_dump(),
        )
        await send_whatsapp_message(
            sender_phone,
            extraction.ai_question or "Please provide the sold items and quantities.",
        )
        return

    resolved_items = []
    for item in extraction.items:
        product_resp = (
            supabase.table("products")
            .select("product_id,product_name")
            .eq("business_id", link["business_id"])
            .ilike("product_name", item.product_name)
            .limit(1)
            .execute()
        )
        if not product_resp.data:
            await send_whatsapp_message(
                sender_phone,
                f"Product '{item.product_name}' not found. Please correct and resend.",
            )
            return
        resolved_items.append({
            "product_id": product_resp.data[0]["product_id"],
            "qty": item.qty,
            "unit_price": item.unit_price,
        })

    draft_payload = {
        "customer_name": extraction.customer_name,
        "items": resolved_items,
        "paid_amount": extraction.paid_amount,
        "discount": extraction.discount,
        "tax": extraction.tax,
    }
    WhatsAppSessionService.upsert_session(
        sender_phone,
        business_id=link["business_id"],
        store_id=link["store_id"],
        feature_mode="invoice",
        state="awaiting_invoice_confirmation",
        draft_payload=draft_payload,
    )
    await send_whatsapp_message(
        sender_phone,
        f"Draft invoice ready with {len(resolved_items)} item(s). Reply YES to post, or send corrections.",
    )


# ==========================================
# 1. THE BACKGROUND WORKER (No @router here!)
# ==========================================
async def process_webhook_logic(message: dict, sender_phone: str, msg_type: str):
    try:
        link = WhatsAppSessionService.get_active_link(sender_phone)

        # ── Subscription gate ─────────────────────────────────────────────────
        # Only enforce after the user is linked (onboarding is always allowed)
        if link:
            store_id = link["store_id"]
            if not SubscriptionService.is_access_allowed(store_id):
                payment_url = await SubscriptionService.get_or_create_payment_link(
                    store_id=store_id,
                    business_id=link["business_id"],
                    customer_phone=sender_phone,
                )
                await send_whatsapp_message(
                    sender_phone,
                    (
                        "⚠️ *Subscription Required*\n\n"
                        "Your free trial has ended. Subscribe to continue using the app.\n\n"
                        f"👉 Pay here (₹99/month):\n{payment_url or 'Contact support'}\n\n"
                        "Your data is safe and will be available once you subscribe."
                    ),
                )
                return

        # --- Phase 1: Handling Text ---
        if msg_type == "text":
            user_text = message["text"]["body"]

            onboarding_session = WhatsAppSessionService.get_session(sender_phone, None, None)
            if not link:
                if not onboarding_session or onboarding_session.get("state") != "awaiting_link_input":
                    WhatsAppSessionService.upsert_session(
                        sender_phone,
                        business_id=None,
                        store_id=None,
                        feature_mode="inventory",
                        state="awaiting_link_input",
                        draft_payload=None,
                    )
                    await send_whatsapp_message(
                        sender_phone,
                        "Your number is not linked. Reply in format: business:<business_id> store:<store_id>",
                    )
                    return

                business_id, store_id = extract_onboarding_ids(user_text)
                if not business_id or not store_id:
                    await send_whatsapp_message(
                        sender_phone,
                        "Invalid format. Use: business:<business_id> store:<store_id>",
                    )
                    return
                if not WhatsAppSessionService.validate_business_store_link(business_id, store_id):
                    await send_whatsapp_message(
                        sender_phone, "Business/store mismatch. Please re-check IDs."
                    )
                    return
                WhatsAppSessionService.upsert_user_link(sender_phone, business_id, store_id)
                WhatsAppSessionService.upsert_session(
                    sender_phone,
                    business_id=business_id,
                    store_id=store_id,
                    feature_mode="inventory",
                    state=None,
                    draft_payload=None,
                )
                await send_whatsapp_message(
                    sender_phone,
                    "Linked successfully. Default mode is inventory. Send 'invoice' to switch to sales mode.",
                )
                return

            # ── Exit mode command ─────────────────────────────────────────────
            if is_exit_mode_command(user_text):
                session = WhatsAppSessionService.get_session(
                    sender_phone, link["business_id"], link["store_id"]
                )
                current_mode = (session or {}).get("feature_mode", "inventory")
                WhatsAppSessionService.upsert_session(
                    sender_phone,
                    business_id=link["business_id"],
                    store_id=link["store_id"],
                    feature_mode="inventory",
                    state=None,
                    draft_payload=None,
                )
                await send_whatsapp_message(
                    sender_phone,
                    f"✅ Exited {current_mode} mode. Back to inventory mode.\n\nSwitch any time: invoice · ledger · inventory",
                )
                return

            # ── Mode timeout: auto-reset expired sessions ──────────────────────
            session = WhatsAppSessionService.get_session(
                sender_phone, link["business_id"], link["store_id"]
            )
            if session and is_mode_timed_out(
                session.get("mode_set_at"),
                session.get("mode_timeout_minutes", 120),
            ):
                WhatsAppSessionService.upsert_session(
                    sender_phone,
                    business_id=link["business_id"],
                    store_id=link["store_id"],
                    feature_mode="inventory",
                    state=None,
                    draft_payload=None,
                )
                session = None
                await send_whatsapp_message(
                    sender_phone,
                    "⏱️ Your session timed out and was reset to inventory mode.\nSend *invoice* or *ledger* to switch modes.",
                )

            mode_command = parse_mode_command(user_text)
            if mode_command:
                WhatsAppSessionService.set_mode(
                    sender_phone, link["business_id"], link["store_id"], mode_command
                )
                if mode_command == "ledger":
                    await send_whatsapp_message(
                        sender_phone,
                        (
                            "💰 Ledger mode ON. Commands:\n"
                            "  expense 200 tea\n"
                            "  bank deposit 5000\n"
                            "  bank withdrawal 1000\n"
                            "  total\n"
                            "  close YYYY-MM-DD (owner only)\n\n"
                            "Send *exit* to leave ledger mode."
                        ),
                    )
                else:
                    await send_whatsapp_message(
                        sender_phone,
                        f"Mode switched to *{mode_command}*. Send *exit* to go back to inventory mode.",
                    )
                return

            identity_context = WhatsAppSessionService.resolve_identity_context(
                sender_phone, link["business_id"], link["store_id"]
            )
            if not identity_context:
                await send_whatsapp_message(
                    sender_phone,
                    "Your WhatsApp number is linked to a store, but not to a user role. Ask owner/admin to map your number.",
                )
                return

            ledger_command = parse_ledger_command(user_text)
            if ledger_command:
                if ledger_command.get("error"):
                    await send_whatsapp_message(sender_phone, ledger_command["error"])
                    return
                await _handle_ledger_command(sender_phone, link, identity_context, ledger_command)
                return

            public_link_command = parse_public_link_command(user_text)
            if public_link_command:
                await _handle_public_link_command(
                    sender_phone, link, identity_context, public_link_command
                )
                return

            scoped_session = WhatsAppSessionService.get_session(
                sender_phone, link["business_id"], link["store_id"]
            )
            feature_mode = (scoped_session or {}).get("feature_mode", "inventory")

            if feature_mode == "ledger":
                await _handle_ledger_mode_text(sender_phone, link, identity_context, user_text)
                return

            if feature_mode == "invoice":
                await _route_invoice_text(sender_phone, link, user_text)
                return

            if sender_phone in pending_sessions:
                await _handle_text_for_existing_session(sender_phone, user_text)
            else:
                await send_whatsapp_message(
                    sender_phone,
                    "Please send a voice note or take a photo of the product.",
                )
            
        # --- Phase 2: Handling Audio/Image ---
        elif msg_type in ["audio", "image"]:
            media_id = message[msg_type]["id"]
            await send_whatsapp_message(sender_phone, "⏳ Processing your media...")

            if not link:
                await send_whatsapp_message(
                    sender_phone,
                    "Please link your account first. Send: business:<business_id> store:<store_id>",
                )
                return

            identity_context = WhatsAppSessionService.resolve_identity_context(
                sender_phone, link["business_id"], link["store_id"]
            )
            if not identity_context:
                await send_whatsapp_message(
                    sender_phone,
                    "Your number is linked to a store but not to a user role. Ask owner/admin to map your number.",
                )
                return

            scoped_session = WhatsAppSessionService.get_session(
                sender_phone, link["business_id"], link["store_id"]
            )
            feature_mode = (scoped_session or {}).get("feature_mode", "inventory")

            # ── Route media to the correct mode handler ───────────────────────
            if feature_mode == "ledger":
                extraction = await AIService.process_media_for_ledger(media_id, msg_type)
                await _handle_ledger_media_result(sender_phone, link, identity_context, extraction)

            elif feature_mode == "invoice":
                extraction = await AIService.process_media_for_invoice(media_id, msg_type)
                await _handle_invoice_media_result(sender_phone, link, extraction)

            else:  # inventory (default)
                extracted_data = await AIService.process_media_for_inventory(media_id, msg_type)
                extracted_dict = extracted_data.model_dump()

                if extracted_data.missing_core_data:
                    _set_pending_session(sender_phone, "awaiting_missing_data", extracted_dict)
                    question = extracted_data.ai_question or "Please provide the missing product name, quantity, and price."
                    await send_whatsapp_message(sender_phone, question)
                    return

                business_id = link["business_id"]
                prod_req = (
                    supabase.table("products")
                    .select("product_id")
                    .eq("business_id", business_id)
                    .ilike("product_name", extracted_data.product_name)
                    .execute()
                )
                is_existing = len(prod_req.data) > 0
                confirm_msg = _build_confirmation_message(extracted_dict, is_existing)
                _set_pending_session(sender_phone, "awaiting_confirmation", extracted_dict)
                await send_whatsapp_message(sender_phone, confirm_msg)

    except Exception as e:
        logger.exception("whatsapp_worker_error error=%s", e)
# ==========================================
# 2. THE FRONT DOOR (This has the @router!)
# ==========================================
@router.post("/webhook")
async def receive_message(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    
    try:
        entry = payload.get("entry", [])[0]
        changes = entry.get("changes", [])[0]
        value = changes.get("value", {})
        
        # Ignore status updates (read, delivered)
        if "messages" not in value:
            return {"status": "ignored"}
            
        message = value["messages"][0]
        sender_phone = message.get("from")
        msg_type = message.get("type") 
        
        logger.info("whatsapp_webhook_received sender=%s msg_type=%s", sender_phone, msg_type)
        
        # INSTANTLY pass the work to the background task function above
        background_tasks.add_task(process_webhook_logic, message, sender_phone, msg_type)

        # INSTANTLY return 200 OK so Meta doesn't loop!
        return {"status": "success"}

    except Exception as e:
        logger.exception("whatsapp_webhook_error error=%s", e)
        return {"status": "error"}