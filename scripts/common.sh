# Shared by install.sh, uninstall.sh and scripts/dock-setup.sh; source it with bash.
# Every function returns 0 unless it says otherwise, so callers can use `set -e`.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER="$ROOT/cmux-linear-sidebar"
DEFAULT_PORT=47801
PORT="${LINEAR_SIDEBAR_PORT:-$DEFAULT_PORT}"
URL="http://127.0.0.1:$PORT/"
CMUX="${CMUX_BUNDLED_CLI_PATH:-cmux}"
PAGE_PANE="" PAGE_SURFACE="" SERVER_SURFACE="" SERVER_RESUME="" PANE_TERMINALS=""

# Sets the variables above from the cmux Dock (see scripts/dock_surfaces.py). Returns 1
# when cmux or the parser fails: `eval` of an empty output would succeed silently.
find_tabs() {
  local parsed
  parsed="$(python3 "$ROOT/scripts/dock_surfaces.py" "$CMUX" "$URL" "$@")" || return 1
  eval "$parsed"
}

# The PID listening on PORT, whatever program it is, or nothing.
listener_pid() {
  lsof -nP -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | head -n 1 || true
}

# True when the sidebar server, not some other program, answers on PORT.
server_answers() {
  curl -fsS --max-time 2 "${URL}api/state" 2>/dev/null |
    python3 -c 'import json, sys; sys.exit(json.load(sys.stdin).get("app") != "cmux-linear-sidebar")' \
      2>/dev/null
}

# The sidebar server's PID, or nothing: another program on PORT is never touched.
server_pid() {
  local pid
  pid="$(listener_pid)"
  [ -n "$pid" ] || return 0
  # The program's own path, not a file inside its folder: python3 …/cmux-linear-sidebar [options]
  case " $(ps -p "$pid" -o command= 2>/dev/null || true) " in
    *[/' ']cmux-linear-sidebar' '*) ;;
    *) return 0 ;;
  esac
  if server_answers; then
    echo "$pid"
  fi
}

# Stops the server with this PID and waits until PORT is free; returns 1 if it is not.
stop_server() {
  kill "$1" 2>/dev/null || true
  for _ in $(seq 1 20); do
    [ -n "$(listener_pid)" ] || return 0
    sleep 0.25
  done
  return 1
}
