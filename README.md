# linkedin-search

LinkedIn people discovery automation with two core flows:

- `standard-search`: use LinkedIn's main search bar, switch to People results, paginate, and extract profiles.
- `company-search`: start from a LinkedIn company page and extract people from its `People` tab.

The project uses clear browser/session management, typed data models, and progress callbacks.

## Features

- Stealth-first webdriver setup using `nodriver` (no automation flags added by this project)
- Cookie-based auth session handling
- Progress callbacks (`ConsoleCallback` included)
- Typed data models (Pydantic)
- CSV export to any local output path
- CLI-first workflow

## Important

- Use responsibly and comply with LinkedIn Terms of Service and local regulations.
- Keep request volume low and add delays when needed.

## Installation

Python version: `>=3.10,<3.14` (Python `3.14` is currently not supported due to an upstream `nodriver` compatibility issue).

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e .
```

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

## External MCP Server (`browser-bridge-mcp`)

This repository no longer ships an MCP server implementation. Use the external
`browser-bridge-mcp` project instead.

### Install

```bash
pipx install "git+https://github.com/codeisalifestyle/browser-bridge-mcp.git"
```

If you prefer the local project virtualenv:

```bash
<venv>/bin/pip install "git+https://github.com/codeisalifestyle/browser-bridge-mcp.git"
```

Use a Python `>=3.10,<3.14` virtualenv for MCP installation.

### Start MCP server

```bash
browser-bridge-mcp --transport stdio
```

### MCP client config example

```json
{
  "mcpServers": {
    "browser-bridge-mcp": {
      "command": "browser-bridge-mcp",
      "args": ["--transport", "stdio"]
    }
  }
}
```

### Important: `CallMcpTool` argument shape

When invoking MCP tools through Cursor `CallMcpTool`, pass tool parameters inside
an `arguments` object. Example:

```json
{
  "server": "user-browser-bridge-mcp",
  "toolName": "session_start",
  "arguments": {
    "cookie_file": "~/.linkedin-search/session.json",
    "cookie_fallback_domain": ".linkedin.com",
    "start_url": "https://www.linkedin.com/feed/",
    "headless": false
  }
}
```

If parameters are provided outside `arguments`, many clients will send an empty
payload and the server will launch a default browser session (which can look like
an extra unexpected window).

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

