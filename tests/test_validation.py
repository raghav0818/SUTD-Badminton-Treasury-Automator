from clubbot import validation


def test_name_normalizes_whitespace():
    assert validation.normalize_full_name("  Alice   Tan ") == "Alice Tan"


def test_name_rejects_garbage():
    assert validation.normalize_full_name("a") is None          # too short
    assert validation.normalize_full_name("12345") is None      # no letters
    assert validation.normalize_full_name("x" * 81) is None     # too long


def test_name_allows_non_ascii():
    assert validation.normalize_full_name("陈伟明") == "陈伟明"


def test_sutd_id_accepts_seven_digits():
    assert validation.normalize_sutd_id(" 1010654 ") == "1010654"


def test_sutd_id_rejects_bad_input():
    assert validation.normalize_sutd_id("101065") is None       # 6 digits
    assert validation.normalize_sutd_id("10106545") is None     # 8 digits
    assert validation.normalize_sutd_id("abcdefg") is None      # not digits
    assert validation.normalize_sutd_id("1007654") is None      # wrong prefix
