"""Entry point: python -m clubbot"""

import logging

from clubbot import bot, config, db, ops, sheets
from clubbot.gemini import GeminiExtractor


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s %(name)s %(levelname)s %(message)s", level=logging.INFO
    )
    cfg = config.load_config()
    conn = db.connect(cfg.db_path)
    db.ensure_treasurer(conn, cfg.treasurer_id)

    extractor = (
        GeminiExtractor(cfg.gemini_api_key, model=cfg.gemini_model)
        if cfg.gemini_api_key
        else None
    )

    # Optional Google Sheet mirror: a missing/misconfigured Sheet degrades to
    # None so it can never block startup.
    mirror = sheets.create_mirror_from_env(
        cfg.google_service_account_json, cfg.sheet_id
    )
    syncer = sheets.SheetSyncer(mirror, conn) if mirror is not None else None
    if syncer is None:
        logging.getLogger(__name__).info(
            "Google Sheet mirror not configured; skipping Sheet sync."
        )

    # Protect the Gemini receipt path: at most 8 submissions/hour per member,
    # and at least 10s between them.
    rate_limiter = ops.RateLimiter(
        max_per_window=8, window_seconds=3600, min_interval_seconds=10
    )

    app = bot.build_application(
        cfg.bot_token,
        conn,
        extractor=extractor,
        sheet_syncer=syncer,
        rate_limiter=rate_limiter,
        db_path=cfg.db_path,
    )
    app.run_polling()


if __name__ == "__main__":
    main()
