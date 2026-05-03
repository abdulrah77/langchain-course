# User Cheat Sheet — WhatsApp Inventory + Sales + Ledger

Quick reference for day-to-day use. Send messages in WhatsApp as described below.

---

## First-time setup (onboarding)

1. Send any message from your WhatsApp number (if not linked yet).
2. The app asks you to link with:
   ```text
   business:<business_id> store:<store_id>
   ```
3. Example (using your store code):
   ```text
   business:12 store:5
   ```
   Your **store code** (e.g. `SAMS-MH-01`) is shown when your store is created.
4. Use that exact pattern (spaces as shown). If the format is wrong, fix it and send again.

---

## Switch what you're doing (sticky mode)

Send one of these **by itself**:

| Send        | Meaning                                              |
|-------------|------------------------------------------------------|
| `invoice`   | Invoice / sales drafting, discount, PDF              |
| `inventory` | Inventory management (photo/audio/text)              |
| `ledger`    | Cash ledger — expenses, deposits, withdrawals        |
| `sales`     | Sales-focused context                                |
| `exit`      | Leave current mode → back to inventory               |

**Sticky mode:** After you switch, all messages (text AND voice/photo) follow that mode until you switch again.

**Exit mode:** Send `exit`, `reset`, `quit`, or `menu` to go back to inventory mode at any time.

**Auto-reset:** If you don't send anything for 2 hours, the session resets automatically to inventory mode.

---

## Inventory — add stock

1. Send `inventory` to switch to inventory mode.
2. Then send a **photo of the product**, a **voice note**, or a **text description**.
3. The app asks for confirmation with extracted data.
4. Reply **`YES`** to save.

**Supported inputs:**
- 📷 Photo → AI extracts product name, quantity, price
- 🎙️ Voice note → AI transcribes → extracts data
- ✍️ Text → AI parses directly

---

## Invoices — record a sale

1. Switch to invoice mode: send `invoice`.
2. Send your invoice details — as **text**, a **voice note**, or a **photo of a handwritten bill**.
3. The bot shows extracted items.
4. Bot asks: **"Any discount? Reply `yes 50` or `no`"**
5. Reply `yes <amount>` or `no`.
6. Bot shows the **full itemised bill** with subtotal, discount, tax, total, and due.
7. Reply **`YES`** to confirm and post.
8. Invoice is saved → stock is reduced → **PDF bill sent as WhatsApp document**.

**Stock rule:** If you try to sell more than available stock, the entire sale is **rejected** — nothing is partially saved.

---

## Payment status (automatic)

The system derives these from collected amounts — you don't set them manually:

- **`paid`** — fully collected
- **`partial`** — partly collected
- **`unpaid`** — nothing collected yet

---

## Cash ledger — ledger sticky mode (recommended)

Send `ledger` to switch into ledger mode. The bot will confirm with a command list.

In ledger mode, send **short commands** — no `ledger` prefix needed:

| Send | What it does |
|---|---|
| `expense 200 tea` | Records ₹200 expense labelled "tea" |
| `bank deposit 5000` | Cash moved to bank |
| `deposit bank 5000` | Same — both word orders work |
| `bank withdrawal 1000` | Cash taken from bank |
| `withdrawal bank 1000` | Same |
| `total` or `balance` | Shows current running totals |
| `close 2026-05-01` | Daily close (owner/admin only) |

**Supports voice notes and receipt photos too:**
- 🎙️ Say *"spent 350 on electricity"* → transcribed → logged → totals shown
- 📷 Photo of a bill → AI reads amount → logged → totals shown

**After every entry**, the bot automatically shows:
```
✅ Expense 200.00 (tea) logged.

💵 Cash in hand: 3,800.00
🏦 Cash in bank: 12,000.00
```
You never have to calculate or ask for totals separately.

## Cash ledger — old-style prefix commands (also work)

If you're not in ledger mode, prefix commands with `ledger`:

- `ledger expense <amount> <note...>` — money out
- `ledger deposit_bank <amount> <note...>` — cash to bank
- `ledger withdraw_bank <amount> <note...>` — cash from bank
- `ledger totals` — cash in hand + cash in bank
- `ledger close <YYYY-MM-DD>` — daily close (owner/admin only)

Role rules:

- **Owner / Admin**: all ledger commands including `ledger close`
- **Manager / Inventory Manager / Product Manager**: expense / deposit / withdraw + totals
- **Sales / Staff**: totals only (no manual entries, no daily close)

**Typical day:**

1. Record expenses and cash movements as they happen.
2. Check **totals** — cash in hand and cash in bank.
3. Run **daily close** at the end of day to lock in closing balances.

---

## Public inventory links (from WhatsApp)

Generate temporary browser links directly in WhatsApp:

- **`link inventory`**
- **`link dashboard`**

The bot replies with:

```text
Open inventory (expires in 60 min): <url>
```

Access rules:

- **Owner / Admin**: broader business-level inventory view
- **Non-owner users**: store-scoped summary link (short-lived)

---

## Purchases — restock inventory

Use the API (or a future WhatsApp purchase mode) to record supplier purchases:

- Creates a purchase record linked to a supplier.
- Automatically increases stock for each item.
- Logs an `expense` in the cash ledger if paid upfront.
- Subsequent payments can be recorded with `POST /api/purchases/{id}/payments`.

**Payment status** works the same as invoices: `paid`, `partial`, `unpaid`.

---

## Sales Returns — reverse a sale

Use the API to process a customer return:

- Link the return to the original invoice.
- Stock is automatically **restored** for returned items.
- If a refund is issued, an `expense` is automatically logged in the cash ledger.

---

## Role permissions summary

| Action                          | Owner | Admin | Manager | Staff/Sales |
|---------------------------------|-------|-------|---------|-------------|
| View inventory / totals         | ✅    | ✅    | ✅      | ✅          |
| Post invoice / sale             | ✅    | ✅    | ✅      | ✅          |
| Post ledger entries             | ✅    | ✅    | ✅      | ❌          |
| View ledger totals              | ✅    | ✅    | ✅      | ✅          |
| Daily close                     | ✅    | ✅    | ❌      | ❌          |
| Manual stock adjustment         | ✅    | ✅    | ✅      | ❌          |
| Process purchase / return       | ✅    | ✅    | ✅      | ❌          |
| Generate public links           | ✅    | ✅    | ✅      | limited     |

---

## Common mistakes

- Wrong onboarding line — must be `business:<id> store:<id>`.
- Working in the wrong mode — send `invoice`, `inventory`, or `ledger` first.
- Forgetting **`YES`** after the final bill — invoice won't be posted.
- Selling **over stock** — blocked on purpose, entire sale rejected.
- Invalid ledger amount/date format — amount must be a number, date must be `YYYY-MM-DD`.
- Trying `ledger close` without owner/admin access.
- Trying to manually adjust stock as a staff member — only manager+ can do this.
- In ledger mode, sending unrecognised text — bot will show the command list again.

---

## Quick fixes

| Problem                           | Try this                                              |
|-----------------------------------|-------------------------------------------------------|
| Wrong reply from bot              | Send `invoice` or `inventory` again, then retry       |
| Onboarding won't work             | Recheck business and store IDs and format             |
| Draft won't become real           | Reply exactly `YES`                                   |
| "Wrong store" / access issues     | Store must belong to **your** business and be in your access list |
| Ledger command rejected           | Check your role permissions and command format        |
| Public link not opening           | Link may be expired — request a fresh one in WhatsApp |
| Voice note not understood         | Speak clearly; app will ask follow-up if data is missing |
| Image not parsed correctly        | Send text correction after the draft appears          |

---

---

## Subscription

| Status | What happens |
|---|---|
| **Trial (7 days)** | All features available, no payment needed |
| **3 days before expiry** | WhatsApp reminder + payment link sent |
| **Expired** | All commands blocked, payment link shown |
| **Active** | All features available until renewal date |

**Price:** ₹99/store/month

**To pay:** Click the Razorpay link sent by the bot → pay via UPI, card, or netbanking.

Once payment is confirmed, the bot sends: *"✅ Payment received! Active until <date>"*

---

## One-line reminders

- **Link once:** `business:<id> store:<id>`
- **Mode:** `invoice` · `inventory` · `ledger` · `sales` · `exit`
- **Post invoice:** `YES` after discount step + final bill → PDF sent
- **Ledger (sticky):** `expense` / `bank deposit` / `bank withdrawal` → `total` → `close`
- **Ledger (prefix):** `ledger expense/deposit_bank/withdraw_bank` → `ledger totals` → `ledger close`
- **Exit mode:** `exit` or `reset`
- **Public link:** `link inventory` / `link dashboard`
- **Purchases + Returns:** via API (or future WhatsApp commands)
