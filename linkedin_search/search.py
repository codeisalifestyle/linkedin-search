"""Search flows: standard search and company people search."""

from __future__ import annotations

import asyncio
import random
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from bs4 import BeautifulSoup

from .browser import LinkedInBrowser
from .callbacks import NullCallback, ProgressCallback
from .models import CompanySearchConfig, PersonProfile, SearchType, StandardSearchConfig


@dataclass
class _SearchState:
    max_results: int
    collected: int = 0

    def percent(self, floor: int = 0, ceiling: int = 100) -> int:
        if self.max_results <= 0:
            return ceiling
        raw = int((self.collected / self.max_results) * 100)
        return max(floor, min(ceiling, raw))


class LinkedInSearcher:
    """Implements the two required LinkedIn search functions."""

    def __init__(self, browser: LinkedInBrowser, callback: ProgressCallback | None = None):
        self.browser = browser
        self.callback = callback or NullCallback()

    def _emit_start(self, operation: str) -> None:
        self.callback.on_start(operation)

    def _emit(self, message: str, percent: int | None = None) -> None:
        self.callback.on_progress(message, percent)

    def _emit_done(self, message: str) -> None:
        self.callback.on_complete(message)

    def _emit_error(self, message: str) -> None:
        self.callback.on_error(message)

    async def standard_search(self, config: StandardSearchConfig) -> list[PersonProfile]:
        """Use LinkedIn's global search bar and extract people results."""
        self._emit_start(f"Standard search: {config.query}")
        state = _SearchState(max_results=config.max_results)

        await self.browser.goto("https://www.linkedin.com/feed", wait_seconds=3)
        search_input = await self.browser.select_first(
            [
                ".search-global-typeahead__input",
                'input[placeholder="Search"]',
                'input[type="text"][role="combobox"]',
            ]
        )
        if not search_input:
            raise RuntimeError("Could not find LinkedIn search bar.")

        self._emit("Typing search query...", 5)
        await search_input.click()
        await asyncio.sleep(0.4)
        await self._type_naturally(search_input, config.query)
        await self.browser.press_key("Enter", "Enter", 13)
        await asyncio.sleep(random.uniform(4.0, 6.0))

        await self._switch_to_people_results(config.query)

        if config.location:
            self._emit(f"Applying location hint: {config.location}", 12)
            await self._apply_standard_location_hint(config.location)

        all_profiles: list[PersonProfile] = []
        seen_urls: set[str] = set()
        page = 1

        while len(all_profiles) < config.max_results:
            self._emit(f"Extracting page {page}...", max(15, state.percent(15, 95)))
            page_profiles = await self._extract_standard_page_profiles()
            if not page_profiles:
                break

            for profile in page_profiles:
                if profile.profile_url in seen_urls:
                    continue
                seen_urls.add(profile.profile_url)
                all_profiles.append(profile)
                state.collected = len(all_profiles)
                if len(all_profiles) >= config.max_results:
                    break

            self._emit(
                f"Collected {len(all_profiles)}/{config.max_results}",
                state.percent(15, 95),
            )

            if len(all_profiles) >= config.max_results:
                break

            has_next = await self._go_to_next_page()
            if not has_next:
                break

            page += 1
            await asyncio.sleep(random.uniform(2.0, 4.0))

        final = all_profiles[: config.max_results]
        self._emit_done(f"Standard search complete: {len(final)} profiles")
        return final

    async def company_search(self, config: CompanySearchConfig) -> list[PersonProfile]:
        """Search from a LinkedIn company page People tab."""
        self._emit_start(f"Company search: {config.company_url}")
        state = _SearchState(max_results=config.max_results)

        people_url = self._normalize_company_people_url(config.company_url)
        self._emit("Navigating to company people tab...", 5)
        await self._navigate_company_people_page(people_url)

        company_name = await self._extract_company_name()
        self._emit(f"Company detected: {company_name}", 12)

        if config.keyword:
            self._emit(f"Applying keyword filter: {config.keyword}", 20)
            await self._apply_company_keyword_filter(config.keyword)

        if config.location:
            self._emit(f"Applying location filter: {config.location}", 30)
            await self._apply_company_location_filter(config.location)

        all_profiles: list[PersonProfile] = []
        seen_urls: set[str] = set()
        loads = 0

        while len(all_profiles) < config.max_results:
            self._emit(
                f"Extracting company people (load {loads + 1})...",
                max(35, state.percent(35, 95)),
            )
            page_profiles = await self._extract_company_page_profiles(company_name)

            for profile in page_profiles:
                if profile.profile_url in seen_urls:
                    continue
                seen_urls.add(profile.profile_url)
                all_profiles.append(profile)
                state.collected = len(all_profiles)
                if len(all_profiles) >= config.max_results:
                    break

            loads += 1
            self._emit(
                f"Collected {len(all_profiles)}/{config.max_results}",
                state.percent(35, 95),
            )

            if len(all_profiles) >= config.max_results:
                break

            has_more = await self._click_show_more_results()
            if not has_more:
                break

            await asyncio.sleep(random.uniform(2.0, 4.0))

        final = all_profiles[: config.max_results]
        self._emit_done(f"Company search complete: {len(final)} profiles")
        return final

    async def _type_naturally(self, element: Any, text: str) -> None:
        for char in text:
            await element.send_keys(char)
            await asyncio.sleep(random.uniform(0.05, 0.15))

    async def _switch_to_people_results(self, query: str) -> None:
        current_url = str(self.browser.tab.url)
        if "/search/results/people/" in current_url:
            return

        pills = await self.browser.select_all('div[role="radio"], button')
        for pill in pills:
            try:
                text = await pill.apply("el => el.textContent")
                if text and "People" in str(text):
                    await pill.click()
                    await asyncio.sleep(random.uniform(2.0, 3.0))
                    if "/search/results/people/" in str(self.browser.tab.url):
                        return
            except Exception:
                continue

        # Deterministic fallback when people tab click fails.
        parsed = urlparse(current_url)
        params = parse_qs(parsed.query)
        keywords = params.get("keywords", [query])[0]
        direct_people_url = f"https://www.linkedin.com/search/results/people/?keywords={quote(keywords)}"
        await self.browser.goto(direct_people_url, wait_seconds=3)

    async def _apply_standard_location_hint(self, location: str) -> None:
        """Best-effort location hint by appending location to keywords."""
        current_url = str(self.browser.tab.url)
        if "/search/results/people/" not in current_url:
            return
        parsed = urlparse(current_url)
        params = parse_qs(parsed.query)
        keywords = params.get("keywords", [""])[0]
        if location.lower() in keywords.lower():
            return
        merged_keywords = f"{keywords} {location}".strip()
        hinted_url = f"https://www.linkedin.com/search/results/people/?keywords={quote(merged_keywords)}"
        await self.browser.goto(hinted_url, wait_seconds=3)

    async def _extract_standard_page_profiles(self) -> list[PersonProfile]:
        html = await self.browser.tab.get_content()
        soup = BeautifulSoup(html, "html.parser")
        profiles: list[PersonProfile] = []
        seen_urls: set[str] = set()

        containers = soup.select("li.reusable-search__result-container, div.entity-result")
        for container in containers:
            profile = self._extract_standard_profile_from_container(container)
            if not profile:
                continue
            if profile.profile_url in seen_urls:
                continue
            seen_urls.add(profile.profile_url)
            profiles.append(profile)

        # Fallback for unexpected page markup.
        if not profiles:
            for link in soup.find_all("a", href=True):
                href = str(link.get("href", ""))
                if "/in/" not in href:
                    continue
                profile_url = self._normalize_profile_url(href)
                if profile_url in seen_urls:
                    continue
                name = self._clean_text(link.get_text(" ", strip=True))
                if not name:
                    continue
                seen_urls.add(profile_url)
                profiles.append(
                    PersonProfile(
                        name=name,
                        headline=None,
                        location=None,
                        company=None,
                        profile_url=profile_url,
                        search_type=SearchType.STANDARD,
                    )
                )
        return profiles

    def _extract_standard_profile_from_container(self, container: Any) -> PersonProfile | None:
        link = container.select_one('a[href*="/in/"]')
        if not link:
            return None
        profile_url = self._normalize_profile_url(str(link.get("href", "")))
        if not profile_url:
            return None

        name = None
        name_node = link.select_one('span[aria-hidden="true"]')
        if name_node:
            name = self._clean_text(name_node.get_text(" ", strip=True))
        if not name:
            name = self._clean_text(link.get_text(" ", strip=True))
        if not name:
            return None

        headline = self._text_from_first(
            container,
            [
                ".entity-result__primary-subtitle",
                ".t-14.t-black.t-normal",
            ],
        )
        location = self._text_from_first(
            container,
            [
                ".entity-result__secondary-subtitle",
                ".t-12.t-black--light.t-normal",
            ],
        )

        return PersonProfile(
            name=name,
            headline=headline,
            location=location,
            company=None,
            profile_url=profile_url,
            search_type=SearchType.STANDARD,
        )

    async def _go_to_next_page(self) -> bool:
        await self.browser.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(1.5)

        buttons = await self.browser.select_all(
            'button[aria-label="Next"], button.artdeco-pagination__button--next'
        )
        if not buttons:
            return False

        next_button = buttons[0]
        try:
            disabled = await next_button.apply(
                "el => el.disabled || el.getAttribute('aria-disabled') === 'true'"
            )
            if disabled:
                return False
        except Exception:
            pass

        current_url = str(self.browser.tab.url)
        await next_button.scroll_into_view()
        await asyncio.sleep(0.5)
        await next_button.click()
        await asyncio.sleep(random.uniform(4.0, 6.0))
        return str(self.browser.tab.url) != current_url

    def _normalize_company_people_url(self, raw: str) -> str:
        value = raw.strip()
        if "/" not in value and "." not in value:
            return f"https://www.linkedin.com/company/{value}/people/"
        if not value.startswith(("http://", "https://")):
            value = f"https://{value}"

        parsed = urlparse(value)
        if "linkedin.com" not in parsed.netloc:
            raise ValueError(f"Not a LinkedIn URL: {raw}")

        parts = parsed.path.strip("/").split("/")
        if "company" not in parts:
            raise ValueError(f"Could not extract company slug from URL: {raw}")
        idx = parts.index("company")
        if idx + 1 >= len(parts):
            raise ValueError(f"Could not extract company slug from URL: {raw}")

        slug = parts[idx + 1]
        return f"https://www.linkedin.com/company/{slug}/people/"

    async def _navigate_company_people_page(self, people_url: str) -> None:
        for attempt in range(1, 4):
            await self.browser.goto(people_url, wait_seconds=random.uniform(4.0, 6.0))
            current_url = str(self.browser.tab.url)
            html = await self.browser.tab.get_content()
            if "/company/" in current_url and "/people/" in current_url and "Organization page for" in html:
                return
            if attempt < 3:
                await asyncio.sleep(random.uniform(2.0, 3.0))
        raise RuntimeError("Could not confirm company people page.")

    async def _extract_company_name(self) -> str:
        html = await self.browser.tab.get_content()
        soup = BeautifulSoup(html, "html.parser")

        h1 = soup.find("h1", class_=re.compile(r"org-top-card-summary__title"))
        if h1:
            name = self._clean_text(h1.get_text(" ", strip=True))
            if name:
                return name

        h1_any = soup.find("h1")
        if h1_any:
            name = self._clean_text(h1_any.get_text(" ", strip=True))
            if name:
                return name

        current_url = str(self.browser.tab.url)
        match = re.search(r"/company/([^/]+)", current_url)
        if match:
            return match.group(1).replace("-", " ").title()
        raise RuntimeError("Could not extract company name.")

    async def _apply_company_keyword_filter(self, keyword: str) -> None:
        input_element = await self.browser.select_first(
            [
                "#people-search-keywords",
                "textarea.org-people__search-input",
            ]
        )
        if not input_element:
            raise RuntimeError("Could not find company keyword filter input.")
        await input_element.click()
        await asyncio.sleep(0.5)
        await self._type_naturally(input_element, keyword)
        await self.browser.press_key("Enter", "Enter", 13)
        await asyncio.sleep(random.uniform(3.0, 5.0))

    async def _apply_company_location_filter(self, location: str) -> None:
        add_buttons = await self.browser.select_all(
            "button.org-people-bar-graph-module__add-search-facet, "
            'button[aria-label*="Add"][aria-label*="location"]'
        )
        if not add_buttons:
            raise RuntimeError("Could not find location Add button.")
        await add_buttons[0].click()
        await asyncio.sleep(1.0)

        location_input = await self.browser.select_first(
            [
                "#people-bar-graph-module-facet-search-input",
                'input[placeholder*="location"]',
            ]
        )
        if not location_input:
            raise RuntimeError("Could not find location filter input.")
        await location_input.click()
        await self._type_naturally(location_input, location)
        await asyncio.sleep(1.0)
        await self.browser.press_key("ArrowDown", "ArrowDown", 40)
        await asyncio.sleep(0.4)
        await self.browser.press_key("Enter", "Enter", 13)
        await asyncio.sleep(random.uniform(3.0, 5.0))

    async def _extract_company_page_profiles(self, company_name: str) -> list[PersonProfile]:
        html = await self.browser.tab.get_content()
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.find_all("div", class_="org-people-profile-card__profile-info")
        profiles: list[PersonProfile] = []
        seen_urls: set[str] = set()

        for card in cards:
            profile = self._extract_company_profile_from_card(card, company_name)
            if not profile:
                continue
            if profile.profile_url in seen_urls:
                continue
            seen_urls.add(profile.profile_url)
            profiles.append(profile)
        return profiles

    def _extract_company_profile_from_card(self, card: Any, company_name: str) -> PersonProfile | None:
        lockup = card.find("div", class_=re.compile(r"artdeco-entity-lockup"))
        if not lockup:
            return None

        title_container = lockup.find("div", class_=re.compile(r"artdeco-entity-lockup__title"))
        if not title_container:
            return None
        profile_link = title_container.find("a", href=re.compile(r"/in/"))
        if not profile_link:
            return None

        profile_url = self._normalize_profile_url(str(profile_link.get("href", "")))
        if not profile_url:
            return None

        name = None
        name_div = profile_link.find("div", class_=re.compile(r"lt-line-clamp"))
        if name_div:
            name = self._clean_text(name_div.get_text(" ", strip=True))
        if not name:
            aria_label = str(profile_link.get("aria-label", ""))
            if aria_label:
                name = aria_label.replace("View ", "").replace("'s profile", "").strip()
        if not name:
            return None

        subtitle_container = lockup.find("div", class_=re.compile(r"artdeco-entity-lockup__subtitle"))
        headline = self._clean_text(subtitle_container.get_text(" ", strip=True)) if subtitle_container else None

        return PersonProfile(
            name=name,
            headline=headline,
            location=None,
            company=company_name,
            profile_url=profile_url,
            search_type=SearchType.COMPANY,
        )

    async def _click_show_more_results(self) -> bool:
        await self.browser.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(random.uniform(1.5, 2.5))

        buttons = await self.browser.select_all(
            "button.scaffold-finite-scroll__load-button, button[class*='load-button']"
        )
        if not buttons:
            return False
        button = buttons[0]

        try:
            disabled = await button.apply("el => el.disabled || el.getAttribute('aria-disabled') === 'true'")
            if disabled:
                return False
        except Exception:
            pass

        await button.scroll_into_view()
        await asyncio.sleep(0.5)
        await button.click()
        await asyncio.sleep(random.uniform(3.0, 5.0))
        return True

    def _normalize_profile_url(self, href: str) -> str:
        value = href.split("?")[0].strip()
        if not value:
            return ""
        if value.startswith("/"):
            value = f"https://www.linkedin.com{value}"
        return value

    def _text_from_first(self, container: Any, selectors: list[str]) -> str | None:
        for selector in selectors:
            node = container.select_one(selector)
            if not node:
                continue
            text = self._clean_text(node.get_text(" ", strip=True))
            if text:
                return text
        return None

    def _clean_text(self, text: str | None) -> str | None:
        if not text:
            return None
        cleaned = re.sub(r"\s+", " ", text).strip()
        return cleaned or None

