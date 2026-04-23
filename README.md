# linkedin-search

A browser automation script for LinkedIn people discovery. It drives a real
browser session over the Chrome DevTools Protocol (CDP), interacting with
LinkedIn exactly as a human would — no proprietary APIs, no headless-only
hacks.

Two search flows are included:

- `standard-search` — use LinkedIn's main search bar, switch to People results, paginate, and extract profiles.
- `company-search` — start from a LinkedIn company page and extract people from its People tab.

## Features

- Stealth-first browser automation (no automation flags injected)
- Cookie-based session management — log in once, reuse cookies
- Real LinkedIn UI location filtering via typeahead
- CSV and JSON export
- Progress callbacks (`ConsoleCallback` included)
- Typed data models (Pydantic)
- CLI-first workflow

## Important

- Use responsibly and comply with LinkedIn Terms of Service and local regulations.
- Keep request volume low and add delays when needed.

## Installation

Python `>=3.10,<3.14` is required (`3.14` is not yet supported due to an
upstream compatibility issue).

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Connecting a Browser

The script communicates with Chrome / Chromium through CDP. You can provide a
browser in several ways:

### Default: let the script launch one

When you run any command without connection flags the script starts a local
Chromium instance automatically. This is the simplest path and works out of the
box after `pip install -e .`.

### Attach to an existing browser via CDP

Start Chrome yourself with remote debugging enabled:

```bash
google-chrome --remote-debugging-port=9222
```

Then point the script at it (the `dev-browser-start` command already supports
`--host` / `--port` flags, or you can use the connection info written to a
state file).

### Selenium / WebDriver

If you already manage browser sessions through Selenium, enable CDP on the
WebDriver instance and let the script attach:

```python
from selenium import webdriver

options = webdriver.ChromeOptions()
options.add_experimental_option("debuggerAddress", "127.0.0.1:9222")
driver = webdriver.Chrome(options=options)
# linkedin-search can now connect to the same CDP endpoint
```

### Playwright

Playwright can launch or connect to a Chromium instance with CDP exposed:

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(args=["--remote-debugging-port=9222"])
    # linkedin-search attaches to the same port
```

### browser-bridge-mcp (MCP option)

[browser-bridge-mcp](https://github.com/codeisalifestyle/browser-bridge-mcp) is
a standalone MCP server that wraps a stealth browser session and exposes it as
tool calls. If you work inside an MCP-capable environment (Cursor, Claude Desktop,
etc.) it lets you launch, inspect, and control browser sessions without writing
any glue code — just add it as an MCP server and call its tools.

## Quick Start

### 1) Create session cookies (manual login once)

```bash
linkedin-search create-session --session-file ~/.linkedin-search/session.json
```

This opens a browser. Log in to LinkedIn manually, then press Enter in your terminal.

### 2) Standard people search

```bash
linkedin-search standard-search \
  --query "portfolio manager" \
  --max-results 100 \
  --output-format json \
  --session-file ~/.linkedin-search/session.json
```

### 3) Company people search

```bash
linkedin-search company-search \
  --company-url "https://www.linkedin.com/company/microsoft/" \
  --keyword "engineering manager" \
  --location "London" \
  --max-results 100 \
  --output-format json \
  --session-file ~/.linkedin-search/session.json
```

If `--output` is omitted, results are saved automatically to:

- `./output/standard_results_<timestamp>.<format>` for `standard-search`
- `./output/company_results_<timestamp>.<format>` for `company-search`

Output format defaults to CSV and can be changed with:

- `--output-format csv`
- `--output-format json`

Query tips:

- Use `--query` (or `-q` / `-query`).
- Queries can include quotes and parentheses, for example:
  `--query 'Financial "Advisor" (Austin)'`

## Development Browser Flow (Locator Debugging)

Use this when you want a long-running browser instance and attachable actions for
iterative page analysis (selectors, click/type behavior, navigation states).

### 1) Start dev browser (keeps running)

```bash
linkedin-search dev-browser-start \
  --session-file ~/.linkedin-search/session.json \
  --state-file output/dev_browser_state.json
```

This writes host/port connection info to `output/dev_browser_state.json`.
If a session is already active for that state file, the command now fails fast to
avoid accidentally launching duplicate browser windows. Use:

```bash
linkedin-search dev-browser-start --state-file output/dev_browser_state.json --reuse-existing
```

to print the existing host/port and reuse the current session.

### 2) Attach and run actions from another terminal

```bash
linkedin-search dev-browser-action \
  --state-file output/dev_browser_state.json \
  --action snapshot \
  --limit 30 \
  --output output/page_snapshot.json
```

Common actions:

```bash
# Current page URL and title
linkedin-search dev-browser-action --state-file output/dev_browser_state.json --action url

# Navigate
linkedin-search dev-browser-action --state-file output/dev_browser_state.json --action navigate --url "https://www.linkedin.com/search/results/people/"

# Inspect selector matches
linkedin-search dev-browser-action --state-file output/dev_browser_state.json --action query --selector "input.search-global-typeahead__input"

# Type and submit
linkedin-search dev-browser-action --state-file output/dev_browser_state.json --action type --selector "input.search-global-typeahead__input" --text "portfolio manager" --clear --submit

# Click
linkedin-search dev-browser-action --state-file output/dev_browser_state.json --action click --selector "button[aria-label='People']"
```

### 3) Stop session

Go back to the `dev-browser-start` terminal and press `Ctrl+C`.

## CLI Commands

- `create-session`
- `standard-search`
- `company-search`
- `dev-browser-start`
- `dev-browser-action`

Run `linkedin-search <command> --help` for full options.

## Output Schema

The exported CSV/JSON records include:

- `name`
- `headline`
- `location`
- `company`
- `profile_url`
- `search_type`

