#!/usr/bin/env bash
# Removes the cmux Linear sidebar: stops the server, closes its Dock tabs and deletes
# the Linear API key from the Keychain. Run it from a terminal inside cmux.
set -euo pipefail

. "$(cd "$(dirname "$0")" && pwd)/scripts/common.sh"
SERVICE="cmux-linear-sidebar"

# cmux refuses to close a terminal while a program runs in it, so stop the server first.
pid="$(server_pid)"
if [ -n "$pid" ]; then
  if stop_server "$pid"; then
    echo "Stopped the server."
  else
    echo "The server did not stop; its Dock tab may stay open." >&2
  fi
elif [ -n "$(listener_pid)" ]; then
  echo "Port $PORT belongs to another program, so it was left running."
fi

# Each step below runs even when an earlier one failed, so the key is always deleted.
if find_tabs; then
  for surface in "$SERVER_SURFACE" "$PAGE_SURFACE"; do
    if [ -n "$surface" ]; then
      "$CMUX" close-surface --surface "$surface" >/dev/null 2>&1 || true
    fi
  done
  sleep 0.5
  find_tabs || true
  if [ -n "$PAGE_SURFACE$SERVER_SURFACE" ]; then
    echo "Could not close every Linear tab in the Dock; close what is left by hand." >&2
  else
    echo "Closed the Linear tabs in the Dock."
  fi
else
  echo "Could not read the cmux Dock, so its tabs were left alone; close them by hand." >&2
fi

if /usr/bin/security delete-generic-password -s "$SERVICE" >/dev/null 2>&1; then
  echo "Deleted the Linear API key from your Keychain."
fi

echo
if [ "$ROOT" = "$HOME/.cmux-linear-sidebar" ]; then
  echo "To finish, delete the program: rm -rf ~/.cmux-linear-sidebar"
fi
echo "Its approval in cmux can be removed in Settings → Terminal → Resume Commands."
