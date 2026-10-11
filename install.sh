#!/usr/bin/env bash
# Sets up the cmux Linear sidebar: stores your Linear API key in the macOS Keychain
# and opens the sidebar in the cmux Dock. Run it from a terminal inside cmux.
#
# Usage: ./install.sh            install, or bring the sidebar back
#        ./install.sh --new-key  replace the stored Linear API key
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
SERVICE="cmux-linear-sidebar"
CMUX="${CMUX_BUNDLED_CLI_PATH:-cmux}"

step() { printf '\n\033[1m%s\033[0m\n' "$*"; }
fail() { printf '\n\033[31mError:\033[0m %s\n' "$*" >&2; exit 1; }

case "${1:-}" in
  "" | --new-key) ;;
  *) fail "Unknown option \"$1\". Usage: $0 [--new-key]" ;;
esac

step "Checking requirements"
[ "$(uname -s)" = "Darwin" ] || fail "The sidebar runs on macOS only."
[ -n "${CMUX_SOCKET_PATH:-}" ] || fail "Run this script from a terminal inside cmux."
# Without the command line tools, /usr/bin/git and /usr/bin/python3 only show an install dialog.
xcode-select -p >/dev/null 2>&1 ||
  fail "Apple's command line tools are missing. Install them with: xcode-select --install"
command -v python3 >/dev/null 2>&1 ||
  fail "python3 is missing. Install Apple's command line tools: xcode-select --install"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null ||
  fail "Python 3.9 or newer is required. Install Apple's command line tools: xcode-select --install"
"$CMUX" workspace list --json >/dev/null 2>&1 ||
  fail "cmux did not answer. Update cmux to 0.65.0 or newer and run this from a cmux terminal."
echo "macOS, cmux and Python $(python3 -c 'import platform; print(platform.python_version())') are ready."

step "Linear API key"
key_saved=false
if [ "${1:-}" != "--new-key" ] && /usr/bin/security find-generic-password -s "$SERVICE" -w >/dev/null 2>&1; then
  echo "Using the key already in your Keychain. To replace it, run: ./install.sh --new-key"
else
  echo "Create a personal API key in Linear: Settings → Security & access → Personal API keys."
  echo "The sidebar only reads issues and projects."
  while true; do
    read -r -s -p "Paste the key and press Enter (the input stays hidden): " key
    echo
    [ -n "$key" ] || fail "No key entered."
    if [[ ! "$key" =~ ^lin_api_[A-Za-z0-9]+$ ]]; then
      echo "That does not look like a Linear API key: it should start with lin_api_. Try again."
      continue
    fi
    if who="$(printf '%s' "$key" | "$ROOT/cmux-linear-sidebar" --check-key 2>&1)"; then
      echo "The key works: $who"
      break
    fi
    echo "Linear did not accept the key ($who). Try again."
  done
  # Through `security -i` on stdin, so the key never appears in a process list.
  printf 'add-generic-password -U -a "%s" -s "%s" -w "%s"\n' "$USER" "$SERVICE" "$key" |
    /usr/bin/security -i >/dev/null 2>&1 || true
  stored="$(/usr/bin/security find-generic-password -s "$SERVICE" -w 2>/dev/null || true)"
  [ "$stored" = "$key" ] ||
    fail "Could not save the key in your Keychain. Unlock the login keychain and run ./install.sh again."
  unset key stored
  key_saved=true
  echo "Saved in your login Keychain as \"$SERVICE\"."
fi

step "Opening the sidebar in the cmux Dock"
if $key_saved; then
  # The server reads the key when it starts, so a running one is restarted.
  "$ROOT/scripts/dock-setup.sh" --restart
else
  "$ROOT/scripts/dock-setup.sh"
fi

step "Done"
echo "The Linear tab in the Dock shows the issues of the workspace you select."
