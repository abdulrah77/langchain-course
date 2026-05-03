"""
services/ai_router.py

Centralised AI provider cascade. Handles:
  - Vision  (image → text): Gemini Flash → Groq LLaMA-Vision → OpenRouter GPT-4o-mini
  - Audio   (voice → text): Groq Whisper → OpenAI Whisper → AssemblyAI

A 429 / quota error on any provider automatically falls through to the next one.
All keys are read from environment variables — add only the ones you have.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
from typing import Callable, Coroutine, Any

import httpx

logger = logging.getLogger("ice_breaker.ai_router")

# ──────────────────────────────────────────────────────────────────────────────
# Sentinel for "quota / rate-limit exhausted" — triggers the next provider
# ──────────────────────────────────────────────────────────────────────────────
class QuotaExhaustedError(Exception):
    """Raised when an AI provider returns a quota / rate-limit error."""


# ──────────────────────────────────────────────────────────────────────────────
# Vision cascade:  image bytes → extracted text / JSON string
# ──────────────────────────────────────────────────────────────────────────────

async def _vision_gemini(image_bytes: bytes, prompt: str) -> str:
    """Use Gemini Flash (free tier: 1,500 req/day, 15 req/min)."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise QuotaExhaustedError("GEMINI_API_KEY not set")

    b64 = base64.b64encode(image_bytes).decode()
    payload = {
        "contents": [
            {
                "parts": [
                    {"inline_data": {"mime_type": "image/jpeg", "data": b64}},
                    {"text": prompt},
                ]
            }
        ],
        "generationConfig": {"response_mime_type": "application/json"},
    }
    model = os.getenv("GEMINI_VISION_MODEL", "gemini-1.5-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=payload)

    if resp.status_code == 429:
        raise QuotaExhaustedError(f"Gemini vision quota hit: {resp.text[:200]}")
    resp.raise_for_status()
    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


async def _vision_groq(image_bytes: bytes, prompt: str) -> str:
    """Use Groq LLaMA Vision (free: generous daily quota)."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise QuotaExhaustedError("GROQ_API_KEY not set")

    b64 = base64.b64encode(image_bytes).decode()
    model = os.getenv("GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            }
        ],
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload)

    if resp.status_code == 429:
        raise QuotaExhaustedError(f"Groq vision quota hit: {resp.text[:200]}")
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


async def _vision_openrouter(image_bytes: bytes, prompt: str) -> str:
    """Use OpenRouter (GPT-4o-mini) as the final vision fallback."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise QuotaExhaustedError("OPENROUTER_API_KEY not set")

    b64 = base64.b64encode(image_bytes).decode()
    model = os.getenv("OPENROUTER_VISION_MODEL", "openai/gpt-4o-mini")
    payload = {
        "model": model,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            }
        ],
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload)

    if resp.status_code == 429:
        raise QuotaExhaustedError(f"OpenRouter vision quota hit: {resp.text[:200]}")
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


# ──────────────────────────────────────────────────────────────────────────────
# Audio cascade:  audio bytes → transcript string
# ──────────────────────────────────────────────────────────────────────────────

async def _audio_groq(audio_bytes: bytes, filename: str = "audio.ogg") -> str:
    """Use Groq Whisper (free: 7,200 req/day — best primary choice)."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise QuotaExhaustedError("GROQ_API_KEY not set")

    model = os.getenv("GROQ_AUDIO_MODEL", "whisper-large-v3-turbo")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            data={"model": model, "language": "en"},
            files={"file": (filename, audio_bytes, "audio/ogg")},
        )

    if resp.status_code == 429:
        raise QuotaExhaustedError(f"Groq audio quota hit: {resp.text[:200]}")
    resp.raise_for_status()
    return resp.json()["text"]


async def _audio_openai(audio_bytes: bytes, filename: str = "audio.ogg") -> str:
    """Use OpenAI Whisper as the second fallback."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise QuotaExhaustedError("OPENAI_API_KEY not set")

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            data={"model": "whisper-1"},
            files={"file": (filename, audio_bytes, "audio/ogg")},
        )

    if resp.status_code == 429:
        raise QuotaExhaustedError(f"OpenAI audio quota hit: {resp.text[:200]}")
    resp.raise_for_status()
    return resp.json()["text"]


async def _audio_assemblyai(audio_bytes: bytes, filename: str = "audio.ogg") -> str:
    """Use AssemblyAI as the final audio fallback (100 hours free on signup)."""
    api_key = os.getenv("ASSEMBLYAI_API_KEY")
    if not api_key:
        raise QuotaExhaustedError("ASSEMBLYAI_API_KEY not set")

    headers = {"authorization": api_key}
    async with httpx.AsyncClient(timeout=60) as client:
        # Upload
        upload_resp = await client.post(
            "https://api.assemblyai.com/v2/upload",
            headers=headers,
            content=audio_bytes,
        )
        if upload_resp.status_code == 429:
            raise QuotaExhaustedError(f"AssemblyAI quota hit: {upload_resp.text[:200]}")
        upload_resp.raise_for_status()
        audio_url = upload_resp.json()["upload_url"]

        # Submit
        sub_resp = await client.post(
            "https://api.assemblyai.com/v2/transcript",
            headers=headers,
            json={"audio_url": audio_url},
        )
        sub_resp.raise_for_status()
        transcript_id = sub_resp.json()["id"]

        # Poll
        for _ in range(30):
            await asyncio.sleep(3)
            poll = await client.get(f"https://api.assemblyai.com/v2/transcript/{transcript_id}", headers=headers)
            poll.raise_for_status()
            result = poll.json()
            if result["status"] == "completed":
                return result["text"]
            if result["status"] == "error":
                raise ValueError(f"AssemblyAI transcription error: {result.get('error')}")

    raise TimeoutError("AssemblyAI transcription timed out after 90 seconds")


# ──────────────────────────────────────────────────────────────────────────────
# Public cascade runners
# ──────────────────────────────────────────────────────────────────────────────

_VISION_CASCADE: list[Callable[..., Coroutine[Any, Any, str]]] = [
    _vision_gemini,
    _vision_groq,
    _vision_openrouter,
]

_AUDIO_CASCADE: list[Callable[..., Coroutine[Any, Any, str]]] = [
    _audio_groq,
    _audio_openai,
    _audio_assemblyai,
]


async def extract_image_text(image_bytes: bytes, prompt: str) -> str:
    """
    Run the vision cascade: Gemini → Groq → OpenRouter.
    Returns raw JSON/text string from the first succeeding provider.
    Raises RuntimeError if all providers fail or are unconfigured.
    """
    last_err: Exception | None = None
    for fn in _VISION_CASCADE:
        provider = fn.__name__.replace("_vision_", "").upper()
        try:
            result = await fn(image_bytes, prompt)
            logger.info("ai_router vision_ok provider=%s", provider)
            return result
        except QuotaExhaustedError as e:
            logger.warning("ai_router vision_quota provider=%s reason=%s", provider, e)
            last_err = e
        except Exception as e:
            logger.warning("ai_router vision_error provider=%s reason=%s", provider, e)
            last_err = e

    raise RuntimeError(f"All vision providers exhausted. Last error: {last_err}")


async def transcribe_audio(audio_bytes: bytes, filename: str = "audio.ogg") -> str:
    """
    Run the audio cascade: Groq Whisper → OpenAI Whisper → AssemblyAI.
    Returns the transcript string from the first succeeding provider.
    Raises RuntimeError if all providers fail or are unconfigured.
    """
    last_err: Exception | None = None
    for fn in _AUDIO_CASCADE:
        provider = fn.__name__.replace("_audio_", "").upper()
        try:
            result = await fn(audio_bytes, filename)
            logger.info("ai_router audio_ok provider=%s", provider)
            return result
        except QuotaExhaustedError as e:
            logger.warning("ai_router audio_quota provider=%s reason=%s", provider, e)
            last_err = e
        except Exception as e:
            logger.warning("ai_router audio_error provider=%s reason=%s", provider, e)
            last_err = e

    raise RuntimeError(f"All audio providers exhausted. Last error: {last_err}")
