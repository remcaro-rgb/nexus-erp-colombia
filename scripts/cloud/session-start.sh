#!/bin/bash
# SessionStart hook (repo .claude/settings.json). Cloud sessions only; fast and idempotent; never fails the session.
[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0
cd "${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}" || exit 0
export PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
{
  command -v make >/dev/null || { (apt-get install -y make || sudo -n apt-get install -y make || { sudo -n apt-get update && sudo -n apt-get install -y make; }) >/dev/null 2>&1 || true; }
  command -v uv >/dev/null && { uv python find 3.13 >/dev/null 2>&1 || timeout 240 uv python install 3.13 >/dev/null 2>&1 || true; }
  if [ -f uv.lock ] && command -v uv >/dev/null; then timeout 300 uv sync --frozen >/dev/null 2>&1 || true; fi
  if [ -f pnpm-lock.yaml ] && command -v pnpm >/dev/null; then timeout 300 pnpm install --frozen-lockfile >/dev/null 2>&1 || true; fi
} &
echo "Nexus ERP cloud session. Read CLAUDE.md. Current plan state: python3 tools/cloudctl.py next (after git fetch)."
echo "System Chrome for Playwright: ${CHROME_PATH:-/usr/bin/google-chrome}"
exit 0
