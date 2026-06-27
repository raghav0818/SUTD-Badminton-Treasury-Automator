"""Treasurer-only handlers for admin roster, treasurer handover, and relinks."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from clubbot import admin, db, ops

NOT_TREASURER_CALLBACK = "Only the treasurer can do this."


def _confirm_keyboard(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Confirm", callback_data=f"{prefix}:confirm"),
                InlineKeyboardButton("Cancel", callback_data=f"{prefix}:cancel"),
            ]
        ]
    )


async def cmd_addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = admin._db(context)
    if not admin._is_treasurer(conn, update.effective_user.id):
        await update.message.reply_text(admin.NOT_TREASURER)
        return
    member = await admin._resolve_member(update, context)
    if member is None:
        return
    member_id = member["telegram_user_id"]
    if db.get_role(conn, member_id) is not None:
        await update.message.reply_text(
            f"{member['full_name']} is already an admin or the treasurer."
        )
        return
    db.add_admin(conn, telegram_user_id=member_id, added_by=update.effective_user.id)
    await update.message.reply_text(f"Added {member['full_name']} as an admin.")
    await context.bot.send_message(
        chat_id=member_id, text="You are now a club admin for the badminton bot."
    )


async def cmd_removeadmin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = admin._db(context)
    if not admin._is_treasurer(conn, update.effective_user.id):
        await update.message.reply_text(admin.NOT_TREASURER)
        return
    member = await admin._resolve_member(update, context)
    if member is None:
        return
    member_id = member["telegram_user_id"]
    if db.get_role(conn, member_id) == "treasurer":
        await update.message.reply_text(
            "You cannot remove the treasurer. Use /transfertreasurer instead."
        )
        return
    if db.remove_admin(conn, member_id):
        await update.message.reply_text(
            f"Removed {member['full_name']}'s admin access."
        )
    else:
        await update.message.reply_text(f"{member['full_name']} is not an admin.")


async def cmd_transfertreasurer(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    conn = admin._db(context)
    caller_id = update.effective_user.id
    if not admin._is_treasurer(conn, caller_id):
        await update.message.reply_text(admin.NOT_TREASURER)
        return
    member = await admin._resolve_member(update, context)
    if member is None:
        return
    member_id = member["telegram_user_id"]
    if db.get_role(conn, member_id) == "treasurer":
        await update.message.reply_text(f"{member['full_name']} is already the treasurer.")
        return
    context.bot_data.setdefault("pending_transfer", {})[caller_id] = {
        "new_id": member_id,
        "name": member["full_name"],
    }
    await update.message.reply_text(
        f"This hands full treasurer control to {member['full_name']}; "
        "you will become a regular admin.",
        reply_markup=_confirm_keyboard("transfer"),
    )


async def on_transfer_confirm(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    conn = admin._db(context)
    caller_id = update.effective_user.id
    if not admin._is_treasurer(conn, caller_id):
        await query.edit_message_text(NOT_TREASURER_CALLBACK)
        return
    pending = context.bot_data.get("pending_transfer", {}).pop(caller_id, None)
    if pending is None:
        await query.edit_message_text("No treasurer transfer is pending.")
        return
    if query.data == "transfer:cancel":
        await query.edit_message_text("Transfer cancelled.")
        return
    new_id = pending["new_id"]
    db.transfer_treasurer(conn, new_treasurer_id=new_id, added_by=caller_id)
    await query.edit_message_text(f"{pending['name']} is now the treasurer.")
    await context.bot.send_message(
        chat_id=new_id,
        text="You are now the club treasurer for the badminton bot.",
    )


async def cmd_relink(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = admin._db(context)
    caller_id = update.effective_user.id
    if not admin._is_treasurer(conn, caller_id):
        await update.message.reply_text(admin.NOT_TREASURER)
        return
    # _resolve_member reads context.args[0] as the SUTD ID and handles the
    # missing/unknown cases with the friendly replies we want here.
    member = await admin._resolve_member(update, context)
    if member is None:
        return
    sutd_id = member["sutd_id"]
    old_id = member["telegram_user_id"]
    if len(context.args) >= 2:
        try:
            new_id = int(context.args[1])
        except ValueError:
            await update.message.reply_text("The new Telegram ID must be a number.")
            return
        new_username = None
    else:
        request = db.get_relink_request(conn, sutd_id)
        if request is None:
            await update.message.reply_text(
                f"No relink request found for {sutd_id}. Ask the member to send "
                "/start from their new account first, or pass the new Telegram ID "
                "explicitly."
            )
            return
        new_id = request["new_telegram_user_id"]
        new_username = request["new_username"]
    if new_id == old_id:
        await update.message.reply_text("That is already this member's account.")
        return
    if db.get_member(conn, new_id) is not None:
        await update.message.reply_text(
            "That Telegram account already belongs to another member."
        )
        return
    context.bot_data.setdefault("pending_relink", {})[caller_id] = {
        "sutd_id": sutd_id,
        "old_id": old_id,
        "new_id": new_id,
        "new_username": new_username,
    }
    await update.message.reply_text(
        f"Relink SUTD {sutd_id} from account {old_id} to {new_id}?",
        reply_markup=_confirm_keyboard("relink"),
    )


async def on_relink_confirm(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    conn = admin._db(context)
    caller_id = update.effective_user.id
    if not admin._is_treasurer(conn, caller_id):
        await query.edit_message_text(NOT_TREASURER_CALLBACK)
        return
    pending = context.bot_data.get("pending_relink", {}).pop(caller_id, None)
    if pending is None:
        await query.edit_message_text("No relink is pending.")
        return
    if query.data == "relink:cancel":
        await query.edit_message_text("Relink cancelled.")
        return
    db.reassign_member_telegram_id(
        conn,
        old_id=pending["old_id"],
        new_id=pending["new_id"],
        new_username=pending["new_username"],
    )
    db.delete_relink_request(conn, pending["sutd_id"])
    ops.mark_dirty(context)
    await query.edit_message_text(
        f"Relinked SUTD {pending['sutd_id']} to the new account."
    )
    await context.bot.send_message(
        chat_id=pending["new_id"],
        text="Your membership has been relinked to this Telegram account.",
    )
