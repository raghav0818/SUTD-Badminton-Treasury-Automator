"""Gemini-backed extraction of payment fields from a bank receipt image."""

from __future__ import annotations

from typing import Any

from clubbot.payments import BankLine, ExtractedPayment

PROMPT = """Read this Singapore bank payment screenshot.
Return only the requested structured fields.
- readable: whether the important receipt text can be read
- is_success_screen: true only if payment is completed/successful, not a preview
- amount_cents: SGD amount as integer cents, or null
- recipient: displayed recipient/merchant name, or null
- billing_id: the bill reference the payment was made against (often labelled
  bill reference, reference, comments or "reference no. (optional)"), or null.
  Not the recipient's UEN/ID on its own.
- payment_timestamp: ISO 8601 date and time including timezone offset, for example
  2026-06-20T10:38:00+08:00, or null. Singapore bank times use +08:00.
- transaction_id: the bank's own unique transaction reference for this payment,
  or null. Not the bill reference and not the recipient's UEN/ID.
- reference_numbers: every ID, UEN, bill, reference and transaction number
  shown anywhere on the screenshot, each copied exactly as shown
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
        "reference_numbers": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "readable",
        "is_success_screen",
        "amount_cents",
        "recipient",
        "billing_id",
        "payment_timestamp",
        "transaction_id",
        "reference_numbers",
    ],
    "additionalProperties": False,
}


BANK_LIST_PROMPT = """This is a screenshot of the club treasurer's bank
collections app (DBS MAX / FLYMAX) listing payments RECEIVED.
Return one entry per payment row that is fully visible (skip rows cut off at
the top or bottom of the screen):
- amount_cents: SGD amount as integer cents
- paid_at: ISO 8601 with +08:00 if a time is shown, e.g. 2026-10-11T18:28:00+08:00;
  just the date (YYYY-MM-DD) if the row shows no time
- payer_name: the payer's name exactly as shown, or null
- transaction_id: the row's transaction/reference number, or null. Not the
  school's bill reference or UEN.
Do not infer missing values. Return an empty list if no rows are visible."""

BANK_LIST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "amount_cents": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
                    "paid_at": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "payer_name": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "transaction_id": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                },
                "required": ["amount_cents", "paid_at", "payer_name", "transaction_id"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["lines"],
    "additionalProperties": False,
}


class GeminiExtractor:
    """Small adapter so tests and future model providers can replace Gemini."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "gemini-3.1-flash-lite",
        client: Any | None = None,
    ) -> None:
        if not api_key and client is None:
            raise ValueError("Gemini API key is required")
        if client is None:
            from google import genai

            client = genai.Client(api_key=api_key)
        self._client = client
        self._model = model

    async def _ask(self, image_bytes: bytes, mime_type: str, prompt: str, schema) -> dict:
        from google.genai import types

        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                prompt,
            ],
            config={
                "response_mime_type": "application/json",
                "response_json_schema": schema,
                "temperature": 0,
            },
        )
        data = response.parsed
        if not isinstance(data, dict):
            raise ValueError("Gemini returned no structured data")
        return data

    async def extract(
        self, image_bytes: bytes, mime_type: str
    ) -> ExtractedPayment:
        data = await self._ask(image_bytes, mime_type, PROMPT, RESPONSE_SCHEMA)
        return ExtractedPayment(**data)

    async def extract_bank_lines(self, image_bytes: bytes, mime_type: str) -> list[BankLine]:
        data = await self._ask(image_bytes, mime_type, BANK_LIST_PROMPT, BANK_LIST_SCHEMA)
        return [BankLine(**line) for line in data.get("lines") or []]
