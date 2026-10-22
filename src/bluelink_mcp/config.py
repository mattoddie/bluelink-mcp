"""Configuration from environment variables."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

REGIONS = {
    "europe": 1,
    "canada": 2,
    "usa": 3,
    "china": 4,
    "australia": 5,
    "india": 6,
    "nz": 7,
    "brazil": 8,
    "europe-cci": 9,
}
BRANDS = {"kia": 1, "hyundai": 2, "genesis": 3}


class ConfigError(ValueError):
    """Raised when the environment does not describe a usable configuration."""


# A ${user_config.<key>} reference that an MCP Bundle host such as Claude Desktop passed
# through unchanged because the user left an optional setting empty. It means "not set".
_PLACEHOLDER = re.compile(r"^\$\{user_config\.[^}]*\}$")


def without_placeholders(env: dict[str, str]) -> dict[str, str]:
    """The environment without variables that hold an unfilled bundle placeholder."""
    return {k: v for k, v in env.items() if not _PLACEHOLDER.match(v.strip())}


def _bool(env: dict[str, str], name: str, default: bool = False) -> bool:
    raw = env.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"{name} must be true or false, not {raw!r}")


def _int(env: dict[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a whole number, not {raw!r}") from None


def _secret(env: dict[str, str], name: str) -> str:
    """A secret from NAME, or from the file NAME_FILE names (for Docker secrets).

    Only the trailing line break of a file is removed, so a password can still end in a space.
    """
    file = env.get(f"{name}_FILE", "").strip()
    if not file:
        return env.get(name, "")
    if env.get(name):
        raise ConfigError(f"Set {name} or {name}_FILE, not both")
    try:
        return Path(file).expanduser().read_text().rstrip("\r\n")
    except OSError as ex:
        raise ConfigError(
            f"Could not read {name}_FILE ({file}): {ex.strerror or type(ex).__name__}"
        ) from None


# Shorter bearer tokens could be guessed.
MIN_AUTH_TOKEN_LENGTH = 16


def _list(env: dict[str, str], name: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in env.get(name, "").split(",") if item.strip())


LOG_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


def log_level(env: dict[str, str]) -> tuple[int, int]:
    """The log level from LOG_LEVEL, and the level for hyundai_kia_connect_api's loggers.

    The library logs whole Bluelink requests and responses (tokens, VIN, location), some
    of them at ERROR, so it is heard from only when LOG_LEVEL=debug.
    """
    raw = without_placeholders(env).get("LOG_LEVEL", "").strip().lower() or "info"
    if raw not in LOG_LEVELS:
        raise ConfigError(f"LOG_LEVEL must be one of {', '.join(LOG_LEVELS)}, not {raw!r}")
    level = LOG_LEVELS[raw]
    return level, logging.DEBUG if level == logging.DEBUG else logging.CRITICAL + 1


def _default_state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "bluelink-mcp"


@dataclass(frozen=True)
class Config:
    username: str
    password: str = field(repr=False)
    pin: str = field(default="", repr=False)
    region: int = REGIONS["europe"]
    brand: int = BRANDS["hyundai"]
    language: str = "en"
    geocode: bool = False
    allow_location: bool = False
    location_precision: int = 4
    token_file: Path | None = None
    allow_writes: bool = False
    allow_unlock: bool = False
    min_refresh_minutes: int = 10
    min_command_seconds: int = 60
    login_retry_minutes: int = 5
    cache_max_age_seconds: int = 60
    transport: str = "stdio"
    http_host: str = "127.0.0.1"
    http_port: int = 8080
    auth_token: str = field(default="", repr=False)
    allowed_hosts: tuple[str, ...] = ()
    allowed_origins: tuple[str, ...] = ()
    audit_log: str = ""

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Config:
        env = without_placeholders(dict(os.environ if env is None else env))

        username = env.get("BLUELINK_USERNAME", "").strip()
        password = _secret(env, "BLUELINK_PASSWORD")
        if not username or not password:
            raise ConfigError(
                "BLUELINK_USERNAME and BLUELINK_PASSWORD (or BLUELINK_PASSWORD_FILE) must be set"
            )

        region_name = (env.get("BLUELINK_REGION", "").strip() or "europe").lower()
        if region_name not in REGIONS:
            raise ConfigError(f"BLUELINK_REGION must be one of {', '.join(REGIONS)}, not {region_name!r}")
        brand_name = (env.get("BLUELINK_BRAND", "").strip() or "hyundai").lower()
        if brand_name not in BRANDS:
            raise ConfigError(f"BLUELINK_BRAND must be one of {', '.join(BRANDS)}, not {brand_name!r}")

        token_raw = env.get("BLUELINK_TOKEN_FILE")
        if token_raw is None:
            token_file: Path | None = _default_state_dir() / "token.json"
        else:
            # An empty value turns token caching off.
            token_file = Path(token_raw).expanduser() if token_raw.strip() else None

        transport = (env.get("MCP_TRANSPORT", "").strip() or "stdio").lower()
        if transport not in ("stdio", "http"):
            raise ConfigError(f"MCP_TRANSPORT must be stdio or http, not {transport!r}")

        allow_writes = _bool(env, "BLUELINK_ALLOW_WRITES")
        allow_unlock = _bool(env, "BLUELINK_ALLOW_UNLOCK")
        if allow_unlock and not allow_writes:
            raise ConfigError("BLUELINK_ALLOW_UNLOCK needs BLUELINK_ALLOW_WRITES=true too")

        allow_location = _bool(env, "BLUELINK_ALLOW_LOCATION")
        geocode = _bool(env, "BLUELINK_GEOCODE")
        if geocode and not allow_location:
            # Geocoding sends the car's coordinates to OpenStreetMap, for a tool that isn't there.
            raise ConfigError("BLUELINK_GEOCODE needs BLUELINK_ALLOW_LOCATION=true too")
        location_precision = _int(env, "BLUELINK_LOCATION_PRECISION", 4)
        if not 0 <= location_precision <= 15:
            raise ConfigError("BLUELINK_LOCATION_PRECISION must be between 0 and 15 decimal places")

        auth_token = _secret(env, "MCP_AUTH_TOKEN").strip()
        if transport == "http" and auth_token and len(auth_token) < MIN_AUTH_TOKEN_LENGTH:
            raise ConfigError(
                f"MCP_AUTH_TOKEN must be at least {MIN_AUTH_TOKEN_LENGTH} characters; "
                "generate one with: openssl rand -hex 32"
            )

        hosts = _list(env, "MCP_ALLOWED_HOSTS")
        origins = _list(env, "MCP_ALLOWED_ORIGINS")
        if origins and not hosts:
            raise ConfigError("MCP_ALLOWED_ORIGINS needs MCP_ALLOWED_HOSTS too")

        min_refresh_minutes = _int(env, "BLUELINK_MIN_REFRESH_MINUTES", 10)
        if min_refresh_minutes < 1:
            raise ConfigError(
                "BLUELINK_MIN_REFRESH_MINUTES must be at least 1, to protect the car's 12V battery"
            )

        min_command_seconds = _int(env, "BLUELINK_MIN_COMMAND_SECONDS", 60)
        if min_command_seconds < 0:
            raise ConfigError("BLUELINK_MIN_COMMAND_SECONDS must not be negative")

        login_retry_minutes = _int(env, "BLUELINK_LOGIN_RETRY_MINUTES", 5)
        if login_retry_minutes < 0:
            raise ConfigError("BLUELINK_LOGIN_RETRY_MINUTES must not be negative")

        return cls(
            username=username,
            password=password,
            pin=_secret(env, "BLUELINK_PIN").strip(),
            region=REGIONS[region_name],
            brand=BRANDS[brand_name],
            language=env.get("BLUELINK_LANGUAGE", "en").strip() or "en",
            geocode=geocode,
            allow_location=allow_location,
            location_precision=location_precision,
            token_file=token_file,
            allow_writes=allow_writes,
            allow_unlock=allow_unlock,
            min_refresh_minutes=min_refresh_minutes,
            min_command_seconds=min_command_seconds,
            login_retry_minutes=login_retry_minutes,
            cache_max_age_seconds=_int(env, "BLUELINK_CACHE_SECONDS", 60),
            transport=transport,
            http_host=env.get("MCP_HTTP_HOST", "127.0.0.1").strip() or "127.0.0.1",
            http_port=_int(env, "MCP_HTTP_PORT", 8080),
            auth_token=auth_token,
            allowed_hosts=hosts,
            allowed_origins=origins,
            audit_log=audit_log(env),
        )


AUDIT_LOG_STDERR = "stderr"


def audit_log(env: dict[str, str]) -> str:
    """BLUELINK_AUDIT_LOG: "" (off), "stderr" or the path of a file, which must be writable."""
    raw = env.get("BLUELINK_AUDIT_LOG", "").strip()
    if not raw or raw.lower() == AUDIT_LOG_STDERR:
        return raw.lower()
    path = str(Path(raw).expanduser())
    try:
        open_audit_log(path).close()
    except OSError as ex:
        raise ConfigError(
            f"BLUELINK_AUDIT_LOG: can't write to {path}: {ex.strerror or type(ex).__name__}"
        ) from None
    return path


def open_audit_log(path: str):
    """Open the audit log for appending, creating it readable by its owner only."""
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    return os.fdopen(fd, "a", encoding="utf-8")
