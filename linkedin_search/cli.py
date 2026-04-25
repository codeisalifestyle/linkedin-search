"""CLI entrypoint for linkedin-search."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from .browser import LinkedInBrowser
from .callbacks import ConsoleCallback
from .csv_exporter import export_profiles_csv
from .dev_browser import (
    DEFAULT_ACTION_LIMIT,
    DEFAULT_ACTION_WAIT_SECONDS,
    DEFAULT_DEV_STATE_FILE,
    run_dev_browser_action,
    run_dev_browser_start,
)
from .humanize import SessionRhythm, session_cooldown, session_warmup
from .json_exporter import export_profiles_json
from .models import CompanySearchConfig, PersonProfile, StandardSearchConfig
from .search import LinkedInSearcher
from .session import SessionManager


def _add_humanize_args(parser: argparse.ArgumentParser) -> None:
    """Attach humanization flags to a search subparser.

    All humanization is opt-in. Defaults preserve the prior behaviour (no
    warmup/cooldown, no decoy clicks). Personality is account-stable: by
    default we derive the personality seed from the session file path so
    every run for the same account feels like the same user. Override with
    ``--personality-seed`` (or ``--no-stable-personality`` for fully random).
    """
    parser.add_argument(
        "--warmup",
        action="store_true",
        help="Browse the LinkedIn feed briefly before starting the search task",
    )
    parser.add_argument(
        "--cooldown",
        action="store_true",
        help="Drop back to the LinkedIn feed and scroll briefly after the task completes",
    )
    parser.add_argument(
        "--camouflage",
        action="store_true",
        help="Occasionally open a non-target profile in a new tab as decoy traffic",
    )
    parser.add_argument(
        "--personality-seed",
        default=None,
        help="Seed string for session personality. Default: derived from --session-file path",
    )
    parser.add_argument(
        "--no-stable-personality",
        action="store_true",
        help="Ignore the default account-derived seed and randomize personality every run",
    )


def _build_rhythm(args: argparse.Namespace) -> SessionRhythm:
    """Build a SessionRhythm from CLI args, preferring an account-stable seed."""
    if args.no_stable_personality and args.personality_seed is None:
        return SessionRhythm.from_seed(None)
    seed = args.personality_seed
    if seed is None:
        seed = str(Path(args.session_file).expanduser().resolve())
    return SessionRhythm.from_seed(seed)


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
    standard.add_argument("--location", help="Optional location filter")
    standard.add_argument("--max-results", type=int, default=100, help="Max profiles to collect")
    standard.add_argument(
        "--session-file",
        default="~/.linkedin-search/session.json",
        help="Path to saved cookie session JSON",
    )
    standard.add_argument(
        "--output",
        help="Output path (default: ./output/standard_results_<timestamp>.<format>)",
    )
    standard.add_argument(
        "--output-format",
        choices=["csv", "json"],
        default="csv",
        help="Output format (default: csv)",
    )
    standard.add_argument("--headless", action="store_true", help="Run browser in headless mode")
    _add_humanize_args(standard)

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
        help="Output path (default: ./output/company_results_<timestamp>.<format>)",
    )
    company.add_argument(
        "--output-format",
        choices=["csv", "json"],
        default="csv",
        help="Output format (default: csv)",
    )
    company.add_argument("--headless", action="store_true", help="Run browser in headless mode")
    _add_humanize_args(company)

    dev_start = subparsers.add_parser(
        "dev-browser-start",
        help="Start a long-running browser for development/debugging actions",
    )
    dev_start.add_argument(
        "--session-file",
        default="~/.linkedin-search/session.json",
        help="Path to saved cookie session JSON",
    )
    dev_start.add_argument(
        "--state-file",
        default=DEFAULT_DEV_STATE_FILE,
        help="Where to write host/port info for later attach commands",
    )
    dev_start.add_argument("--headless", action="store_true", help="Run browser in headless mode")
    dev_start.add_argument(
        "--skip-auth",
        action="store_true",
        help="Skip LinkedIn cookie auth bootstrap (generic page debugging)",
    )
    dev_start.add_argument(
        "--start-url",
        default="https://www.linkedin.com/feed",
        help="Initial URL to open once browser is ready",
    )
    dev_start.add_argument(
        "--reuse-existing",
        action="store_true",
        help=(
            "If an active session is already recorded in --state-file, print its "
            "connection details and exit instead of launching a new browser"
        ),
    )

    dev_action = subparsers.add_parser(
        "dev-browser-action",
        help="Attach to the running dev browser and execute one action",
    )
    dev_action.add_argument(
        "--state-file",
        default=DEFAULT_DEV_STATE_FILE,
        help="Path to state file written by dev-browser-start",
    )
    dev_action.add_argument(
        "--action",
        required=True,
        choices=["url", "navigate", "snapshot", "query", "click", "type", "wait"],
        help="Action to execute against the running browser",
    )
    dev_action.add_argument("--url", help="Target URL (required for navigate)")
    dev_action.add_argument("--selector", help="CSS selector (required for query/click/type)")
    dev_action.add_argument("--text", help="Text to type (required for type)")
    dev_action.add_argument(
        "--wait-seconds",
        type=float,
        default=DEFAULT_ACTION_WAIT_SECONDS,
        help="Post-action wait time in seconds",
    )
    dev_action.add_argument("--clear", action="store_true", help="Clear input before typing")
    dev_action.add_argument("--submit", action="store_true", help="Press Enter after type action")
    dev_action.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_ACTION_LIMIT,
        help="Max elements returned for snapshot/query actions",
    )
    dev_action.add_argument("--output", help="Optional file path to write JSON action output")

    return parser


def configure_logging(debug: bool) -> None:
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def default_output_path(search_kind: str, output_format: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    extension = "json" if output_format == "json" else "csv"
    return Path("output") / f"{search_kind}_results_{timestamp}.{extension}"


def export_profiles(
    profiles: list[PersonProfile],
    *,
    search_kind: str,
    output: str | None,
    output_format: str,
) -> Path:
    output_path = (
        Path(output)
        if output
        else default_output_path(search_kind=search_kind, output_format=output_format)
    )
    if output_format == "json":
        return export_profiles_json(profiles, output_path)
    return export_profiles_csv(profiles, output_path)


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
    rhythm = _build_rhythm(args)
    browser = await _open_authenticated_browser(args.session_file, headless=args.headless)
    try:
        if args.warmup:
            await session_warmup(browser, rhythm)
        searcher = LinkedInSearcher(
            browser,
            callback=callback,
            rhythm=rhythm,
            camouflage=args.camouflage,
        )
        profiles = await searcher.standard_search(config)
        if args.cooldown:
            await session_cooldown(browser, rhythm)
    finally:
        await browser.close()

    output_path = export_profiles(
        profiles,
        search_kind="standard",
        output=args.output,
        output_format=args.output_format,
    )
    print(f"Saved {len(profiles)} profiles to {output_path}")


async def run_company_search(args: argparse.Namespace) -> None:
    config = CompanySearchConfig(
        company_url=args.company_url,
        keyword=args.keyword,
        location=args.location,
        max_results=args.max_results,
    )
    callback = ConsoleCallback()
    rhythm = _build_rhythm(args)
    browser = await _open_authenticated_browser(args.session_file, headless=args.headless)
    try:
        if args.warmup:
            await session_warmup(browser, rhythm)
        searcher = LinkedInSearcher(
            browser,
            callback=callback,
            rhythm=rhythm,
            camouflage=args.camouflage,
        )
        profiles = await searcher.company_search(config)
        if args.cooldown:
            await session_cooldown(browser, rhythm)
    finally:
        await browser.close()

    output_path = export_profiles(
        profiles,
        search_kind="company",
        output=args.output,
        output_format=args.output_format,
    )
    print(f"Saved {len(profiles)} profiles to {output_path}")


async def run_dev_browser_start_cmd(args: argparse.Namespace) -> None:
    await run_dev_browser_start(
        session_file=args.session_file,
        state_file=args.state_file,
        headless=args.headless,
        skip_auth=args.skip_auth,
        start_url=args.start_url,
        reuse_existing=args.reuse_existing,
    )


async def run_dev_browser_action_cmd(args: argparse.Namespace) -> None:
    payload = await run_dev_browser_action(
        state_file=args.state_file,
        action=args.action,
        selector=args.selector,
        text=args.text,
        url=args.url,
        wait_seconds=args.wait_seconds,
        clear=args.clear,
        submit=args.submit,
        limit=args.limit,
        output=args.output,
    )
    print(json.dumps(payload, indent=2))


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
        elif args.command == "dev-browser-start":
            asyncio.run(run_dev_browser_start_cmd(args))
        elif args.command == "dev-browser-action":
            asyncio.run(run_dev_browser_action_cmd(args))
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

