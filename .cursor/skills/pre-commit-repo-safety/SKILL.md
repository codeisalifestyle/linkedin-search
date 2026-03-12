---
name: pre-commit-repo-safety
description: Runs strict pre-commit health and security checks before creating any commit. Use when the user or agent is preparing a commit, preparing a push, or verifying repository safety for public or private remotes.
---

# Pre-Commit Repo Safety

## Purpose

Prevent unsafe commits by enforcing a strict health and security gate for both private and public repositories.

## When to apply

Apply this skill when:
- The user asks to commit changes.
- The agent determines a commit is about to be created.
- The user asks if code is safe to push publicly.
- The user asks for a pre-commit or pre-push safety check.
- The user asks if code is safe for a private or internal repository.

## Required workflow

1. Determine commit scope
   - Review staged and unstaged changes.
   - Focus checks on files that will be committed.

2. Run project health checks
   - Use the repository's existing quality commands first (from CI config, README, or project scripts).
   - If available, run lint, tests, and basic static checks.
   - If a configured health check fails, mark the gate as failed.

3. Run strict security checks
   - Scan staged changes for secrets and credentials.
   - Look for accidental inclusion of tokens, private keys, cookies, auth headers, API keys, passwords, and internal endpoints.
   - Verify no sensitive local files are being committed (for example `.env`, credential exports, private keys, local dumps).
   - Check lockfiles/dependencies with a vulnerability scanner if the repo already uses one.
   - If any high, medium, or uncertain security signal appears, mark the gate as failed until resolved.

4. Enforce blocking policy
   - Do not say "safe to commit" or "safe to push" unless all checks pass.
   - If any check fails or cannot run, treat status as failed.
   - Explain exactly what failed and what must be fixed.

## Output format (checklist only)

Return results in this structure:

```markdown
Pre-Commit Repo Safety Checklist

- [x] Scope reviewed (staged/unstaged understood)
- [x] Health checks passed
- [x] Secret scan passed
- [x] Sensitive files check passed
- [x] Dependency/security checks passed
- [x] Final gate: SAFE TO COMMIT/PUSH
```

Use `[ ]` for failures and append a short reason:
- `- [ ] Secret scan passed - Potential API key pattern found in path/to/file`

## Strict decision rule

- SAFE TO COMMIT/PUSH: only when every checklist item passes.
- NOT SAFE TO COMMIT/PUSH: if any item fails or is unverified.

## Remediation guidance

When failing the gate, provide:
- Exact failing item(s)
- A minimal fix plan
- Re-check steps needed to clear the gate
