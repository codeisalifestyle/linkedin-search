"""Development browser workflow for locator and action debugging."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .browser import LinkedInBrowser
from .session import SessionManager

DEFAULT_DEV_STATE_FILE = "output/dev_browser_state.json"
DEFAULT_ACTION_WAIT_SECONDS = 1.2
DEFAULT_ACTION_LIMIT = 40
MAX_ACTION_LIMIT = 300


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_path(value: str | Path) -> Path:
    return Path(value).expanduser()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _find_active_dev_browser_state(path: str | Path) -> dict[str, Any] | None:
    state_path = _as_path(path)
    if not state_path.exists():
        return None

    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("stopped_at"):
        return None

    pid = payload.get("pid")
    host = payload.get("host")
    port = payload.get("port")
    if not isinstance(pid, int) or not isinstance(host, str) or not isinstance(port, int):
        return None
    if not _is_pid_running(pid):
        return None

    payload["host"] = host.strip()
    payload["port"] = int(port)
    payload["_state_path"] = str(state_path)
    return payload


def _read_state(path: str | Path) -> dict[str, Any]:
    state_path = _as_path(path)
    if not state_path.exists():
        raise FileNotFoundError(
            f"Dev browser state file not found: {state_path}. "
            "Start one with `linkedin-search dev-browser-start`."
        )
    data = json.loads(state_path.read_text(encoding="utf-8"))
    host = str(data.get("host", "")).strip()
    port = data.get("port")
    if not host or not isinstance(port, int):
        raise ValueError(
            f"Invalid state file {state_path}: expected string `host` and integer `port`."
        )
    data["host"] = host
    data["port"] = int(port)
    data["_state_path"] = str(state_path)
    return data


def _clamp_limit(value: int) -> int:
    if value < 1:
        return 1
    if value > MAX_ACTION_LIMIT:
        return MAX_ACTION_LIMIT
    return value


def _looks_like_object_pairs(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    for item in value:
        if not isinstance(item, list) or len(item) != 2:
            return False
        if not isinstance(item[0], str):
            return False
    return True


def _normalize_evaluate_payload(value: Any) -> Any:
    if isinstance(value, dict) and "type" in value:
        kind = value.get("type")
        if kind == "null":
            return None
        if kind in {"string", "number", "boolean"}:
            return value.get("value")
        if kind == "array":
            raw_items = value.get("value", [])
            if isinstance(raw_items, list):
                return [_normalize_evaluate_payload(item) for item in raw_items]
            return []
        if kind == "object":
            raw_obj = value.get("value", [])
            if _looks_like_object_pairs(raw_obj):
                return {
                    item[0]: _normalize_evaluate_payload(item[1])
                    for item in raw_obj
                }
            return raw_obj
        if "value" in value:
            return _normalize_evaluate_payload(value["value"])
        return value

    if _looks_like_object_pairs(value):
        return {
            item[0]: _normalize_evaluate_payload(item[1])
            for item in value
        }

    if isinstance(value, list):
        return [_normalize_evaluate_payload(item) for item in value]

    return value


async def _attach_browser(state: dict[str, Any]) -> LinkedInBrowser:
    browser = LinkedInBrowser(connect_host=state["host"], connect_port=state["port"])
    await browser.start()
    return browser


def _snapshot_script(limit: int) -> str:
    return f"""
    (() => {{
      const clean = (value, maxLen = 160) => {{
        if (!value) return "";
        return String(value).replace(/\\s+/g, " ").trim().slice(0, maxLen);
      }};
      const selectors =
        'input,button,a,textarea,select,[role="button"],[role="textbox"],[contenteditable="true"]';
      const nodes = Array.from(document.querySelectorAll(selectors));
      const items = nodes.slice(0, {limit}).map((el, idx) => {{
        const attrs = {{}};
        for (const attr of ["id", "name", "type", "role", "aria-label", "placeholder", "href"]) {{
          const value = el.getAttribute(attr);
          if (value) attrs[attr] = clean(value);
        }}
        const hints = [];
        if (el.id) hints.push(`#${{el.id}}`);
        if (attrs["name"]) hints.push(`${{el.tagName.toLowerCase()}}[name="${{attrs["name"]}}"]`);
        if (attrs["aria-label"]) {{
          hints.push(`${{el.tagName.toLowerCase()}}[aria-label="${{attrs["aria-label"]}}"]`);
        }}
        if (!hints.length) hints.push(el.tagName.toLowerCase());
        return {{
          index: idx,
          tag: el.tagName.toLowerCase(),
          text: clean(el.innerText || el.textContent || ""),
          classes: clean(el.className || "", 120),
          attrs,
          locator_hints: hints.slice(0, 3),
        }};
      }});
      return {{
        url: location.href,
        title: document.title,
        total_interactive: nodes.length,
        returned: items.length,
        interactive: items,
      }};
    }})()
    """


def _query_script(selector: str, limit: int) -> str:
    selector_json = json.dumps(selector)
    return f"""
    (() => {{
      const selector = {selector_json};
      const clean = (value, maxLen = 160) => {{
        if (!value) return "";
        return String(value).replace(/\\s+/g, " ").trim().slice(0, maxLen);
      }};
      const nodes = Array.from(document.querySelectorAll(selector)).slice(0, {limit});
      const elements = nodes.map((el, idx) => {{
        const attrs = {{}};
        for (const attr of ["id", "name", "type", "role", "aria-label", "placeholder", "href"]) {{
          const value = el.getAttribute(attr);
          if (value) attrs[attr] = clean(value);
        }}
        return {{
          index: idx,
          tag: el.tagName.toLowerCase(),
          text: clean(el.innerText || el.textContent || ""),
          classes: clean(el.className || "", 120),
          attrs,
        }};
      }});
      return {{
        selector,
        count: elements.length,
        elements,
      }};
    }})()
    """


def _clear_selector_script(selector: str) -> str:
    selector_json = json.dumps(selector)
    return f"""
    (() => {{
      const el = document.querySelector({selector_json});
      if (!el) return false;
      if (!("value" in el)) return false;
      el.value = "";
      el.dispatchEvent(new Event("input", {{ bubbles: true }}));
      return true;
    }})()
    """


async def _action_snapshot(browser: LinkedInBrowser, limit: int) -> dict[str, Any]:
    payload = _normalize_evaluate_payload(await browser.evaluate(_snapshot_script(limit)))
    if isinstance(payload, dict):
        return payload
    raise RuntimeError("Snapshot action returned non-object payload.")


async def _action_query(browser: LinkedInBrowser, selector: str, limit: int) -> dict[str, Any]:
    payload = _normalize_evaluate_payload(await browser.evaluate(_query_script(selector, limit)))
    if isinstance(payload, dict):
        return payload
    raise RuntimeError("Query action returned non-object payload.")


async def _action_click(browser: LinkedInBrowser, selector: str, wait_seconds: float) -> dict[str, Any]:
    element = await browser.select_first([selector])
    if not element:
        raise RuntimeError(f"No element found for selector: {selector}")
    try:
        await element.scroll_into_view()
    except Exception:
        pass
    await asyncio.sleep(0.2)
    await element.click()
    if wait_seconds > 0:
        await asyncio.sleep(wait_seconds)
    return {
        "action": "click",
        "selector": selector,
        "url": str(browser.tab.url),
    }


async def _action_type(
    browser: LinkedInBrowser,
    selector: str,
    text: str,
    *,
    clear: bool,
    submit: bool,
    wait_seconds: float,
) -> dict[str, Any]:
    element = await browser.select_first([selector])
    if not element:
        raise RuntimeError(f"No element found for selector: {selector}")
    await element.click()
    await asyncio.sleep(0.2)
    if clear:
        await browser.evaluate(_clear_selector_script(selector))
        await asyncio.sleep(0.1)
    for char in text:
        await element.send_keys(char)
        await asyncio.sleep(0.015)
    if submit:
        await browser.press_key("Enter", "Enter", 13)
    if wait_seconds > 0:
        await asyncio.sleep(wait_seconds)
    return {
        "action": "type",
        "selector": selector,
        "submitted": submit,
        "typed_chars": len(text),
        "url": str(browser.tab.url),
    }


async def run_dev_browser_start(
    *,
    session_file: str,
    state_file: str,
    headless: bool,
    skip_auth: bool,
    start_url: str | None,
    reuse_existing: bool,
) -> None:
    state_path = _as_path(state_file)
    active_state = _find_active_dev_browser_state(state_path)
    if active_state:
        if reuse_existing:
            print("Dev browser already running; reusing existing session.", flush=True)
            print(f"State file: {state_path}", flush=True)
            print(
                f"Attach host/port: {active_state['host']}:{active_state['port']}",
                flush=True,
            )
            print(
                "Example action: linkedin-search dev-browser-action "
                f"--state-file {state_path} --action snapshot --limit 25",
                flush=True,
            )
            return
        raise RuntimeError(
            "A dev browser session is already running for this state file "
            f"(pid={active_state['pid']}, host={active_state['host']}, "
            f"port={active_state['port']}). "
            "Stop it first (Ctrl+C in the start terminal), "
            "or rerun with `--reuse-existing`."
        )

    browser = LinkedInBrowser(headless=headless)
    state_payload: dict[str, Any] = {}

    await browser.start()
    try:
        if skip_auth:
            if start_url:
                await browser.goto(start_url, wait_seconds=1.2)
        else:
            manager = SessionManager(session_file)
            cookies = manager.load_cookies()
            SessionManager.ensure_required_auth_cookies(cookies)
            await browser.set_cookies(cookies)
            await browser.ensure_authenticated()
            if start_url:
                await browser.goto(start_url, wait_seconds=1.8)

        state_payload = {
            "version": 1,
            "pid": os.getpid(),
            "host": browser.connection_host,
            "port": browser.connection_port,
            "websocket_url": browser.websocket_url,
            "headless": headless,
            "skip_auth": skip_auth,
            "session_file": str(_as_path(session_file)) if not skip_auth else None,
            "start_url": start_url,
            "current_url": str(browser.tab.url),
            "started_at": _utc_now_iso(),
        }
        _write_json(state_path, state_payload)
        print("Dev browser ready.", flush=True)
        print(f"State file: {state_path}", flush=True)
        print(f"Attach host/port: {state_payload['host']}:{state_payload['port']}", flush=True)
        print(
            "Example action: linkedin-search dev-browser-action "
            f"--state-file {state_path} --action snapshot --limit 25",
            flush=True,
        )
        print("Leave this running. Press Ctrl+C to stop.", flush=True)

        while True:
            await asyncio.sleep(1.0)
    finally:
        await browser.close()
        if state_payload:
            state_payload["stopped_at"] = _utc_now_iso()
            _write_json(state_path, state_payload)


async def run_dev_browser_action(
    *,
    state_file: str,
    action: str,
    selector: str | None,
    text: str | None,
    url: str | None,
    wait_seconds: float,
    clear: bool,
    submit: bool,
    limit: int,
    output: str | None,
) -> dict[str, Any]:
    state = _read_state(state_file)
    browser = await _attach_browser(state)
    payload: dict[str, Any]
    effective_limit = _clamp_limit(limit)

    try:
        if action == "url":
            title = await browser.evaluate("document.title")
            payload = {
                "action": "url",
                "url": str(browser.tab.url),
                "title": str(title) if title is not None else "",
            }
        elif action == "navigate":
            if not url:
                raise ValueError("Action `navigate` requires `--url`.")
            await browser.goto(url, wait_seconds=max(0.0, wait_seconds))
            payload = {
                "action": "navigate",
                "url": str(browser.tab.url),
            }
        elif action == "snapshot":
            payload = await _action_snapshot(browser, effective_limit)
            payload["action"] = "snapshot"
        elif action == "query":
            if not selector:
                raise ValueError("Action `query` requires `--selector`.")
            payload = await _action_query(browser, selector, effective_limit)
            payload["action"] = "query"
        elif action == "click":
            if not selector:
                raise ValueError("Action `click` requires `--selector`.")
            payload = await _action_click(browser, selector, max(0.0, wait_seconds))
        elif action == "type":
            if not selector:
                raise ValueError("Action `type` requires `--selector`.")
            if text is None:
                raise ValueError("Action `type` requires `--text`.")
            payload = await _action_type(
                browser,
                selector,
                text,
                clear=clear,
                submit=submit,
                wait_seconds=max(0.0, wait_seconds),
            )
        elif action == "wait":
            await asyncio.sleep(max(0.0, wait_seconds))
            payload = {
                "action": "wait",
                "seconds": max(0.0, wait_seconds),
                "url": str(browser.tab.url),
            }
        else:
            raise ValueError(f"Unsupported action: {action}")

        payload["connection"] = {
            "host": state["host"],
            "port": state["port"],
        }
        payload["executed_at"] = _utc_now_iso()

        if output:
            out_path = _as_path(output)
            _write_json(out_path, payload)
            payload["output_file"] = str(out_path)

        return payload
    finally:
        await browser.close()
