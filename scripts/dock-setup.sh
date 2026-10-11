#!/usr/bin/env bash
# Puts the Linear sidebar into the cmux Dock: the page, with the server running in a
# background tab of the same Dock pane. Safe to run again: it reuses what is there.
# Run it from a terminal inside cmux.
#
# Usage: scripts/dock-setup.sh            open what is missing
#        scripts/dock-setup.sh --restart  also restart a running server (after an update or a new key)
#
# The page pane is reused when the Dock already shows http://127.0.0.1:<port>/ (for
# example a chromeless browser control seeded from ~/.config/cmux/dock.json); otherwise
# a browser pane is created, which shows cmux's browser toolbar.
set -euo pipefail

. "$(cd "$(dirname "$0")" && pwd)/common.sh"

fail() { printf 'Error: %s\n' "$*" >&2; exit 1; }

# Runs a cmux command; when it fails, stops with what cmux said.
cmux_do() {
  local output
  output="$("$CMUX" "$@" 2>&1)" || fail "cmux $1 failed: ${output:-no output}"
}

read_dock() {
  find_tabs "$@" || fail "Could not read the cmux Dock. Run this from a terminal inside cmux (cmux 0.65.0 or newer)."
}

# A page opened before the server listens shows the browser's error page, which
# never retries; reload it once the server answers.
reload_page_when_server_answers() {
  for _ in $(seq 1 60); do
    server_answers && break
    sleep 0.5
  done
  if server_answers; then
    "$CMUX" browser "$PAGE_SURFACE" reload >/dev/null 2>&1 || true
  else
    fail "The server did not answer; look at the \"Linear server\" tab in the Dock."
  fi
}

restart=false
case "${1:-}" in
  "") ;;
  --restart) restart=true ;;
  *) fail "Unknown option \"$1\". Usage: $0 [--restart]" ;;
esac

case "$PORT" in
  "" | *[!0-9]* | 0*) fail "LINEAR_SIDEBAR_PORT must be a port number, not \"$PORT\"." ;;
esac
[ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ] || fail "LINEAR_SIDEBAR_PORT must be between 1 and 65535."
refresh="${LINEAR_SIDEBAR_REFRESH_SECONDS:-}"
case "$refresh" in
  *[!0-9]* | 0*) fail "LINEAR_SIDEBAR_REFRESH_SECONDS must be a whole number of seconds, not \"$refresh\"." ;;
esac

# Settings other than the defaults become options, so that cmux restarts the server with
# them too. The API key stays in the Keychain, out of command lines.
SERVER_ARGS=("$SERVER")
[ "$PORT" = "$DEFAULT_PORT" ] || SERVER_ARGS+=(--port "$PORT")
[ -z "$refresh" ] || SERVER_ARGS+=(--refresh-seconds "$refresh")
# new-surface --command and send type into a shell, so every word is quoted.
SERVER_COMMAND="$(printf '%q ' "${SERVER_ARGS[@]}")"
SERVER_COMMAND="${SERVER_COMMAND% }"

if $restart; then
  pid="$(server_pid)"
  if [ -n "$pid" ]; then
    stop_server "$pid" ||
      fail "The server did not stop. Press Ctrl-C in the \"Linear server\" tab and run this again."
    echo "Stopped the server."
  fi
fi

read_dock
"$CMUX" right-sidebar set dock --no-focus >/dev/null 2>&1 || true
if [ -z "$PAGE_PANE" ]; then
  cmux_do new-pane --type browser --placement dock --url "$URL"
  read_dock
  [ -n "$PAGE_PANE" ] || fail "Could not open the Linear page in the Dock."
  echo "Opened the Linear page in the Dock."
fi

if [ -z "$SERVER_SURFACE" ] && server_answers; then
  echo "The server already runs in another tab; the Linear page uses it."
  exit 0
fi

if [ -z "$SERVER_SURFACE" ]; then
  [ -z "$(listener_pid)" ] ||
    fail "Port $PORT is already in use by another program; stop it or set LINEAR_SIDEBAR_PORT."
  before="$PANE_TERMINALS"
  cmux_do new-surface --type terminal --placement dock --pane "$PAGE_PANE" \
    --command "$SERVER_COMMAND" --focus false
  read_dock --created-after "$before"
  [ -n "$SERVER_SURFACE" ] || fail "Could not find the server tab that was just opened in the Dock."
  started="Started the server in a background tab behind the Linear page."
elif server_answers; then
  started=""
elif [ -n "$(listener_pid)" ]; then
  fail "Port $PORT is already in use by another program; stop it or set LINEAR_SIDEBAR_PORT."
else
  cmux_do send --surface "$SERVER_SURFACE" "$SERVER_COMMAND\n"
  started="Restarted the server in its Dock tab."
fi

# The resume binding lets cmux rerun the server after a relaunch, once the user approves
# it, and marks the tab as the server's. An unchanged binding keeps its approval.
if [ "$SERVER_RESUME" != "${SERVER_ARGS[*]}" ]; then
  cmux_do surface resume set --surface "$SERVER_SURFACE" --name "Linear sidebar server" \
    --cwd "$ROOT" -- "${SERVER_ARGS[@]}"
fi

if [ -n "$started" ]; then
  reload_page_when_server_answers
  echo "$started"
else
  echo "The Linear page and its server are already running in the Dock."
fi

auto_resume="$("$CMUX" surface resume show --json --surface "$SERVER_SURFACE" 2>/dev/null |
  python3 -c 'import json, sys; print((json.load(sys.stdin).get("resume_binding") or {}).get("auto_resume") is True)' \
  2>/dev/null || echo False)"
if [ "$auto_resume" != "True" ]; then
  cat <<'EOF'

One more step, once, so the sidebar comes back after cmux restarts:
  1. In the Dock (right sidebar), click the "Linear server" tab.
  2. Right-click inside it, choose Resume Commands → Edit, and save without changes.
  3. In the "Allow Resume Command?" prompt, choose Auto-Restore.
  4. Click the "Linear" tab to return to the issues.
EOF
fi
