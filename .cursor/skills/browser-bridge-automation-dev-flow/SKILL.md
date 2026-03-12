---
name: browser-bridge-automation-dev-flow
description: Uses browser-bridge-mcp as the primary browser control channel for web scraping and automation development. Apply when building new automation flows, debugging stale selectors or changed UI/API behavior, reproducing failures, and converting exploratory browser actions into deterministic scripts.
---

# Browser Bridge Automation Dev Flow

## Purpose

Use this skill for browser automation and scraping work that requires a tight
development loop between:

1. understanding live browser state,
2. taking one action,
3. verifying the result,
4. then encoding stable behavior in code.

The primary MCP connection method is `browser-bridge-mcp`.

## Core Principles

- Keep browser work evidence-driven: observe before and after each action.
- Change one variable at a time (selector, wait, navigation, input payload).
- Prefer deterministic behavior over "best effort" heuristics.
- Capture enough artifacts to explain and reproduce failures.
- Finish with script-level verification, not just manual browser success.

## Primary MCP Workflow

1. Session lifecycle:
   - list sessions,
   - start or attach one session,
   - stop sessions when done.
2. Baseline state inspection:
   - URL/title,
   - DOM snapshot/query,
   - cookies/storage if auth-sensitive.
3. Single-step mutation:
   - navigate, click, type, scroll, evaluate.
4. Verification:
   - wait for URL/selector/text,
   - re-snapshot and confirm expected state transition.
5. Extraction:
   - gather structured output via `browser_evaluate` or targeted queries.

## Tooling Guardrails

- Use `browser-bridge-mcp` tools as the default browser interface.
- When using Cursor `CallMcpTool`, pass parameters inside `arguments`.
- Keep one active session per task unless parallel sessions are explicitly needed.
- On completion, stop sessions created during the task to avoid orphan browsers.

## Development Modes

### 1) New Automation Development

Use this when implementing a new flow or extending an existing flow.

1. Parse the requested business outcome into browser-level milestones.
2. Reproduce the flow manually through MCP actions end-to-end.
3. At each milestone, verify exact state transitions and capture evidence.
4. Iterate through failures and edge cases until behavior is consistent.
5. Transcribe the successful sequence into deterministic script code.
6. Run the script from terminal and verify it reproduces MCP-observed behavior.

Expected output from the agent:
- deterministic function/script steps,
- explicit waits/assertions,
- structured extraction payload contract,
- verification notes.

### 2) Maintenance and Bug Fixing

Use this when existing automation breaks or drifts due to UI/API changes.

1. Execute the failing automation in terminal first.
2. Read logs/traces to isolate the failing step and failure type.
3. Recreate only the problematic segment with MCP actions.
4. Identify root cause (selector drift, timing, auth/session, changed endpoint, etc.).
5. Patch script behavior with robust selectors/state checks/retry strategy.
6. Re-run the script end-to-end and verify expected output.

Expected output from the agent:
- concise root-cause statement,
- code fix with rationale,
- verification run results,
- residual risks or follow-up tests.

## Deterministic Automation Checklist

- Entry state is validated (URL/auth/session preconditions).
- Selectors are stable and specific (avoid fragile generated classes).
- Every state-changing action has explicit post-conditions.
- Waits are condition-based when possible (URL/selector/text/network-idle).
- Error handling is explicit (timeout, missing element, unexpected redirects).
- Output schema is structured and consistent across runs.
- Session cleanup is handled so no stale browser sessions remain.

## Recommended Feedback Loop

For each suspect step:

1. Inspect (`url` + `snapshot`/`query`).
2. Execute one action.
3. Verify expected change.
4. If mismatch, capture evidence and adjust only one thing.
5. Repeat until stable, then codify.
