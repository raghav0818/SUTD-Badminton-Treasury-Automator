import asyncio
import sqlite3
from unittest.mock import AsyncMock, MagicMock

from clubbot import db, ops


def test_chunk_text_short_is_single_piece():
    assert ops.chunk_text("hello", limit=100) == ["hello"]


def test_chunk_text_splits_under_limit_losslessly():
    body = "\n".join(str(i) for i in range(5000))
    parts = ops.chunk_text(body, limit=100)
    assert all(len(p) <= 100 for p in parts)
    assert "\n".join(parts) == body


def test_chunk_text_hard_splits_overlong_line():
    parts = ops.chunk_text("x" * 250, limit=100)
    assert [len(p) for p in parts] == [100, 100, 50]


def test_rate_limiter_blocks_beyond_max_in_window():
    rl = ops.RateLimiter(
        max_per_window=2, window_seconds=60, min_interval_seconds=0,
        clock=iter([0.0, 0.0, 0.0]).__next__,
    )
    assert rl.allow("u") is True
    assert rl.allow("u") is True
    assert rl.allow("u") is False


def test_rate_limiter_enforces_min_interval():
    rl = ops.RateLimiter(
        max_per_window=99, window_seconds=60, min_interval_seconds=5,
        clock=iter([0.0, 1.0, 6.0]).__next__,
    )
    assert rl.allow("u") is True
    assert rl.allow("u") is False  # only 1s since last
    assert rl.allow("u") is True   # 6s since first → ok


def test_rate_limiter_is_per_key():
    rl = ops.RateLimiter(
        max_per_window=1, window_seconds=60, clock=iter([0.0, 0.0]).__next__
    )
    assert rl.allow("a") is True
    assert rl.allow("b") is True


def test_backup_database_creates_readable_copy(tmp_path):
    src = tmp_path / "clubbot.db"
    conn = db.connect(str(src))
    db.ensure_treasurer(conn, 999)
    out = ops.backup_database(
        str(src), backups_dir=str(tmp_path / "backups"), keep=3
    )
    restored = sqlite3.connect(out)
    assert restored.execute("SELECT COUNT(*) FROM admins").fetchone()[0] == 1


def test_backup_database_prunes_to_keep(tmp_path):
    src = tmp_path / "clubbot.db"
    db.connect(str(src))
    backups_dir = tmp_path / "backups"
    # Pretend several older backups already exist.
    backups_dir.mkdir()
    for name in ("clubbot-20260101-000000.db", "clubbot-20260102-000000.db"):
        (backups_dir / name).write_text("old")
    ops.backup_database(str(src), backups_dir=str(backups_dir), keep=2)
    remaining = sorted(p.name for p in backups_dir.iterdir())
    assert len(remaining) == 2  # newest 2 kept, oldest pruned


def _run_on_error_with_treasurer():
    ops.reset_error_alert_throttle()
    conn = db.connect(":memory:")
    db.ensure_treasurer(conn, 999)
    update = MagicMock()
    context = MagicMock()
    context.error = ValueError("boom")
    context.bot_data = {"db": conn}
    context.bot.send_message = AsyncMock()
    asyncio.run(ops.on_error(update, context))
    return context


def test_on_error_dms_treasurer():
    context = _run_on_error_with_treasurer()
    assert context.bot.send_message.await_count == 1
    assert context.bot.send_message.call_args.kwargs["chat_id"] == 999


def test_on_error_is_rate_limited():
    ops.reset_error_alert_throttle()
    conn = db.connect(":memory:")
    db.ensure_treasurer(conn, 999)
    context = MagicMock()
    context.error = ValueError("boom")
    context.bot_data = {"db": conn}
    context.bot.send_message = AsyncMock()
    asyncio.run(ops.on_error(MagicMock(), context))
    asyncio.run(ops.on_error(MagicMock(), context))  # within cooldown
    assert context.bot.send_message.await_count == 1


def test_mark_dirty_noop_without_syncer():
    context = MagicMock()
    context.bot_data = {}
    ops.mark_dirty(context)  # must not raise


def test_mark_dirty_calls_syncer():
    syncer = MagicMock()
    context = MagicMock()
    context.bot_data = {"sheet_syncer": syncer}
    ops.mark_dirty(context)
    syncer.mark_dirty.assert_called_once()
