"""Settings from options and environment variables, and the API key from the environment or the login Keychain."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
KEYCHAIN_SERVICE = "cmux-linear-sidebar"
KEYCHAIN_TIMEOUT_SECONDS = 30
DEFAULT_PORT = 47801
DEFAULT_REFRESH_SECONDS = 60
# `security` exits with the low byte of the Security framework's status: 44 is errSecItemNotFound.
_ITEM_NOT_FOUND = 44


class SettingsError(Exception):
    """An option or environment variable holds a value the sidebar cannot use."""


class KeychainError(Exception):
    """The Keychain did not hand out the key: it is locked, refused access, or did not answer."""


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    cmux_bin: str
    refresh_seconds: int


def whole_number(name: str, raw: str, maximum: Optional[int] = None) -> int:
    try:
        value = int(raw)
    except ValueError:
        raise SettingsError(f"{name} must be a whole number, not {raw!r}") from None
    if value <= 0 or (maximum is not None and value > maximum):
        limit = f"between 1 and {maximum}" if maximum is not None else "greater than zero"
        raise SettingsError(f"{name} must be {limit}")
    return value


def _from_environment(name: str, default: int, maximum: Optional[int] = None) -> int:
    raw = os.environ.get(name)
    return whole_number(name, raw, maximum) if raw else default


def load(port: Optional[int] = None, refresh_seconds: Optional[int] = None) -> Settings:
    """Options given on the command line win over the environment variables."""
    return Settings(
        host="127.0.0.1",
        port=port or _from_environment("LINEAR_SIDEBAR_PORT", DEFAULT_PORT, 65535),
        # cmux terminals export the bundled CLI path; plain `cmux` covers the rest.
        cmux_bin=os.environ.get("CMUX_BUNDLED_CLI_PATH") or "cmux",
        refresh_seconds=refresh_seconds or _from_environment("LINEAR_SIDEBAR_REFRESH_SECONDS",
                                                             DEFAULT_REFRESH_SECONDS),
    )


def keychain_password(service: str) -> Optional[str]:
    """A generic password from the login Keychain, as stored by install.sh; None when there is none.

    Raises KeychainError when the Keychain does not answer, so a locked Keychain
    is not taken for a missing key.
    """
    try:
        result = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-s", service, "-w"],
            capture_output=True, text=True, timeout=KEYCHAIN_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        raise KeychainError(f"The Keychain did not answer within {KEYCHAIN_TIMEOUT_SECONDS} seconds; "
                            "it may be locked") from None
    except OSError as error:
        raise KeychainError(f"Could not run /usr/bin/security: {error.strerror or error}") from None
    if result.returncode == _ITEM_NOT_FOUND:
        return None
    if result.returncode != 0:
        said = (result.stderr.strip().splitlines() or [""])[-1].replace("security: ", "", 1)
        raise KeychainError(f"The Keychain did not hand out the key ({said or f'exit status {result.returncode}'})")
    return result.stdout.strip() or None


def api_key() -> Tuple[Optional[str], Optional[str]]:
    """The Linear key and where it came from: the environment, then the Keychain; (None, None) without one.

    Raises KeychainError. Reading the Keychain can wait on a macOS unlock prompt.
    """
    if os.environ.get("LINEAR_API_KEY"):
        return os.environ["LINEAR_API_KEY"], "environment"
    from_keychain = keychain_password(KEYCHAIN_SERVICE)
    if from_keychain:
        return from_keychain, "Keychain"
    return None, None
