import unittest

from linkedin_search.search import LinkedInSearcher


class CompanyUrlNormalizationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.searcher = LinkedInSearcher(browser=object())

    def test_slug_input(self) -> None:
        url = self.searcher._normalize_company_people_url("microsoft")
        self.assertEqual(url, "https://www.linkedin.com/company/microsoft/people/")

    def test_full_linkedin_input(self) -> None:
        url = self.searcher._normalize_company_people_url(
            "https://www.linkedin.com/company/acme-corp/about/"
        )
        self.assertEqual(url, "https://www.linkedin.com/company/acme-corp/people/")

    def test_non_linkedin_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.searcher._normalize_company_people_url("https://example.com/company/acme")


if __name__ == "__main__":
    unittest.main()

