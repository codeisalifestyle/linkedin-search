"""Auth session persistence for LinkedIn cookies."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


REQUIRED_AUTH_COOKIES = {"li_at", "JSESSIONID"}


class SessionManager:
    """Save/load cookie sessions from disk."""

    def __init__(self, session_file: str | Path):
        self.session_file = Path(session_file).expanduser()

    def save_cookies(self, cookies: list[dict[str, Any]]) -> Path:
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "cookies": cookies,
        }
        with self.session_file.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        return self.session_file

    def load_cookies(self) -> list[dict[str, Any]]:
        if not self.session_file.exists():
            raise FileNotFoundError(f"Session file not found: {self.session_file}")

        with self.session_file.open("r", encoding="utf-8") as fh:
            data = json.load(fh)

        if isinstance(data, list):
            cookies = data
        else:
            cookies = data.get("cookies", [])

        if not isinstance(cookies, list):
            raise ValueError("Invalid session format: cookies must be a list")

        normalized: list[dict[str, Any]] = []
        for cookie in cookies:
            if not isinstance(cookie, dict):
                continue
            if "name" not in cookie or "value" not in cookie:
                continue
            normalized.append(cookie)
        return normalized

    @staticmethod
    def linkedin_only(cookies: list[dict[str, Any]]) -> list[dict[str, Any]]:
        linkedin_cookies: list[dict[str, Any]] = []
        for cookie in cookies:
            domain = str(cookie.get("domain", ""))
            if "linkedin.com" in domain:
                linkedin_cookies.append(cookie)
        return linkedin_cookies

    @staticmethod
    def ensure_required_auth_cookies(cookies: list[dict[str, Any]]) -> None:
        names = {str(cookie.get("name")) for cookie in cookies}
        missing = REQUIRED_AUTH_COOKIES - names
        if missing:
            missing_list = ", ".join(sorted(missing))
            raise RuntimeError(
                f"Session is missing required LinkedIn auth cookies: {missing_list}. "
                "Re-create session with `create-session`."
            )

