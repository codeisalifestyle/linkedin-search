import tempfile
import unittest
from pathlib import Path

from linkedin_search.csv_exporter import export_profiles_csv
from linkedin_search.models import PersonProfile, SearchType


class CsvExportTest(unittest.TestCase):
    def test_export_profiles_csv(self) -> None:
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
            out = Path(tmp) / "profiles.csv"
            path = export_profiles_csv(profiles, out)
            self.assertTrue(path.exists())

            content = path.read_text(encoding="utf-8")
            self.assertIn("Alice Smith", content)
            self.assertIn("https://www.linkedin.com/in/bob-lee/", content)
            self.assertIn("search_type", content)


if __name__ == "__main__":
    unittest.main()

