# WhatsApp Inventory Tool

FastAPI + Supabase + React application for inventory management, with WhatsApp webhook ingestion and AI-assisted extraction from image/audio messages.

## Architecture

- Backend: FastAPI app in `main.py` with routers under `routers/`
- Data: Supabase tables (`businesses`, `user_businesses`, `products`, `inventory`, etc.)
- WhatsApp ingestion: `routers/whatsapp.py` + `services/ai_service.py`
- Inventory domain: `routers/inventory.py` + `services/inventory_service.py`
- Frontend dashboard: React app in `frontend/`

## Environment Setup

Create `ice_breaker/.env` for backend:

```env
NEXT_PUBLIC_SUPABASE_URL=...
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=...

WHATSAPP_VERIFY_TOKEN=...
WHATSAPP_ACCESS_TOKEN=...
WHATSAPP_PHONE_ID=...

GEMINI_API_KEY=...

# AI extraction routing (cost optimization)
AI_ROUTING_MODE=cheap_first
AI_PROVIDER_PRIORITY=gemini,openrouter
AI_TIMEOUT_SECONDS=25
AI_MAX_FALLBACKS=2
GEMINI_MODEL=gemini-2.5-flash

# Optional second provider
OPENROUTER_API_KEY=...
OPENROUTER_MODEL=openai/gpt-4o-mini
```

Create `ice_breaker/frontend/.env` for frontend:

```env
VITE_SUPABASE_URL=...
VITE_SUPABASE_PUBLISHABLE_KEY=...
VITE_API_BASE_URL=http://127.0.0.1:8000
```

Do not store backend secrets in frontend env files.

## Run Backend

```bash
cd ice_breaker
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

## Run Frontend

```bash
cd ice_breaker/frontend
npm install
npm run dev
```

Frontend runs on `http://127.0.0.1:5173` by default.

## Core API Routes

- `GET /health`
- `GET /api/inventory/`
- `POST /api/inventory/`
- `PUT /api/inventory/{inventory_id}`
- `DELETE /api/inventory/{inventory_id}`
- `GET /api/inventory/stats/dashboard`
- `GET /api/whatsapp/webhook`
- `POST /api/whatsapp/webhook`
- `POST /api/invoices/`
- `POST /api/cash-ledger/entries`
- `GET /api/cash-ledger/totals/{store_id}`
- `POST /api/cash-ledger/daily-close`

## WhatsApp Flow (High Level)

1. User sends audio/image to WhatsApp.
2. Webhook receives media event and calls AI extraction.
3. AI service selects providers/models based on routing mode and priority, then tries them in order.
4. If core fields are missing, a pending missing-data session is stored and user is asked follow-up questions.
5. Once data is complete, user receives confirmation preview.
6. User replies `YES` to persist in Supabase inventory tables.

### AI Routing Behavior

- `AI_ROUTING_MODE=cheap_first`: starts with lower-cost configured providers, then falls back.
- `AI_ROUTING_MODE=balanced`: uses default mixed ordering with the same fallback behavior.
- `AI_ROUTING_MODE=quality_first`: starts with stronger configured providers first.
- `AI_PROVIDER_PRIORITY`: optional comma-separated override (for example `gemini,openrouter`).
- `AI_TIMEOUT_SECONDS`: hard timeout per provider attempt.
- `AI_MAX_FALLBACKS`: max number of fallback attempts after the first provider.
- Provider/model attempts are validated before acceptance:
  - output must deserialize into `InventoryExtraction`
  - core fields (`product_name`, `total_quantity`, `base_price`) must be present unless `missing_core_data=true`
  - invalid or timed out responses trigger fallback to the next candidate

## Notes

- Current pending WhatsApp session memory is in-process (`pending_sessions` dict). For production, move this to Redis or a durable store.
- SQL migration for WhatsApp session/link, invoice, and cash-ledger entities is available at `sql/migrations/20260501_whatsapp_invoice_cash_ledger.sql`.
- Manual validation steps are documented in `QA_WHATSAPP_LEDGER_CHECKLIST.md`.

## Authentication and Tenant Isolation

- Web app login uses Supabase email/password authentication.
- API access uses Bearer JWT tokens issued by Supabase; backend dependencies validate the token and resolve user context.
- WhatsApp webhook flow maps sender phone number to a business for identity mapping in that channel; it is not equivalent to a JWT session.
- Tenant isolation is enforced defensively at two layers:
  - app-layer service mutations scope update/delete operations by both record ID and authenticated `business_id`
  - database row-level security (RLS) is still expected as the primary DB guardrail
- For product, supplier, and inventory mutations, scoped update/delete calls return a safe not-found response when no row is affected.
