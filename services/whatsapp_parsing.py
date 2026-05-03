from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional, Tuple


def extract_onboarding_ids(user_text: str) -> Tuple[Optional[str], Optional[str]]:
    normalized = user_text.replace(",", " ").replace("=", ":")
    parts = [p.strip() for p in normalized.split() if p.strip()]
    business_id = None
    store_id = None
    for token in parts:
        lowered = token.lower()
        if lowered.startswith("business:"):
            business_id = token.split(":", 1)[1]
        if lowered.startswith("store:"):
            store_id = token.split(":", 1)[1]
    if not business_id or not store_id:
        return None, None
    return business_id, store_id


def parse_mode_command(text: str) -> Optional[str]:
    normalized = text.strip().lower()
    if normalized == "inventory":
        return "inventory"
    if normalized in ("sales", "invoice"):
        return "invoice"
    if normalized in ("ledger", "cash", "cashledger", "cash ledger"):
        return "ledger"
    return None


EXIT_COMMANDS = {"exit", "exit mode", "reset", "reset mode", "quit", "stop mode", "main menu", "menu"}


def is_exit_mode_command(text: str) -> bool:
    """Returns True if the user wants to exit the current sticky mode."""
    return text.strip().lower() in EXIT_COMMANDS


MODE_DEFAULT_TIMEOUT_MINUTES = 120


def is_mode_timed_out(mode_set_at_iso: Optional[str], timeout_minutes: int = MODE_DEFAULT_TIMEOUT_MINUTES) -> bool:
    """
    Returns True if the session mode has exceeded the timeout window.
    mode_set_at_iso: ISO 8601 string from whatsapp_sessions.mode_set_at
    """
    if not mode_set_at_iso:
        return False
    try:
        from datetime import datetime, timezone
        mode_set_at = datetime.fromisoformat(mode_set_at_iso.replace("Z", "+00:00"))
        elapsed = (datetime.now(timezone.utc) - mode_set_at).total_seconds() / 60
        return elapsed > timeout_minutes
    except Exception:
        return False

def parse_ledger_mode_shorthand(text: str) -> Optional[Dict[str, Any]]:
    """
    Parse natural shorthand ledger commands when the user is already in ledger mode.
    Accepts flexible word orders and typo-tolerant synonyms.

    Supported patterns (case-insensitive):
      expense <amount> [note...]
      deposit bank <amount> [note...]  /  bank deposit <amount> [note...]
      withdrawal bank <amount> [note...]  /  bank withdrawal <amount> [note...]
      total  /  totals  /  balance
      close <YYYY-MM-DD>
    """
    raw = text.strip()
    parts = raw.split()
    if not parts:
        return None

    lower_parts = [p.lower() for p in parts]
    normalized = " ".join(lower_parts)

    # ── totals / balance ──────────────────────────────────────────────────────
    if lower_parts[0] in {"total", "totals", "balance", "bal", "summary"}:
        return {"action": "totals"}

    # ── close ─────────────────────────────────────────────────────────────────
    if lower_parts[0] == "close":
        if len(parts) < 2:
            return {"error": "Date is required. Example: close 2026-05-01"}
        try:
            from datetime import datetime
            as_of_date = datetime.strptime(parts[1], "%Y-%m-%d").date()
        except ValueError:
            return {"error": "Date must be YYYY-MM-DD. Example: close 2026-05-01"}
        return {"action": "close", "as_of_date": as_of_date}

    # ── expense ───────────────────────────────────────────────────────────────
    if lower_parts[0] == "expense":
        return _parse_amount_entry(parts, 1, "expense")

    # ── bank deposit  /  deposit bank ─────────────────────────────────────────
    if normalized.startswith("bank deposit") or normalized.startswith("deposit bank"):
        skip = 2
        return _parse_amount_entry(parts, skip, "cash_deposit_bank")

    # ── bank withdrawal  /  withdrawal bank  /  bank withdraw ─────────────────
    if (
        normalized.startswith("bank withdrawal")
        or normalized.startswith("withdrawal bank")
        or normalized.startswith("bank withdraw")
        or normalized.startswith("withdraw bank")
    ):
        skip = 2
        return _parse_amount_entry(parts, skip, "cash_withdraw_bank")

    return None


def _parse_amount_entry(parts: list[str], amount_index: int, entry_type: str) -> Dict[str, Any]:
    if len(parts) <= amount_index:
        label = entry_type.replace("cash_", "").replace("_", " ")
        return {"error": f"Amount is required. Example: {label} 500"}
    try:
        amount = float(parts[amount_index])
    except ValueError:
        return {"error": "Amount must be a number. Example: expense 100 tea"}
    if amount <= 0:
        return {"error": "Amount must be greater than 0."}
    note = " ".join(parts[amount_index + 1:]).strip() or None
    return {"action": "entry", "entry_type": entry_type, "amount": amount, "note": note}


def parse_ledger_command(text: str) -> Optional[Dict[str, Any]]:
    parts = text.strip().split()
    if len(parts) < 2 or parts[0].lower() != "ledger":
        return None

    command = parts[1].lower()
    if command in {"expense", "deposit_bank", "withdraw_bank"}:
        if len(parts) < 3:
            return {"error": "Amount is required. Example: ledger expense 100 tea"}
        try:
            amount = float(parts[2])
        except ValueError:
            return {"error": "Amount must be a number."}
        if amount <= 0:
            return {"error": "Amount must be greater than 0."}
        entry_type_map = {
            "expense": "expense",
            "deposit_bank": "cash_deposit_bank",
            "withdraw_bank": "cash_withdraw_bank",
        }
        note = " ".join(parts[3:]).strip() or None
        return {
            "action": "entry",
            "entry_type": entry_type_map[command],
            "amount": amount,
            "note": note,
        }

    if command == "totals":
        return {"action": "totals"}

    if command == "close":
        if len(parts) < 3:
            return {"error": "Date is required. Example: ledger close 2026-05-01"}
        try:
            as_of_date = datetime.strptime(parts[2], "%Y-%m-%d").date()
        except ValueError:
            return {"error": "Date must be in YYYY-MM-DD format."}
        return {"action": "close", "as_of_date": as_of_date}

    return None


def parse_public_link_command(text: str) -> Optional[Dict[str, str]]:
    normalized = " ".join(text.strip().lower().split())
    if normalized == "link inventory":
        return {"action": "create_public_link", "kind": "inventory"}
    if normalized == "link dashboard":
        return {"action": "create_public_link", "kind": "dashboard"}
    return None


def can_execute_ledger(role: str, action: str, entry_type: str | None = None) -> bool:
    normalized_role = (role or "").strip().lower()
    if normalized_role in {"owner", "admin"}:
        return True
    if action == "close":
        return False
    if action == "totals":
        return normalized_role in {
            "manager",
            "inventory_manager",
            "product_manager",
            "sales",
            "sales_staff",
            "staff",
        }
    if action == "entry":
        if normalized_role in {"manager", "inventory_manager", "product_manager"}:
            return entry_type in {"expense", "cash_deposit_bank", "cash_withdraw_bank"}
        return False
    return False


def resolve_public_link_scope(role: str, requested_kind: str) -> str:
    normalized_role = (role or "").strip().lower()
    if requested_kind == "dashboard":
        return "inventory_business_view" if normalized_role in {"owner", "admin"} else "dashboard_store_summary"
    return "inventory_business_view" if normalized_role in {"owner", "admin"} else "inventory_store_summary"
