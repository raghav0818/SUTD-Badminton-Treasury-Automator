"""Entry point: python -m clubbot"""

import logging

from clubbot import bot, config, db
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
    app = bot.build_application(cfg.bot_token, conn, extractor=extractor)
    app.run_polling()


if __name__ == "__main__":
    main()
