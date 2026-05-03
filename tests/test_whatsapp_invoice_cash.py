import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from services.finance_utils import compute_cash_totals, compute_payment
from services.public_link_service import PublicLinkService
from services.whatsapp_parsing import (
    can_execute_ledger,
    extract_onboarding_ids,
    parse_ledger_command,
    parse_mode_command,
    parse_public_link_command,
)


class WhatsAppParsingTests(unittest.TestCase):
    def test_extract_onboarding_ids(self):
        business_id, store_id = extract_onboarding_ids(
            "business:11111111-1111-1111-1111-111111111111 store:22222222-2222-2222-2222-222222222222"
        )
        self.assertTrue(business_id.startswith("1111"))
        self.assertTrue(store_id.startswith("2222"))

    def test_mode_commands(self):
        self.assertEqual(parse_mode_command("inventory"), "inventory")
        self.assertEqual(parse_mode_command("sales"), "invoice")
        self.assertEqual(parse_mode_command("invoice"), "invoice")
        self.assertIsNone(parse_mode_command("help"))

    def test_parse_ledger_entry_command(self):
        cmd = parse_ledger_command("ledger expense 200 staff tea")
        self.assertEqual(cmd["action"], "entry")
        self.assertEqual(cmd["entry_type"], "expense")
        self.assertEqual(cmd["amount"], 200.0)
        self.assertEqual(cmd["note"], "staff tea")

    def test_parse_ledger_close_command(self):
        cmd = parse_ledger_command("ledger close 2026-05-01")
        self.assertEqual(cmd["action"], "close")
        self.assertEqual(str(cmd["as_of_date"]), "2026-05-01")

    def test_parse_public_link_command(self):
        cmd = parse_public_link_command("link inventory")
        self.assertEqual(cmd["kind"], "inventory")

    def test_ledger_rbac(self):
        self.assertTrue(can_execute_ledger("owner", "close"))
        self.assertTrue(can_execute_ledger("manager", "entry", "expense"))
        self.assertFalse(can_execute_ledger("sales", "entry", "expense"))
        self.assertFalse(can_execute_ledger("staff", "close"))


class InvoiceComputationTests(unittest.TestCase):
    def test_payment_status_paid(self):
        result = compute_payment(subtotal=100, discount=0, tax=0, paid_amount=100)
        self.assertEqual(result.payment_status, "paid")
        self.assertEqual(result.due_amount, 0)

    def test_payment_status_partial(self):
        result = compute_payment(subtotal=100, discount=0, tax=0, paid_amount=40)
        self.assertEqual(result.payment_status, "partial")
        self.assertEqual(result.due_amount, 60)


class CashLedgerTotalsTests(unittest.TestCase):
    def test_bank_and_hand_totals(self):
        totals = compute_cash_totals(
            [
            {"entry_type": "opening_balance", "direction": "in", "amount": 1000},
            {"entry_type": "expense", "direction": "out", "amount": 200},
            {"entry_type": "cash_deposit_bank", "direction": "out", "amount": 300},
            {"entry_type": "cash_withdraw_bank", "direction": "in", "amount": 50},
            ]
        )
        self.assertEqual(totals["cash_in_hand"], 550)
        self.assertEqual(totals["cash_in_bank"], 250)


class _FakeQuery:
    def __init__(self, table_name, supabase):
        self.table_name = table_name
        self.supabase = supabase
        self.filters = {}
        self.update_payload = None

    def select(self, _value):
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def is_(self, _key, _value):
        return self

    def limit(self, _value):
        return self

    def insert(self, payload):
        self.supabase.inserted_payload = payload
        return self

    def update(self, payload):
        self.update_payload = payload
        return self

    def execute(self):
        if self.table_name == "public_share_links":
            if self.update_payload is not None:
                self.supabase.updated_payload = self.update_payload
                return type("Resp", (), {"data": [self.update_payload]})
            token_hash = self.filters.get("token_hash")
            if token_hash and token_hash in self.supabase.link_by_hash:
                return type("Resp", (), {"data": [self.supabase.link_by_hash[token_hash]]})
            return type("Resp", (), {"data": []})
        if self.table_name == "inventory":
            return type("Resp", (), {"data": self.supabase.inventory_rows})
        return type("Resp", (), {"data": []})


class _FakeSupabase:
    def __init__(self, link_by_hash, inventory_rows):
        self.link_by_hash = link_by_hash
        self.inventory_rows = inventory_rows
        self.inserted_payload = None
        self.updated_payload = None

    def table(self, table_name):
        return _FakeQuery(table_name, self)


class PublicLinkServiceTests(unittest.TestCase):
    def test_create_link_uses_short_ttl_for_non_owner(self):
        fake_supabase = _FakeSupabase({}, [])
        with patch("services.public_link_service.supabase", fake_supabase):
            result = PublicLinkService.create_link(
                business_id="b1",
                store_id="s1",
                user_id="u1",
                role="sales",
                kind="inventory",
                base_url="http://localhost:5173",
            )
        self.assertEqual(result["expires_in_minutes"], 60)
        self.assertEqual(result["scope"], "inventory_store_summary")
        self.assertTrue(fake_supabase.inserted_payload["token_hash"])

    def test_get_inventory_snapshot_rejects_expired_token(self):
        expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        token = "test-token"
        token_hash = PublicLinkService._hash_token(token)
        fake_supabase = _FakeSupabase(
            {
                token_hash: {
                    "share_link_id": "sl1",
                    "token_hash": token_hash,
                    "business_id": "b1",
                    "store_id": "s1",
                    "scope": "inventory_store_summary",
                    "expires_at": expired,
                    "max_uses": None,
                    "use_count": 0,
                }
            },
            [],
        )
        with patch("services.public_link_service.supabase", fake_supabase):
            with self.assertRaises(ValueError):
                PublicLinkService.get_inventory_snapshot(token)

    def test_get_inventory_snapshot_returns_sanitized_summary(self):
        valid = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
        token = "valid-token"
        token_hash = PublicLinkService._hash_token(token)
        fake_supabase = _FakeSupabase(
            {
                token_hash: {
                    "share_link_id": "sl2",
                    "token_hash": token_hash,
                    "business_id": "b1",
                    "store_id": "s1",
                    "scope": "inventory_store_summary",
                    "expires_at": valid,
                    "max_uses": None,
                    "use_count": 0,
                }
            },
            [
                {
                    "stock_quantity": 4,
                    "buy_price": 10,
                    "selling_price": 14,
                    "products": {"product_name": "Pipe", "sku_code": "P1"},
                },
                {
                    "stock_quantity": 20,
                    "buy_price": 5,
                    "selling_price": 9,
                    "products": {"product_name": "Tape", "sku_code": "T1"},
                },
            ],
        )
        with patch("services.public_link_service.supabase", fake_supabase):
            payload = PublicLinkService.get_inventory_snapshot(token)
        self.assertEqual(payload["summary"]["total_unique_items"], 2)
        self.assertEqual(payload["summary"]["low_stock_alerts"], 1)
        self.assertIsNotNone(fake_supabase.updated_payload)


if __name__ == "__main__":
    unittest.main()
