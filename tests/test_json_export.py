import json
import tempfile
import unittest
from pathlib import Path

from linkedin_search.json_exporter import export_profiles_json
from linkedin_search.models import PersonProfile, SearchType


class JsonExportTest(unittest.TestCase):
    def test_export_profiles_json(self) -> None:
        profiles = [
            PersonProfile(
                name="Alice Smith",
                headline="Engineering Manager",
                location="London",
                company="Acme",
                profile_url="https://www.linkedin.com/in/alice-smith/",
                search_type=SearchType.COMPANY,
            ),
            PersonProfile(
                name="Bob Lee",
                headline=None,
                location=None,
                company=None,
                profile_url="https://www.linkedin.com/in/bob-lee/",
                search_type=SearchType.STANDARD,
            ),
        ]

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "profiles.json"
            path = export_profiles_json(profiles, out)
            self.assertTrue(path.exists())

            content = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(content), 2)
            self.assertEqual(content[0]["name"], "Alice Smith")
            self.assertEqual(content[1]["profile_url"], "https://www.linkedin.com/in/bob-lee/")
            self.assertEqual(content[0]["search_type"], "company")


if __name__ == "__main__":
    unittest.main()
