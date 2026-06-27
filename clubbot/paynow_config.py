"""Treasurer-editable PayNow routing/verification settings (PRD §7.3).

The school's verified Phase 0 constants in :mod:`clubbot.payments` are the
defaults; the treasurer can override any of them via ``/settings`` so the bot
can be re-pointed at a new DBS FLYMAX account without a code change. The
routing-critical keys (the UEN and Billing ID that decide whether money reaches
DBS FLYMAX) require an explicit Confirm/Cancel step before they are applied.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass, replace
from decimal import Decimal

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from clubbot import db, paynow, payments

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PayNowConfig:
    uen: str
    merchant_name: str
    bill_number: str
    recipient_match: str


# settings-table key -> PayNowConfig field
SETTING_KEYS = {
    "paynow_uen": "uen",
    "merchant_name": "merchant_name",
    "bill_number": "bill_number",
    "recipient_match": "recipient_match",
}
EDITABLE_KEYS = set(SETTING_KEYS)
ROUTING_CRITICAL = {"paynow_uen", "bill_number"}
# Keys whose value is embedded in the QR payload and must form a valid EMVCo/
# PayNow field (ASCII, length-bounded). recipient_match is verify-only, so it is
# not in this set.
QR_PAYLOAD_KEYS = {"paynow_uen", "merchant_name", "bill_number"}

# settings-table key -> default drawn from the verified school constants.
_DEFAULTS = {
    "paynow_uen": payments.SCHOOL_UEN,
    "merchant_name": payments.SCHOOL_MERCHANT_NAME,
    "bill_number": payments.SCHOOL_BILL_NUMBER,
    "recipient_match": payments.DEFAULT_RECIPIENT_MATCH,
}

NOT_ADMIN = "This command is for club admins."
NOT_TREASURER = "Only the treasurer can change settings."


def _db(context: ContextTypes.DEFAULT_TYPE) -> sqlite3.Connection:
    return context.bot_data["db"]


def get_paynow_config(conn: sqlite3.Connection) -> PayNowConfig:
    """Effective config: each stored override, else the school default."""
    values = {
        field: db.get_setting(conn, key) or _DEFAULTS[key]
        for key, field in SETTING_KEYS.items()
    }
    return PayNowConfig(**values)


def set_value(conn: sqlite3.Connection, key: str, value: str) -> str:
    """Validate, normalise, and persist one setting; return the stored value.

    `recipient_match` is stored as uppercase alphanumerics only, because the
    verifier substring-matches it against the receipt recipient after the same
    normalisation (see :func:`clubbot.payments.verify_extracted_payment`).
    """
    if key not in EDITABLE_KEYS:
        raise ValueError(f"unknown setting: {key}")
    if not value.strip():
        raise ValueError("value cannot be empty")
    if key == "recipient_match":
        stored = re.sub(r"[^A-Z0-9]", "", value.upper())
        if not stored:
            raise ValueError("value has no alphanumeric characters")
    else:
        stored = value.strip()
    if key in QR_PAYLOAD_KEYS:
        # Reject anything the EMVCo/PayNow builder cannot encode (non-ASCII,
        # too long): a bad routing value would otherwise break every future QR.
        candidate = replace(get_paynow_config(conn), **{SETTING_KEYS[key]: stored})
        try:
            paynow.build_payload(
                uen=candidate.uen,
                merchant_name=candidate.merchant_name,
                amount=Decimal("0.05"),
                bill_number=candidate.bill_number,
                reference_label="BDMTEST",
            )
        except Exception as exc:
            raise ValueError(f"not a valid PayNow {key}: {exc}") from exc
    db.set_setting(conn, key, stored)
    return stored


def _format_config(conn: sqlite3.Connection) -> str:
    config = get_paynow_config(conn)
    lines = ["Current PayNow settings:"]
    for key, field in SETTING_KEYS.items():
        lines.append(f"- {key}: {getattr(config, field)}")
    lines.append("")
    lines.append("To change one: /settings set <key> <value>")
    lines.append("Editable keys: " + ", ".join(SETTING_KEYS))
    return "\n".join(lines)


def _usage() -> str:
    return (
        "Usage: /settings set <key> <value>\n"
        "Editable keys: " + ", ".join(SETTING_KEYS)
    )


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = _db(context)
    uid = update.effective_user.id
    role = db.get_role(conn, uid)
    if role not in ("treasurer", "admin"):
        await update.message.reply_text(NOT_ADMIN)
        return

    # Anything that is not an explicit "set" is treated as a read-only view.
    if not context.args or context.args[0] != "set":
        await update.message.reply_text(_format_config(conn))
        return

    if role != "treasurer":
        await update.message.reply_text(NOT_TREASURER)
        return

    key = context.args[1] if len(context.args) > 1 else ""
    value = " ".join(context.args[2:])
    if not key or not value.strip() or key not in EDITABLE_KEYS:
        await update.message.reply_text(_usage())
        return

    if key in ROUTING_CRITICAL:
        # Stash and require confirmation: a wrong UEN/Billing ID silently breaks
        # the Phase 0 routing that delivers money to DBS FLYMAX.
        context.bot_data.setdefault("pending_settings", {})[uid] = {
            "key": key,
            "value": value,
        }
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Confirm", callback_data="settings:confirm"),
                    InlineKeyboardButton("Cancel", callback_data="settings:cancel"),
                ]
            ]
        )
        await update.message.reply_text(
            "WARNING (Phase 0 routing): Changing this can stop payments reaching "
            "DBS FLYMAX.\n"
            f"Proposed change: {key} -> {value}\n"
            "Confirm to apply, or Cancel to keep the current value.",
            reply_markup=keyboard,
        )
        return

    try:
        stored = set_value(conn, key, value)
    except ValueError as exc:
        await update.message.reply_text(f"Could not update {key}: {exc}")
        return
    note = ""
    if key == "merchant_name":
        # The receipt check uses recipient_match (the bank's registered name),
        # which is independent of this QR label — remind the treasurer.
        note = "\nNote: receipt verification uses 'recipient_match', not this."
    await update.message.reply_text(f"Updated {key} to: {stored}{note}")


async def on_settings_confirm(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    conn = _db(context)
    if db.get_role(conn, update.effective_user.id) != "treasurer":
        await query.edit_message_text(NOT_TREASURER)
        return

    pending = context.bot_data.get("pending_settings", {})
    change = pending.pop(update.effective_user.id, None)
    if change is None:
        await query.edit_message_text("No settings change is pending.")
        return

    if query.data == "settings:cancel":
        await query.edit_message_text("Settings change cancelled.")
        return

    try:
        stored = set_value(conn, change["key"], change["value"])
    except ValueError as exc:
        await query.edit_message_text(
            f"Could not update {change['key']}: {exc}"
        )
        return
    await query.edit_message_text(f"Updated {change['key']} to: {stored}")
