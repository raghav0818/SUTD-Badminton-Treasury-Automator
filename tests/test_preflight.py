from unittest.mock import MagicMock

from clubbot.sheets import SheetMirror
from scripts import preflight


def test_sheet_preflight_checks_access_without_rewriting_tabs(monkeypatch):
    mirror = MagicMock()
    mirror.check_access.return_value = "Club membership"
    monkeypatch.setattr(SheetMirror, "from_config", MagicMock(return_value=mirror))
    cfg = MagicMock(google_credentials="service-account.json", sheet_id="sheet-id")

    assert preflight.check_sheet(cfg)
    mirror.check_access.assert_called_once_with()
    mirror.snapshot.assert_not_called()
    mirror.push.assert_not_called()
