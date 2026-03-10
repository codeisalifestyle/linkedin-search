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

```bash
python3 -m venv .venv
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
  --session-file ~/.linkedin-search/session.json \
  --output ./output/standard_results.csv
```

### 3) Company people search

```bash
linkedin-search company-search \
  --company-url "https://www.linkedin.com/company/microsoft/" \
  --keyword "engineering manager" \
  --location "London" \
  --max-results 100 \
  --session-file ~/.linkedin-search/session.json \
  --output ./output/company_results.csv
```

## CLI Commands

- `create-session`
- `standard-search`
- `company-search`

Run `linkedin-search <command> --help` for full options.

## Output CSV Schema

The exported CSV includes:

- `name`
- `headline`
- `location`
- `company`
- `profile_url`
- `search_type`

