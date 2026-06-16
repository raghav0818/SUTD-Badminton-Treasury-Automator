from clubbot import paynow, phase0


def test_variant_a_keeps_school_bill_number_and_adds_reference_label():
    payload = phase0.variant_a_payload()
    assert paynow.verify_crc(payload)
    root = paynow.parse_tlv(payload)
    extra = paynow.parse_tlv(root["62"])
    assert extra["01"] == phase0.SCHOOL_BILL_NUMBER
    assert extra["05"] == "BDMTEST01"
    assert root["54"] == "0.10"
    assert paynow.parse_tlv(root["26"])["03"] == "0"  # amount locked


def test_variant_b_replaces_bill_number_with_our_code():
    payload = phase0.variant_b_payload()
    assert paynow.verify_crc(payload)
    root = paynow.parse_tlv(payload)
    extra = paynow.parse_tlv(root["62"])
    assert extra["01"] == "BDMTEST02"
    assert "05" not in extra
    assert root["54"] == "0.10"
    assert paynow.parse_tlv(root["26"])["02"] == phase0.SCHOOL_UEN
