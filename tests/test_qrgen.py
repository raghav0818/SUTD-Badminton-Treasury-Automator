import io

import zxingcpp
from PIL import Image

from clubbot import paynow, qrgen


def _school_like_payload() -> str:
    return paynow.build_payload(
        uen="200913519CSL5",
        merchant_name="SINGAPORE UNIVERSITY OF T",
        editable_amount=True,
        bill_number="200913519CSL5EIU616138169",
    )


def test_render_png_magic_bytes():
    png = qrgen.render_png(_school_like_payload())
    assert png.startswith(b"\x89PNG\r\n\x1a\n")


def test_rendered_qr_decodes_back_to_payload():
    payload = _school_like_payload()
    img = Image.open(io.BytesIO(qrgen.render_png(payload)))
    results = zxingcpp.read_barcodes(img)
    assert len(results) == 1
    assert results[0].text == payload
