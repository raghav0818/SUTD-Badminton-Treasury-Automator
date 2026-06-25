"""Gemini-backed extraction of payment fields from a bank receipt image."""

from __future__ import annotations

from typing import Any

from clubbot.payments import ExtractedPayment

PROMPT = """Read this Singapore bank payment screenshot.
Return only the requested structured fields.
- readable: whether the important receipt text can be read
- is_success_screen: true only if payment is completed/successful, not a preview
- amount_cents: SGD amount as integer cents, or null
- recipient: displayed recipient/merchant name, or null
- billing_id: the UEN/bill reference number, or null
- payment_timestamp: ISO 8601 date and time including timezone offset, for example
  2026-06-20T10:38:00+08:00, or null. Singapore bank times use +08:00.
- transaction_id: the bank's transaction/reference number, or null
Do not infer missing values."""

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "readable": {"type": "boolean"},
        "is_success_screen": {"type": "boolean"},
        "amount_cents": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
        "recipient": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "billing_id": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "payment_timestamp": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "transaction_id": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    },
    "required": [
        "readable",
        "is_success_screen",
        "amount_cents",
        "recipient",
        "billing_id",
        "payment_timestamp",
        "transaction_id",
    ],
    "additionalProperties": False,
}


class GeminiExtractor:
    """Small adapter so tests and future model providers can replace Gemini."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "gemini-2.5-flash",
        client: Any | None = None,
    ) -> None:
        if not api_key and client is None:
            raise ValueError("Gemini API key is required")
        if client is None:
            from google import genai

            client = genai.Client(api_key=api_key)
        self._client = client
        self._model = model

    async def extract(
        self, image_bytes: bytes, mime_type: str
    ) -> ExtractedPayment:
        from google.genai import types

        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                PROMPT,
            ],
            config={
                "response_mime_type": "application/json",
                "response_json_schema": RESPONSE_SCHEMA,
                "temperature": 0,
            },
        )
        data = response.parsed
        if not isinstance(data, dict):
            raise ValueError("Gemini returned no structured payment data")
        return ExtractedPayment(**data)
