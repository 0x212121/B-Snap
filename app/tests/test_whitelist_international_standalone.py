"""Exercise whitelist writes with mocked persistence and no application database."""

import io
import unittest

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from starlette.datastructures import UploadFile

from app.models.camera_group import CameraGroup
from app.models.whitelist import WhatsappWhitelist
from app.routes.whitelist import add_whitelist, import_whitelist_csv
from app.schemas.whitelist_schema import WhitelistCreate


class WhitelistInternationalTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_normalizes_and_uses_existing_duplicate_key(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
        admin = SimpleNamespace(username="admin")
        entry = WhitelistCreate(phone_number="202 555 0123", country="US")
        with patch("app.routes.whitelist.log_audit"):
            result = add_whitelist(request, entry, db, admin)
        assert result == {"status": "success"}
        assert db.add.call_args.args[0].phone_number == "+12025550123"

    async def test_duplicate_uses_normalized_key(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = object()
        request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
        admin = SimpleNamespace(username="admin")
        entry = WhitelistCreate(phone_number="+1 202 555 0123")
        with self.assertRaises(HTTPException) as error:  # noqa: PT027
            add_whitelist(request, entry, db, admin)
        assert error.exception.status_code == 409
        db.add.assert_not_called()

    async def test_import_country_and_invalid_rows(self):
        db = MagicMock()
        group_query = MagicMock()
        group_query.all.return_value = []
        phone_query = MagicMock()
        phone_query.filter.return_value.first.return_value = None
        db.query.side_effect = lambda model: group_query if model is CameraGroup else phone_query
        csv = b"phone_number,country,name\n2025550123,US,Alice\n123,ID,Invalid\n"
        file = UploadFile(file=io.BytesIO(csv), filename="whitelist.csv")
        request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
        admin = SimpleNamespace(username="admin")
        with patch("app.routes.whitelist.log_audit"):
            result = await import_whitelist_csv(request, file, db, admin)
        assert result["imported"] == 1
        assert result["skipped"] == 1
        saved = db.add.call_args.args[0]
        assert isinstance(saved, WhatsappWhitelist)
        assert saved.phone_number == "+12025550123"


if __name__ == "__main__":
    unittest.main()
