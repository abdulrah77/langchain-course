# services/ai_service.py
import asyncio
import json
import logging
import os
from typing import List, Optional

import httpx
from google import genai
from google.genai import types
from pydantic import BaseModel, Field, ValidationError

from services.ai_router import extract_image_text, transcribe_audio


class ProductVariant(BaseModel):
    description: str = Field(description="Variant details (e.g., 'blue', 'size XL')")
    quantity: int = Field(description="Quantity for this specific variant")
    price: Optional[float] = Field(description="Price for this variant, if different from base price. Otherwise return null.")


class InventoryExtraction(BaseModel):
    product_name: str = Field(description="The generic name of the product")
    total_quantity: int = Field(description="Total number of items. If variants are listed, this should be the sum of their quantities.")
    base_price: float = Field(description="The default price per unit. Use 0 if completely unknown.")
    brand: Optional[str] = Field(description="The brand name, if mentioned")
    variants: List[ProductVariant] = Field(default=[], description="List of specific variations and their quantities.")
    
    # --- NEW: Dynamic AI Prompts ---
    missing_core_data: bool = Field(description="True ONLY if product_name, total_quantity, or base_price is completely missing.")
    ai_question: Optional[str] = Field(description="If missing_core_data is True, what exactly should we ask the user to provide?")# services/ai_service.py
# (Keep your imports and InventoryExtraction class at the top)


class InvoiceItemExtraction(BaseModel):
    product_name: str = Field(description="Product name as spoken/written")
    qty: float = Field(description="Quantity sold")
    unit_price: Optional[float] = Field(description="Unit price if provided")


class InvoiceExtraction(BaseModel):
    customer_name: Optional[str] = Field(description="Optional customer name")
    items: List[InvoiceItemExtraction] = Field(default=[])
    paid_amount: float = Field(default=0, description="Amount collected now")
    discount: float = Field(default=0, description="Discount applied")
    tax: float = Field(default=0, description="Tax amount")
    missing_core_data: bool = Field(description="True when required invoice data is missing")
    ai_question: Optional[str] = Field(description="Follow-up question when missing data")


class LedgerExtraction(BaseModel):
    entry_type: str = Field(
        description="One of: expense, cash_deposit_bank, cash_withdraw_bank"
    )
    amount: float = Field(description="The monetary amount. Must be > 0.")
    note: Optional[str] = Field(description="Short description or reason for this entry")
    missing_core_data: bool = Field(
        description="True if entry_type or amount cannot be determined from the input"
    )
    ai_question: Optional[str] = Field(
        description="Question to ask the user when missing_core_data is True"
    )


class AIService:
    _logger = logging.getLogger(__name__)

    @staticmethod
    def _env_int(name: str, default: int) -> int:
        value = os.getenv(name)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            return default

    @staticmethod
    def _env_float(name: str, default: float) -> float:
        value = os.getenv(name)
        if value is None:
            return default
        try:
            return float(value)
        except ValueError:
            return default

    @staticmethod
    def _parse_priority(raw_priority: Optional[str]) -> List[str]:
        if not raw_priority:
            return []
        return [item.strip().lower() for item in raw_priority.split(",") if item.strip()]

    @staticmethod
    def _routing_candidates() -> List[Tuple[str, str]]:
        routing_mode = os.getenv("AI_ROUTING_MODE", "cheap_first").strip().lower()
        env_priority = AIService._parse_priority(os.getenv("AI_PROVIDER_PRIORITY"))

        gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        openrouter_model = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")

        default_by_mode = {
            "cheap_first": [("gemini", gemini_model), ("openrouter", openrouter_model)],
            "balanced": [("gemini", gemini_model), ("openrouter", openrouter_model)],
            "quality_first": [("openrouter", openrouter_model), ("gemini", gemini_model)],
        }
        ordered = default_by_mode.get(routing_mode, default_by_mode["cheap_first"])

        if env_priority:
            model_map = {"gemini": gemini_model, "openrouter": openrouter_model}
            ordered = [(provider, model_map[provider]) for provider in env_priority if provider in model_map]
            for provider, model in default_by_mode.get(routing_mode, default_by_mode["cheap_first"]):
                if provider not in [p for p, _ in ordered]:
                    ordered.append((provider, model))

        available: List[Tuple[str, str]] = []
        for provider, model in ordered:
            if provider == "gemini" and os.getenv("GEMINI_API_KEY"):
                available.append((provider, model))
            if provider == "openrouter" and os.getenv("OPENROUTER_API_KEY"):
                available.append((provider, model))
        return available

    @staticmethod
    def _validate_extraction(extraction: InventoryExtraction) -> InventoryExtraction:
        normalized = extraction.model_copy(deep=True)
        normalized.product_name = (normalized.product_name or "").strip()
        normalized.total_quantity = max(0, int(normalized.total_quantity))
        normalized.base_price = float(normalized.base_price)
        normalized.missing_core_data = bool(normalized.missing_core_data)

        core_missing = (
            not normalized.product_name
            or normalized.total_quantity <= 0
            or normalized.base_price <= 0
        )
        if core_missing and not normalized.missing_core_data:
            raise ValueError("core fields are missing but response is not flagged missing_core_data")

        if normalized.missing_core_data and not (normalized.ai_question or "").strip():
            normalized.ai_question = "Please share product name, quantity and price."

        return normalized

    @staticmethod
    async def _extract_with_gemini(temp_filename: str, model: str) -> InventoryExtraction:
        def _run() -> InventoryExtraction:
            ai_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
            uploaded_file = ai_client.files.upload(file=temp_filename)
            try:
                prompt = (
                    "Analyze this media and extract inventory data. Return JSON using the schema exactly. "
                    "If product_name, total_quantity, or base_price cannot be determined, "
                    "set missing_core_data to true and provide a short ai_question."
                )
                response = ai_client.models.generate_content(
                    model=model,
                    contents=[uploaded_file, prompt],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=InventoryExtraction,
                    ),
                )
                if not response.parsed:
                    raise ValueError("gemini returned empty parsed payload")
                return response.parsed
            finally:
                ai_client.files.delete(name=uploaded_file.name)

        return await asyncio.to_thread(_run)

    @staticmethod
    async def _extract_with_openrouter(temp_filename: str, media_type: str, model: str) -> InventoryExtraction:
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY is not configured")

        with open(temp_filename, "rb") as f:
            media_bytes = f.read()
        b64_data = base64.b64encode(media_bytes).decode("utf-8")
        mime_type = "audio/ogg" if media_type == "audio" else "image/jpeg"
        prompt = (
            "Extract inventory data from the attached media and return JSON only with keys: "
            "product_name,total_quantity,base_price,brand,variants,missing_core_data,ai_question. "
            "Use missing_core_data=true if core fields are uncertain and include a short ai_question."
        )
        if media_type == "audio":
            raise ValueError("openrouter audio extraction is not enabled for this project")

        payload = {
            "model": model,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{b64_data}"},
                        },
                    ],
                }
            ],
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload)
            response.raise_for_status()
            body = response.json()
        content = body["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        return InventoryExtraction.model_validate(parsed)

    @staticmethod
    async def _extract_from_provider(provider: str, temp_filename: str, media_type: str, model: str) -> InventoryExtraction:
        if provider == "gemini":
            return await AIService._extract_with_gemini(temp_filename=temp_filename, model=model)
        if provider == "openrouter":
            return await AIService._extract_with_openrouter(
                temp_filename=temp_filename,
                media_type=media_type,
                model=model,
            )
        raise ValueError(f"unsupported provider: {provider}")

    @staticmethod
    async def process_whatsapp_media(media_id: str, media_type: str) -> InventoryExtraction:
        AIService._logger.info("ai_media_start media_id=%s media_type=%s", media_id, media_type)

        token = os.getenv("WHATSAPP_ACCESS_TOKEN")
        if not token:
            raise Exception("Missing WHATSAPP_ACCESS_TOKEN")

        # ── Step 1: Download media bytes from Meta ────────────────────────────
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(timeout=30) as client:
            meta_resp = await client.get(f"https://graph.facebook.com/v18.0/{media_id}", headers=headers)
            media_data = meta_resp.json()
            media_url = media_data.get("url")
            if not media_url:
                raise Exception(f"Failed to get media URL. Meta response: {media_data}")
            file_resp = await client.get(media_url, headers=headers)
            media_bytes = file_resp.content

        # ── Step 2: Route through the cascade ────────────────────────────────
        if media_type == "audio":
            # Audio path: Groq Whisper → OpenAI Whisper → AssemblyAI
            filename = f"{media_id}.ogg"
            transcript = await transcribe_audio(media_bytes, filename=filename)
            AIService._logger.info("ai_audio_transcript_ok chars=%s", len(transcript))

            # Now extract inventory data from the transcript text via Gemini
            return await AIService._extract_inventory_from_text(transcript)

        else:
            # Image path: Gemini → Groq → OpenRouter
            prompt = (
                "Analyze this image and extract inventory data. Return JSON using the schema exactly. "
                "Required fields: product_name, total_quantity, base_price, brand, variants, "
                "missing_core_data, ai_question. "
                "If product_name, total_quantity, or base_price cannot be determined, "
                "set missing_core_data to true and provide a short ai_question."
            )
            raw_json_str = await extract_image_text(media_bytes, prompt)
            try:
                parsed = json.loads(raw_json_str)
                extraction = InventoryExtraction.model_validate(parsed)
            except (json.JSONDecodeError, ValidationError) as e:
                AIService._logger.warning("ai_image_parse_error reason=%s raw=%s", e, raw_json_str[:300])
                extraction = InventoryExtraction(
                    product_name="", total_quantity=0, base_price=0,
                    missing_core_data=True,
                    ai_question="Could not parse the image. Please describe the product, quantity, and price."
                )
            return AIService._validate_extraction(extraction)

    @staticmethod
    async def _download_media_bytes(media_id: str) -> bytes:
        """Download raw media bytes from Meta's CDN. Shared by all process_media_for_* methods."""
        token = os.getenv("WHATSAPP_ACCESS_TOKEN")
        if not token:
            raise Exception("Missing WHATSAPP_ACCESS_TOKEN")
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(timeout=30) as client:
            meta_resp = await client.get(
                f"https://graph.facebook.com/v18.0/{media_id}", headers=headers
            )
            media_data = meta_resp.json()
            media_url = media_data.get("url")
            if not media_url:
                raise Exception(f"Failed to get media URL. Meta response: {media_data}")
            file_resp = await client.get(media_url, headers=headers)
            return file_resp.content

    # ── Mode-specific public entry points ─────────────────────────────────────

    @staticmethod
    async def process_media_for_inventory(media_id: str, media_type: str) -> InventoryExtraction:
        """Download media and extract inventory product data."""
        AIService._logger.info("media_inventory media_id=%s type=%s", media_id, media_type)
        media_bytes = await AIService._download_media_bytes(media_id)
        if media_type == "audio":
            transcript = await transcribe_audio(media_bytes, filename=f"{media_id}.ogg")
            AIService._logger.info("inventory_transcript chars=%s", len(transcript))
            return await AIService._extract_inventory_from_text(transcript)
        prompt = (
            "Analyze this image and extract inventory data. Return JSON using the schema exactly. "
            "Required fields: product_name, total_quantity, base_price, brand, variants, "
            "missing_core_data, ai_question. "
            "If product_name, total_quantity, or base_price cannot be determined, "
            "set missing_core_data to true and provide a short ai_question."
        )
        raw = await extract_image_text(media_bytes, prompt)
        try:
            extraction = InventoryExtraction.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as e:
            AIService._logger.warning("inventory_image_parse_error reason=%s", e)
            extraction = InventoryExtraction(
                product_name="", total_quantity=0, base_price=0,
                missing_core_data=True,
                ai_question="Could not read the image. Please describe the product, quantity, and price.",
            )
        return AIService._validate_extraction(extraction)

    @staticmethod
    async def process_media_for_invoice(media_id: str, media_type: str) -> InvoiceExtraction:
        """Download media and extract invoice / sales data."""
        AIService._logger.info("media_invoice media_id=%s type=%s", media_id, media_type)
        media_bytes = await AIService._download_media_bytes(media_id)
        if media_type == "audio":
            transcript = await transcribe_audio(media_bytes, filename=f"{media_id}.ogg")
            AIService._logger.info("invoice_transcript chars=%s", len(transcript))
            return await AIService.extract_invoice_from_text(transcript)
        # Image: extract raw text then parse as invoice
        prompt = (
            "This is an invoice or receipt image. Extract sale data and return strict JSON. "
            "Required fields: customer_name, items (product_name, qty, unit_price), "
            "paid_amount, discount, tax, missing_core_data, ai_question. "
            "If items cannot be read, set missing_core_data=true."
        )
        raw = await extract_image_text(media_bytes, prompt)
        try:
            extraction = InvoiceExtraction.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as e:
            AIService._logger.warning("invoice_image_parse_error reason=%s", e)
            extraction = InvoiceExtraction(
                missing_core_data=True,
                ai_question="Could not read the invoice image. Please describe the items and amounts as text.",
            )
        if not extraction.items:
            extraction.missing_core_data = True
            extraction.ai_question = extraction.ai_question or "Please confirm the items sold and their quantities."
        return extraction

    @staticmethod
    async def process_media_for_ledger(media_id: str, media_type: str) -> LedgerExtraction:
        """Download media and extract a cash ledger entry (expense / deposit / withdrawal)."""
        AIService._logger.info("media_ledger media_id=%s type=%s", media_id, media_type)
        media_bytes = await AIService._download_media_bytes(media_id)
        if media_type == "audio":
            transcript = await transcribe_audio(media_bytes, filename=f"{media_id}.ogg")
            AIService._logger.info("ledger_transcript chars=%s", len(transcript))
            return await AIService.extract_ledger_from_text(transcript)
        # Image: read text from receipt/bill then extract ledger entry
        prompt = (
            "This is a receipt, bill, or expense note. Extract the cash ledger entry and return strict JSON. "
            "Fields: entry_type (expense | cash_deposit_bank | cash_withdraw_bank), "
            "amount (number > 0), note (short description), "
            "missing_core_data (true if entry_type or amount unclear), ai_question."
        )
        raw = await extract_image_text(media_bytes, prompt)
        try:
            extraction = LedgerExtraction.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as e:
            AIService._logger.warning("ledger_image_parse_error reason=%s", e)
            extraction = LedgerExtraction(
                entry_type="expense", amount=0,
                missing_core_data=True,
                ai_question="Could not read the image. Is this an expense, bank deposit, or withdrawal? What amount?",
            )
        return extraction

    @staticmethod
    async def extract_ledger_from_text(text: str) -> LedgerExtraction:
        """Use Gemini to extract a structured ledger entry from free text or transcript."""
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY not set")
        ai_client = genai.Client(api_key=api_key)
        prompt = (
            f"The user said: '{text}'\n"
            "Extract a cash ledger entry and return strict JSON. "
            "entry_type must be one of: expense, cash_deposit_bank, cash_withdraw_bank. "
            "amount must be a positive number. "
            "Set missing_core_data=true and provide ai_question if entry_type or amount is unclear."
        )
        response = ai_client.models.generate_content(
            model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=LedgerExtraction,
            ),
        )
        return response.parsed

    # ── Legacy shim — kept for backward compat, routes to inventory ───────────
    @staticmethod
    async def process_whatsapp_media(media_id: str, media_type: str) -> InventoryExtraction:
        return await AIService.process_media_for_inventory(media_id, media_type)

    @staticmethod
    async def correct_extraction(previous_data: dict, user_correction: str) -> InventoryExtraction:
        ai_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

        prompt = (
            f"Here is the currently extracted inventory data: {previous_data}\n"
            f"The user provided this correction: '{user_correction}'\n"
            f"Apply this correction to the data and return the updated JSON."
        )

        response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=InventoryExtraction,
            ),
        )

        return response.parsed

    @staticmethod
    async def extract_invoice_from_text(user_text: str) -> InvoiceExtraction:
        ai_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        prompt = (
            "Extract invoice data from this user message and return strict JSON. "
            "Required: items[].product_name and items[].qty. "
            "If missing, set missing_core_data=true and ask follow-up in ai_question. "
            f"Message: {user_text}"
        )
        response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=InvoiceExtraction,
            ),
        )
        parsed = response.parsed
        if not parsed.items:
            parsed.missing_core_data = True
            parsed.ai_question = parsed.ai_question or "Please send at least one sold item and quantity."
        return parsed