"""Entry point: python -m clubbot"""

import logging

from clubbot import bot, config, db
from clubbot.gemini import GeminiExtractor
from clubbot.sheets import SheetMirror


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
    sheet = None
    if cfg.google_credentials and cfg.sheet_id:
        sheet = SheetMirror.from_config(cfg.google_credentials, cfg.sheet_id)
    elif cfg.google_credentials or cfg.sheet_id:
        logging.warning(
            "Google Sheet mirror needs BOTH GOOGLE_SERVICE_ACCOUNT_FILE and "
            "SHEET_ID; mirror disabled."
        )
    app = bot.build_application(cfg.bot_token, conn, extractor=extractor, sheet=sheet)
    app.run_polling()


if __name__ == "__main__":
    main()
