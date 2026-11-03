"""Isolated tests for international phone parsing; no database is needed."""

import unittest

from app.schemas.whitelist_schema import WhitelistCreate
from app.utils.phone_numbers import normalize_phone_number, normalize_whatsapp_phone


class PhoneNumberTests(unittest.TestCase):
    def test_international_and_local_numbers(self):
        cases = [
            ("0812-3456-7890", "ID", "+6281234567890"),
            ("+1 (202) 555-0123", "ID", "+12025550123"),
            ("020 7946 0018", "GB", "+442079460018"),
            ("+65 6123 4567", "ID", "+6561234567"),
            ("+60 12-345 6789", "ID", "+60123456789"),
        ]
        for value, region, expected in cases:
            with self.subTest(value=value):
                assert normalize_phone_number(value, region) == expected

    def test_invalid_values_and_extensions(self):
        cases = [("", "ID"), ("123", "ID"), ("+1 202 555 0123 ext 4", "US"), ("081234567890", "XX")]
        for value, region in cases:
            with (
                self.subTest(value=value, region=region),
                self.assertRaises(ValueError),  # noqa: PT027
            ):
                normalize_phone_number(value, region)

    def test_whitelist_normalizes_equivalent_forms_and_selected_region(self):
        cases = [
            ("0812 3456 7890", "ID", "+6281234567890"),
            ("+62 812 3456 7890", "ID", "+6281234567890"),
            ("202 555 0123", "US", "+12025550123"),
            ("+1 202 555 0123", "ID", "+12025550123"),
            ("020 7946 0018", "GB", "+442079460018"),
        ]
        for value, region, expected in cases:
            with self.subTest(value=value, region=region):
                entry = WhitelistCreate(phone_number=value, country=region)
                assert entry.model_dump()["phone_number"] == expected
                assert "country" not in entry.model_dump()
        assert normalize_whatsapp_phone("6281234567890") == "+6281234567890"
        assert normalize_whatsapp_phone("12025550123") == "+12025550123"


if __name__ == "__main__":
    unittest.main()
