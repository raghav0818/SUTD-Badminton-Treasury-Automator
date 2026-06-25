import asyncio
from unittest.mock import AsyncMock, MagicMock

from clubbot.gemini import GeminiExtractor


def test_gemini_adapter_requests_structured_image_extraction():
    response = MagicMock()
    response.parsed = {
        "readable": True,
        "is_success_screen": True,
        "amount_cents": 5,
        "recipient": "Singapore University of Technology and Design",
        "billing_id": "200913519CSL5EIU616138169",
        "payment_timestamp": "2026-06-20T10:38:00+08:00",
        "transaction_id": "TX1",
    }
    client = MagicMock()
    client.aio.models.generate_content = AsyncMock(return_value=response)
    extractor = GeminiExtractor("", client=client)

    result = asyncio.run(extractor.extract(b"image", "image/png"))

    assert result.amount_cents == 5
    call = client.aio.models.generate_content.call_args.kwargs
    assert call["config"]["response_mime_type"] == "application/json"
    assert call["config"]["temperature"] == 0
    assert call["contents"][0].inline_data.mime_type == "image/png"
