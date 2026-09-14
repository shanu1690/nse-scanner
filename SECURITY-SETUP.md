# Phase 0 — security setup

## Already done
- Angel One API key rotated
- MPIN changed

## Still outstanding

1. **Regenerate the TOTP secret.** Disable and re-enable 2FA in Angel One. A
   rotated API key and new MPIN do not invalidate the old TOTP seed, which
   generates valid codes indefinitely.
2. **Review account activity** for anything you did not initiate.
3. **Scrub git history**, then force-push and delete forks:
   ```bash
   pip install git-filter-repo
   git filter-repo --path secrets.yaml --invert-paths --force
   # or, to remove a literal string wherever it appears:
   # echo 'OLD_API_KEY_VALUE==>REDACTED' > replace.txt
   # git filter-repo --replace-text replace.txt --force
   git push origin --force --all
   git push origin --force --tags
   ```
4. **Verify nothing remains:**
   ```bash
   pip install trufflehog3 || true
   docker run --rm -v "$PWD:/repo" trufflesecurity/trufflehog:latest git file:///repo
   git log --all -p | grep -niE 'api_?key|mpin|totp|client_?code' | head
   ```
5. **Make the repository private.** Note: GitHub Pages from a private repo needs a
   paid plan. Since the live feed work moves you to a backend service anyway, plan
   to host the dashboard there or on Cloudflare Pages / Netlify.

## Files in this scaffold

| File | Where it goes | What it does |
|---|---|---|
| `.github/workflows/secret-scan.yml` | repo root | fails CI on secrets in diffs, history, or the published bundle |
| `.pre-commit-config.yaml` | repo root | blocks secrets before they reach a commit |
| `gitignore-additions.txt` | append to `.gitignore` | then verify with `git check-ignore -v secrets.yaml` |
| `.claude/agents/*.md` | repo root | 12 Claude Code subagents |
| `.claude/skills/*/SKILL.md` | repo root | 8 reusable procedure skills |

Install the hooks:
```bash
pip install pre-commit
pre-commit install
pre-commit run --all-files
```

## Runtime credential handling

Credentials live in environment variables only — never a tracked file, never a log
line, never an error message, never a command-line argument, never the published
bundle. Locally use an untracked `.env`; in CI use Actions Secrets; on the backend
use the host's environment variable settings.
