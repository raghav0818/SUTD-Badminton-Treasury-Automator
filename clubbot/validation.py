"""Validators for registration input. Permissive on names (non-ASCII names
exist in the club), strict on SUTD IDs (7 digits)."""

import re

_SUTD_ID_RE = re.compile(r"\d{7}")


def normalize_full_name(text: str) -> str | None:
    name = " ".join(text.split())
    if not 2 <= len(name) <= 80:
        return None
    if not any(ch.isalpha() for ch in name):
        return None
    return name


def normalize_sutd_id(text: str) -> str | None:
    sutd_id = text.strip()
    return sutd_id if _SUTD_ID_RE.fullmatch(sutd_id) else None
