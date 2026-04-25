"""Search flows: standard search and company people search."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from bs4 import BeautifulSoup

from .browser import LinkedInBrowser
from .callbacks import NullCallback, ProgressCallback
from .humanize import (
    SessionRhythm,
    hover_drift,
    human_click,
    human_scroll,
    human_sleep,
    human_type,
    maybe_decoy_profile_open,
    maybe_micro_break,
)
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

    def __init__(
        self,
        browser: LinkedInBrowser,
        callback: ProgressCallback | None = None,
        *,
        rhythm: SessionRhythm | None = None,
        camouflage: bool = False,
    ):
        self.browser = browser
        self.callback = callback or NullCallback()
        self.rhythm = rhythm or SessionRhythm.from_seed(None)
        self.camouflage = camouflage

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

        await self.browser.goto("https://www.linkedin.com/feed", wait_seconds=0)
        await human_sleep(self.rhythm, "page_load")
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
        await human_click(self.browser, search_input, self.rhythm)
        await human_sleep(self.rhythm, "micro")
        await human_type(self.browser, search_input, config.query, self.rhythm)
        await human_sleep(self.rhythm, "micro")
        await self.browser.press_key("Enter", "Enter", 13)
        await human_sleep(self.rhythm, "page_load")

        await self._switch_to_people_results(config.query)

        if config.location:
            self._emit(f"Applying location filter: {config.location}", 12)
            await self._apply_standard_location_filter(config.location)

        all_profiles: list[PersonProfile] = []
        seen_urls: set[str] = set()
        page = 1

        while len(all_profiles) < config.max_results:
            self._emit(f"Extracting page {page}...", max(15, state.percent(15, 95)))
            await human_sleep(self.rhythm, "scan")
            await hover_drift(self.browser, self.rhythm)
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

            if self.camouflage:
                await self._maybe_decoy()

            if len(all_profiles) >= config.max_results:
                break

            has_next = await self._go_to_next_page()
            if not has_next:
                break

            page += 1
            await maybe_micro_break(self.rhythm)
            await human_sleep(self.rhythm, "next_page")

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

        # When both filters are present, randomize order: humans don't always
        # apply them in the same sequence and the resulting URL is identical.
        steps: list[tuple[str, Any]] = []
        if config.keyword:
            steps.append(("keyword", config.keyword))
        if config.location:
            steps.append(("location", config.location))
        if len(steps) > 1 and self.rhythm.rng.random() < 0.5:
            steps.reverse()

        for kind, value in steps:
            if kind == "keyword":
                self._emit(f"Applying keyword filter: {value}", 20)
                await self._apply_company_keyword_filter(value)
            else:
                self._emit(f"Applying location filter: {value}", 30)
                await self._apply_company_location_filter_with_retry(value)

        all_profiles: list[PersonProfile] = []
        seen_urls: set[str] = set()
        loads = 0

        while len(all_profiles) < config.max_results:
            self._emit(
                f"Extracting company people (load {loads + 1})...",
                max(35, state.percent(35, 95)),
            )
            await human_sleep(self.rhythm, "scan")
            await hover_drift(self.browser, self.rhythm)
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

            if self.camouflage:
                await self._maybe_decoy()

            if len(all_profiles) >= config.max_results:
                break

            has_more = await self._click_show_more_results()
            if not has_more:
                break

            await maybe_micro_break(self.rhythm)
            await human_sleep(self.rhythm, "next_page")

        final = all_profiles[: config.max_results]
        self._emit_done(f"Company search complete: {len(final)} profiles")
        return final

    async def _switch_to_people_results(self, query: str) -> None:
        current_url = str(self.browser.tab.url)
        if "/search/results/people/" in current_url:
            return

        pills = await self.browser.select_all('div[role="radio"], button')
        for pill in pills:
            try:
                text = await pill.apply("el => el.textContent")
                if text and "People" in str(text):
                    await human_click(self.browser, pill, self.rhythm)
                    await human_sleep(self.rhythm, "page_load")
                    if "/search/results/people/" in str(self.browser.tab.url):
                        return
            except Exception:
                continue

        # Deterministic fallback when people tab click fails.
        parsed = urlparse(current_url)
        params = parse_qs(parsed.query)
        keywords = params.get("keywords", [query])[0]
        direct_people_url = f"https://www.linkedin.com/search/results/people/?keywords={quote(keywords)}"
        await self.browser.goto(direct_people_url, wait_seconds=0)
        await human_sleep(self.rhythm, "page_load")

    async def _apply_standard_location_filter(self, location: str) -> None:
        normalized = location.strip()
        if not normalized:
            return

        current_url = str(self.browser.tab.url)
        if "/search/results/people/" not in current_url:
            raise RuntimeError("Location filter can only be applied on People results.")
        if self._url_has_location_facet(current_url):
            return

        await self._apply_standard_location_filter_ui(normalized)

    def _url_has_location_facet(self, url: str) -> bool:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        for key, values in params.items():
            lowered = key.lower()
            if "geo" not in lowered:
                continue
            if any(str(value).strip() for value in values):
                return True
        return False

    async def _apply_standard_location_filter_ui(self, location: str) -> None:
        locations_toggle = await self._find_clickable_by_text("Locations")
        if not locations_toggle:
            raise RuntimeError("Could not find 'Locations' filter control.")

        try:
            await locations_toggle.scroll_into_view()
        except Exception:
            pass
        await human_sleep(self.rhythm, "tick")
        await human_click(self.browser, locations_toggle, self.rhythm)
        await human_sleep(self.rhythm, "micro")

        location_selectors = [
            'input[placeholder*="Add a location"]',
            'input[aria-label*="Add a location"]',
            'input[placeholder*="Location"]',
            'input[aria-label*="Location"]',
            'input[id*="advanced-filter-geoUrn"]',
            'input[name*="geoUrn"]',
        ]
        location_input = await self.browser.select_first(location_selectors)
        if not location_input:
            await self._dismiss_dialog()
            raise RuntimeError("Could not find location input in LinkedIn filters.")

        await human_click(self.browser, location_input, self.rhythm)
        await human_sleep(self.rhythm, "tick")
        await self._clear_focused_input()
        await human_sleep(self.rhythm, "tick")
        await human_type(self.browser, location_input, location, self.rhythm, allow_typos=False)
        await human_sleep(self.rhythm, "micro")
        await self._select_first_typeahead_option()

        show_results_control = await self.browser.select_first(
            [
                "button.search-reusables__secondary-filters-show-results-button",
                "button.search-reusables__all-filters-show-results-button",
                'button[data-test-reusables-filters-show-results-button]',
                'button[aria-label*="Show results"]',
                "a[href*='origin=FACETED_SEARCH']",
            ]
        )
        if not show_results_control:
            show_results_control = await self._find_clickable_by_text("Show results")
        if not show_results_control:
            await self._dismiss_dialog()
            raise RuntimeError("Could not find 'Show results' after setting location filter.")

        try:
            await show_results_control.scroll_into_view()
        except Exception:
            pass
        await human_sleep(self.rhythm, "tick")
        await human_click(self.browser, show_results_control, self.rhythm)
        await human_sleep(self.rhythm, "filter_apply")
        if not self._url_has_location_facet(str(self.browser.tab.url)):
            raise RuntimeError("Location filter did not apply (geo facet missing in URL).")

    async def _find_clickable_by_text(self, needle: str) -> Any | None:
        elements = await self.browser.select_all("button, a, div[role='button']")
        for element in elements:
            try:
                text = await element.apply(
                    """
                    el => [el.innerText || "", el.getAttribute("aria-label") || ""]
                      .join(" ")
                      .replace(/\\s+/g, " ")
                      .trim()
                    """
                )
            except Exception:
                continue
            if text and needle.lower() in str(text).lower():
                return element
        return None

    async def _clear_focused_input(self) -> None:
        try:
            await self.browser.evaluate(
                """
                (() => {
                  const el = document.activeElement;
                  if (!el || !("value" in el)) return false;
                  el.value = "";
                  el.dispatchEvent(new Event("input", { bubbles: true }));
                  return true;
                })()
                """
            )
        except Exception:
            pass

    async def _select_first_typeahead_option(self) -> None:
        option = await self.browser.select_first(
            [
                'li[role="option"]',
                'div[role="option"]',
                "li.search-reusables__collection-values-item",
            ]
        )
        if option:
            try:
                await option.scroll_into_view()
            except Exception:
                pass
            await human_sleep(self.rhythm, "tick")
            try:
                await human_click(self.browser, option, self.rhythm)
                await human_sleep(self.rhythm, "micro")
                return
            except Exception:
                pass

        await self.browser.press_key("ArrowDown", "ArrowDown", 40)
        await human_sleep(self.rhythm, "tick")
        await self.browser.press_key("Enter", "Enter", 13)
        await human_sleep(self.rhythm, "micro")

    async def _dismiss_dialog(self) -> None:
        try:
            await self.browser.press_key("Escape", "Escape", 27)
            await human_sleep(self.rhythm, "tick")
        except Exception:
            pass

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
        await human_scroll(self.browser, self.rhythm, until_bottom=True)
        await human_sleep(self.rhythm, "micro")

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
        try:
            await next_button.scroll_into_view()
        except Exception:
            pass
        await human_sleep(self.rhythm, "tick")
        await human_click(self.browser, next_button, self.rhythm)
        await human_sleep(self.rhythm, "next_page")
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
        # The target page must match the requested company slug, not just any
        # company People tab; without this check, a previous target's URL
        # would short-circuit navigation to the new one.
        target_slug = self._slug_from_people_url(people_url)

        # ~40% of the time, simulate a human exploring the company by landing
        # on the overview page first and clicking the People tab.
        try_overview = self.rhythm.rng.random() < 0.4
        if try_overview:
            overview_url = people_url.replace("/people/", "/")
            try:
                await self.browser.goto(overview_url, wait_seconds=0)
                await human_sleep(self.rhythm, "page_load")
                await human_scroll(
                    self.browser, self.rhythm,
                    pixels=int(self.rhythm.rng.uniform(200, 600)),
                )
                await human_sleep(self.rhythm, "scan", multiplier=0.6)
                people_link = await self._find_clickable_by_text("People")
                if people_link:
                    try:
                        await human_click(self.browser, people_link, self.rhythm)
                        await human_sleep(self.rhythm, "page_load")
                    except Exception:
                        pass
            except Exception:
                pass

        for attempt in range(1, 4):
            if await self._on_target_company_people_page(target_slug):
                return
            await self.browser.goto(people_url, wait_seconds=0)
            await human_sleep(self.rhythm, "page_load")
            if await self._on_target_company_people_page(target_slug):
                return
            if attempt < 3:
                await human_sleep(self.rhythm, "think")
        raise RuntimeError(f"Could not confirm company people page for slug '{target_slug}'.")

    def _slug_from_people_url(self, people_url: str) -> str:
        parts = urlparse(people_url).path.strip("/").split("/")
        if "company" in parts:
            idx = parts.index("company")
            if idx + 1 < len(parts):
                return parts[idx + 1].lower()
        return ""

    async def _on_target_company_people_page(self, target_slug: str) -> bool:
        current_url = str(self.browser.tab.url)
        if "/company/" not in current_url or "/people/" not in current_url:
            return False
        if target_slug and self._slug_from_people_url(current_url) != target_slug:
            return False
        try:
            html = await self.browser.tab.get_content()
        except Exception:
            return False
        return "Organization page for" in html

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
        await human_click(self.browser, input_element, self.rhythm)
        await human_sleep(self.rhythm, "micro")
        await human_type(self.browser, input_element, keyword, self.rhythm, allow_typos=False)
        await human_sleep(self.rhythm, "micro")
        await self.browser.press_key("Enter", "Enter", 13)
        await human_sleep(self.rhythm, "filter_apply")

    async def _apply_company_location_filter_with_retry(self, location: str) -> None:
        """Apply location filter; on failure, retry once with a varied flow."""
        try:
            await self._apply_company_location_filter(location)
            return
        except Exception as first_exc:
            # Vary the retry: dismiss, longer pause, then try again.
            await self._dismiss_dialog()
            await human_sleep(self.rhythm, "deliberate")
            try:
                await self._apply_company_location_filter(location)
                return
            except Exception:
                raise first_exc

    async def _apply_company_location_filter(self, location: str) -> None:
        normalized = location.strip()
        if not normalized:
            return
        if self._url_has_company_location_facet(str(self.browser.tab.url)):
            return

        add_buttons = await self.browser.select_all(
            "button.org-people-bar-graph-module__add-search-facet, "
            'button[aria-label*="Add"][aria-label*="location"]'
        )
        if not add_buttons:
            raise RuntimeError("Could not find location Add button.")
        await human_click(self.browser, add_buttons[0], self.rhythm)
        await human_sleep(self.rhythm, "think", multiplier=0.7)

        location_input = await self.browser.select_first(
            [
                "#people-bar-graph-module-facet-search-input",
                'input[placeholder*="location"]',
            ]
        )
        if not location_input:
            raise RuntimeError("Could not find location filter input.")
        await human_click(self.browser, location_input, self.rhythm)
        await self._clear_focused_input()
        await human_type(self.browser, location_input, normalized, self.rhythm, allow_typos=False)
        await human_sleep(self.rhythm, "think", multiplier=0.7)

        selected = await self._select_company_location_option(normalized)
        if not selected:
            await self._select_first_typeahead_option()

        await human_sleep(self.rhythm, "filter_apply")
        if not self._url_has_company_location_facet(str(self.browser.tab.url)):
            raise RuntimeError("Company location filter did not apply (facetGeoRegion missing in URL).")

    def _url_has_company_location_facet(self, url: str) -> bool:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        values = params.get("facetGeoRegion", [])
        return any(str(value).strip() for value in values)

    async def _select_company_location_option(self, location: str) -> bool:
        location_json = json.dumps(location)
        return bool(
            await self.browser.evaluate(
                f"""
                (() => {{
                  const normalize = (value) => (value || "").replace(/\\s+/g, " ").trim();
                  const target = normalize({location_json}).toLowerCase();
                  const options = Array.from(document.querySelectorAll('li[role="option"]'))
                    .filter((option) => option.offsetWidth || option.offsetHeight || option.getClientRects().length);
                  const exact = options.find((option) => normalize(option.innerText).toLowerCase() === target);
                  const partial = options.find((option) => normalize(option.innerText).toLowerCase().includes(target));
                  const option = exact || partial;
                  if (!option) return false;
                  option.scrollIntoView({{ block: "center" }});
                  for (const type of ["pointerdown", "mousedown", "mouseup", "click"]) {{
                    option.dispatchEvent(new MouseEvent(type, {{ bubbles: true, cancelable: true, view: window }}));
                  }}
                  return true;
                }})()
                """
            )
        )

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
        await human_scroll(self.browser, self.rhythm, until_bottom=True)
        await human_sleep(self.rhythm, "micro")

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

        try:
            await button.scroll_into_view()
        except Exception:
            pass
        await human_sleep(self.rhythm, "tick")
        await human_click(self.browser, button, self.rhythm)
        await human_sleep(self.rhythm, "next_page")
        return True

    async def _maybe_decoy(self) -> None:
        """Optionally open a non-target profile in a new tab as camouflage."""
        try:
            links = await self.browser.select_all('a[href*="/in/"]')
        except Exception:
            return
        await maybe_decoy_profile_open(self.browser, self.rhythm, links)

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
