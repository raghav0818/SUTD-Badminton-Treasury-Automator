from decimal import Decimal

import pytest

from clubbot import paynow

SCHOOL_PAYLOAD = "00020101021126520009SG.PAYNOW010120213200913519CSL5030110408299912315204000053037025802SG5925SINGAPORE UNIVERSITY OF T6002SG62290125200913519CSL5EIU6161381696304432B"


def test_crc_of_school_qr():
    assert paynow.crc16_ccitt(SCHOOL_PAYLOAD[:-4]) == "432B"


def test_verify_crc():
    assert paynow.verify_crc(SCHOOL_PAYLOAD)
    assert not paynow.verify_crc(SCHOOL_PAYLOAD[:-1] + "C")


def test_school_qr_reencodes_byte_identically():
    rebuilt = paynow.build_payload(
        uen="200913519CSL5",
        merchant_name="SINGAPORE UNIVERSITY OF T",
        editable_amount=True,
        bill_number="200913519CSL5EIU616138169",
    )
    assert rebuilt == SCHOOL_PAYLOAD


def test_parse_tlv_school_qr():
    root = paynow.parse_tlv(SCHOOL_PAYLOAD)
    assert root["00"] == "01"
    assert root["01"] == "11"
    account = paynow.parse_tlv(root["26"])
    assert account["00"] == "SG.PAYNOW"
    assert account["01"] == "2"
    assert account["02"] == "200913519CSL5"
    assert account["03"] == "1"
    assert account["04"] == "29991231"
    assert root["59"] == "SINGAPORE UNIVERSITY OF T"
    assert paynow.parse_tlv(root["62"])["01"] == "200913519CSL5EIU616138169"


def test_dynamic_payload_with_fixed_amount():
    payload = paynow.build_payload(
        uen="200913519CSL5",
        merchant_name="SINGAPORE UNIVERSITY OF T",
        amount=Decimal("12.50"),
        bill_number="BDM-T5-047",
    )
    assert paynow.verify_crc(payload)
    root = paynow.parse_tlv(payload)
    assert root["01"] == "12"  # dynamic QR
    assert root["54"] == "12.50"
    account = paynow.parse_tlv(root["26"])
    assert account["03"] == "0"  # payer cannot edit the amount
    assert paynow.parse_tlv(root["62"])["01"] == "BDM-T5-047"


def test_reference_label_subfield():
    payload = paynow.build_payload(
        uen="200913519CSL5",
        merchant_name="SINGAPORE UNIVERSITY OF T",
        amount=Decimal("0.10"),
        bill_number="200913519CSL5EIU616138169",
        reference_label="BDMTEST01",
    )
    extra = paynow.parse_tlv(paynow.parse_tlv(payload)["62"])
    assert extra["01"] == "200913519CSL5EIU616138169"
    assert extra["05"] == "BDMTEST01"


def test_invalid_inputs_rejected():
    with pytest.raises(ValueError):
        paynow.build_payload(uen="X", merchant_name="Y")  # no amount and not editable
    with pytest.raises(ValueError):
        paynow.build_payload(
            uen="X", merchant_name="Y", amount=Decimal("1.00"), editable_amount=True
        )
    with pytest.raises(ValueError):
        paynow.build_payload(uen="X", merchant_name="Y", amount=Decimal("0.00"))
    with pytest.raises(ValueError):
        paynow.tlv("01", "")
    with pytest.raises(ValueError):
        paynow.tlv("01", "x" * 100)
