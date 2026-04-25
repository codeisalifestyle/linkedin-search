import asyncio
import unittest

from linkedin_search.search import LinkedInSearcher


class _FakeTab:
    def __init__(self, url: str):
        self.url = url


class _FakeBrowser:
    def __init__(self, url: str):
        self.tab = _FakeTab(url)
        self.goto_calls: list[tuple[str, float]] = []

    async def goto(self, url: str, *, wait_seconds: float = 0.0) -> None:
        self.goto_calls.append((url, wait_seconds))
        self.tab.url = url


class StandardLocationFilterTest(unittest.TestCase):
    def test_url_has_location_facet(self) -> None:
        browser = _FakeBrowser("https://www.linkedin.com/search/results/people/?keywords=data")
        searcher = LinkedInSearcher(browser)
        url = "https://www.linkedin.com/search/results/people/?keywords=data&geoUrn=%5B%22103644278%22%5D"
        self.assertTrue(searcher._url_has_location_facet(url))

    def test_url_without_location_facet(self) -> None:
        browser = _FakeBrowser("https://www.linkedin.com/search/results/people/?keywords=data")
        searcher = LinkedInSearcher(browser)
        url = "https://www.linkedin.com/search/results/people/?keywords=geo+analyst"
        self.assertFalse(searcher._url_has_location_facet(url))

    def test_company_url_has_location_facet(self) -> None:
        browser = _FakeBrowser("https://www.linkedin.com/company/aksia/people/")
        searcher = LinkedInSearcher(browser)
        url = "https://www.linkedin.com/company/aksia/people/?facetGeoRegion=101165590"
        self.assertTrue(searcher._url_has_company_location_facet(url))

    def test_company_url_without_location_facet(self) -> None:
        browser = _FakeBrowser("https://www.linkedin.com/company/aksia/people/")
        searcher = LinkedInSearcher(browser)
        url = "https://www.linkedin.com/company/aksia/people/?keywords=research"
        self.assertFalse(searcher._url_has_company_location_facet(url))

    def test_location_filter_raises_if_not_people_page(self) -> None:
        browser = _FakeBrowser("https://www.linkedin.com/feed/")
        searcher = LinkedInSearcher(browser)
        with self.assertRaisesRegex(RuntimeError, "People results"):
            asyncio.run(searcher._apply_standard_location_filter("Austin"))

    def test_location_filter_raises_if_ui_step_fails(self) -> None:
        browser = _FakeBrowser("https://www.linkedin.com/search/results/people/?keywords=financial+advisor")
        searcher = LinkedInSearcher(browser)

        async def _raise_ui(_: str) -> None:
            raise RuntimeError("ui failure")

        searcher._apply_standard_location_filter_ui = _raise_ui  # type: ignore[method-assign]

        with self.assertRaisesRegex(RuntimeError, "ui failure"):
            asyncio.run(searcher._apply_standard_location_filter("Austin"))

    def test_slug_from_people_url(self) -> None:
        browser = _FakeBrowser("about:blank")
        searcher = LinkedInSearcher(browser)
        self.assertEqual(
            searcher._slug_from_people_url("https://www.linkedin.com/company/aksia/people/"),
            "aksia",
        )
        self.assertEqual(
            searcher._slug_from_people_url("https://www.linkedin.com/company/albourne-partners/people/?facetGeoRegion=101165590"),
            "albourne-partners",
        )
        self.assertEqual(searcher._slug_from_people_url("https://example.com/foo"), "")


if __name__ == "__main__":
    unittest.main()
