"""CLI entrypoint for linkedin-search."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

from .browser import LinkedInBrowser
from .callbacks import ConsoleCallback
from .csv_exporter import export_profiles_csv
from .models import CompanySearchConfig, StandardSearchConfig
from .search import LinkedInSearcher
from .session import SessionManager


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="linkedin-search",
        description="LinkedIn people search automation",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_session = subparsers.add_parser(
        "create-session",
        help="Open browser for manual login and save LinkedIn cookies",
    )
    create_session.add_argument(
        "--session-file",
        default="~/.linkedin-search/session.json",
        help="Where to save cookie session JSON",
    )

    standard = subparsers.add_parser(
        "standard-search",
        help="Run standard search from LinkedIn global search bar",
    )
    standard.add_argument("--query", "-q", "-query", required=True, help="Search query")
    standard.add_argument("--location", help="Optional location hint")
    standard.add_argument("--max-results", type=int, default=100, help="Max profiles to collect")
    standard.add_argument(
        "--session-file",
        default="~/.linkedin-search/session.json",
        help="Path to saved cookie session JSON",
    )
    standard.add_argument(
        "--output",
        help="Output CSV path (default: ./output/standard_results_<timestamp>.csv)",
    )
    standard.add_argument("--headless", action="store_true", help="Run browser in headless mode")

    company = subparsers.add_parser(
        "company-search",
        help="Run search from LinkedIn company page -> People tab",
    )
    company.add_argument("--company-url", required=True, help="LinkedIn company URL or slug")
    company.add_argument("--keyword", help="Keyword filter inside company people search")
    company.add_argument("--location", help="Location filter inside company people search")
    company.add_argument("--max-results", type=int, default=100, help="Max profiles to collect")
    company.add_argument(
        "--session-file",
        default="~/.linkedin-search/session.json",
        help="Path to saved cookie session JSON",
    )
    company.add_argument(
        "--output",
        help="Output CSV path (default: ./output/company_results_<timestamp>.csv)",
    )
    company.add_argument("--headless", action="store_true", help="Run browser in headless mode")

    return parser


def configure_logging(debug: bool) -> None:
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def default_output_path(search_kind: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path("output") / f"{search_kind}_results_{timestamp}.csv"


async def run_create_session(session_file: str) -> None:
    manager = SessionManager(session_file)
    print("Opening browser for LinkedIn login...")
    print("After login is complete, return here and press Enter.")

    async with LinkedInBrowser(headless=False) as browser:
        await browser.goto("https://www.linkedin.com/login", wait_seconds=1.5)
        await asyncio.to_thread(input, "Press Enter after successful login: ")
        await browser.goto("https://www.linkedin.com/feed", wait_seconds=2.5)

        state = await browser.detect_auth_state()
        if state != "logged_in":
            raise RuntimeError(
                f"Login validation failed (state={state}). "
                "Please re-run `create-session` and complete login."
            )

        cookies = await browser.get_cookies()
        linkedin_cookies = SessionManager.linkedin_only(cookies)
        SessionManager.ensure_required_auth_cookies(linkedin_cookies)
        saved_path = manager.save_cookies(linkedin_cookies)

    print(f"Session saved: {saved_path}")


async def _open_authenticated_browser(session_file: str, *, headless: bool) -> LinkedInBrowser:
    manager = SessionManager(session_file)
    cookies = manager.load_cookies()
    SessionManager.ensure_required_auth_cookies(cookies)

    browser = LinkedInBrowser(headless=headless)
    await browser.start()
    try:
        await browser.set_cookies(cookies)
        await browser.ensure_authenticated()
        return browser
    except Exception:
        await browser.close()
        raise


async def run_standard_search(args: argparse.Namespace) -> None:
    config = StandardSearchConfig(
        query=args.query,
        location=args.location,
        max_results=args.max_results,
    )
    callback = ConsoleCallback()
    browser = await _open_authenticated_browser(args.session_file, headless=args.headless)
    try:
        searcher = LinkedInSearcher(browser, callback=callback)
        profiles = await searcher.standard_search(config)
    finally:
        await browser.close()

    output_path = Path(args.output) if args.output else default_output_path("standard")
    output = export_profiles_csv(profiles, output_path)
    print(f"Saved {len(profiles)} profiles to {output}")


async def run_company_search(args: argparse.Namespace) -> None:
    config = CompanySearchConfig(
        company_url=args.company_url,
        keyword=args.keyword,
        location=args.location,
        max_results=args.max_results,
    )
    callback = ConsoleCallback()
    browser = await _open_authenticated_browser(args.session_file, headless=args.headless)
    try:
        searcher = LinkedInSearcher(browser, callback=callback)
        profiles = await searcher.company_search(config)
    finally:
        await browser.close()

    output_path = Path(args.output) if args.output else default_output_path("company")
    output = export_profiles_csv(profiles, output_path)
    print(f"Saved {len(profiles)} profiles to {output}")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    configure_logging(args.debug)

    try:
        if args.command == "create-session":
            asyncio.run(run_create_session(args.session_file))
        elif args.command == "standard-search":
            asyncio.run(run_standard_search(args))
        elif args.command == "company-search":
            asyncio.run(run_company_search(args))
        else:
            parser.error(f"Unknown command: {args.command}")
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        raise SystemExit(130)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()

