"""A thread-safe wrapper around hyundai_kia_connect_api's VehicleManager.

The library is synchronous and keeps login state on the manager, so every call
goes through one lock. The login token is cached on disk because repeated
password logins can get a Bluelink account temporarily blocked.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from hyundai_kia_connect_api import Vehicle, VehicleManager
from hyundai_kia_connect_api.const import ORDER_STATUS
from hyundai_kia_connect_api.exceptions import (
    APIError,
    AuthenticationError,
    AuthenticationOTPRequired,
    ConsentRequiredError,
    DeviceIDError,
    DuplicateRequestError,
    InvalidAPIResponseError,
    NoDataFound,
    PINMissingError,
    RateLimitingError,
    RequestTimeoutError,
    ServiceTemporaryUnavailable,
    UnsupportedControlError,
)
from hyundai_kia_connect_api.Token import Token
from mcp.server.mcpserver.exceptions import ToolError

from .config import BRANDS, REGIONS, Config, ConfigError

_LOGGER = logging.getLogger(__name__)

T = TypeVar("T")

# Fields a usable token cache must have, all strings.
_TOKEN_FIELDS = ("username", "access_token", "valid_until")

# Library exceptions and what to tell the assistant instead of their text, which
# can contain fragments of Bluelink's responses. Subclasses come before their bases.
_EXPLANATIONS: list[tuple[type[BaseException], str]] = [
    (
        ConsentRequiredError,
        "Bluelink wants you to accept its terms. Log in once in the official app, then try again.",
    ),
    (
        AuthenticationError,
        "Bluelink rejected the login. Check BLUELINK_USERNAME, BLUELINK_PASSWORD, BLUELINK_PIN, "
        "BLUELINK_REGION and BLUELINK_BRAND.",
    ),
    (PINMissingError, "This needs your app PIN. Set BLUELINK_PIN."),
    (RateLimitingError, "Bluelink's API limit has been reached. Try again later (the limit resets daily)."),
    (DuplicateRequestError, "Bluelink is still handling an earlier command. Wait a minute and try again."),
    (UnsupportedControlError, "This car does not support that command."),
    (
        RequestTimeoutError,
        "The car did not answer Bluelink in time, perhaps for lack of mobile signal. Try later.",
    ),
    (ServiceTemporaryUnavailable, "Bluelink's remote service is temporarily unavailable. Try again later."),
    (NoDataFound, "Bluelink has no data for this car or period."),
    (DeviceIDError, "Bluelink rejected this session's device ID. Try again."),
    (InvalidAPIResponseError, "Bluelink sent a response that could not be read. Try again later."),
    (APIError, "Bluelink returned an error."),
    (OSError, "Could not reach Bluelink. Check the network connection and try again."),
]


class BluelinkError(ToolError):
    """An error with a message that is safe and useful to show the assistant."""


def explain(ex: BaseException) -> str:
    """A fixed, safe description of a library exception. The details are logged at DEBUG."""
    _LOGGER.debug("Bluelink error", exc_info=ex)
    message = next(
        (text for kind, text in _EXPLANATIONS if isinstance(ex, kind)),
        "Unexpected error; run the server with debug logging for details.",
    )
    return f"{message} ({type(ex).__name__})"


def check_supported(config: Config) -> None:
    """Raise ConfigError if the library has no API for this region and brand."""
    try:
        VehicleManager.get_implementation_by_region_brand(config.region, config.brand, config.language)
    except Exception:
        region = next(k for k, v in REGIONS.items() if v == config.region)
        brand = next(k for k, v in BRANDS.items() if v == config.brand)
        raise ConfigError(f"BLUELINK_BRAND={brand} is not supported in BLUELINK_REGION={region}") from None


class BluelinkClient:
    def __init__(
        self,
        config: Config,
        manager_factory: Callable[..., Any] = VehicleManager,
        *,
        command_wait_seconds: float = 60,
        poll_seconds: float = 5,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._config = config
        self._lock = threading.RLock()
        self._manager_factory = manager_factory
        self._manager: Any = None
        self._saved_token: dict | None = None
        self._login_failed_at: float | None = None
        self._cache_updated: dict[str, float] = {}
        self._last_force_refresh: dict[str, float] = {}
        # Waiting for a command polls with the lock released between checks, and stays
        # under the 60-120 s request timeouts common in MCP clients.
        self._command_wait_seconds = command_wait_seconds
        self._poll_seconds = poll_seconds
        self._sleep = sleep

    # --- session -------------------------------------------------------------

    def _load_token(self) -> Token | None:
        path = self._config.token_file
        if path is None or not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError) as ex:
            _LOGGER.warning("Ignoring unreadable token cache %s: %s", path, type(ex).__name__)
            return None
        if not isinstance(data, dict) or not all(isinstance(data.get(k), str) for k in _TOKEN_FIELDS):
            _LOGGER.warning("Ignoring token cache %s: not a token this server wrote", path)
            return None
        # A cache written for another account must not be reused.
        if data["username"] != self._config.username:
            return None
        try:
            token = Token.from_dict(data)
        except (TypeError, ValueError) as ex:
            _LOGGER.warning("Ignoring token cache %s: %s", path, type(ex).__name__)
            return None
        # The library falls back to a password login with the token's own copy of the
        # password and PIN. The cache doesn't store them (older versions did, and a
        # stale copy would fail every time and risk locking the account), so always use
        # the configured ones. A cache that still holds them is rewritten without them
        # on the next save, because it then differs from what would be written.
        token.password = self._config.password
        token.pin = self._config.pin
        self._saved_token = data
        return token

    @staticmethod
    def _cache_data(token: Token) -> dict:
        """The token as stored on disk: everything except the password and PIN."""
        data = token.to_dict()
        data["password"] = None
        data["pin"] = None
        return data

    def _save_token(self) -> None:
        path: Path | None = self._config.token_file
        token = getattr(self._manager, "token", None)
        if path is None or not isinstance(token, Token):
            return
        data = self._cache_data(token)
        if data == self._saved_token:
            return
        tmp: str | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # A unique temporary file (created with mode 0600) per write, so several
            # server processes sharing the cache can't interleave their writes.
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                json.dump(data, f)
            os.replace(tmp, path)
            tmp = None
            self._saved_token = data
        except OSError as ex:
            _LOGGER.warning("Could not write token cache %s: %s", path, ex)
        finally:
            if tmp is not None:
                Path(tmp).unlink(missing_ok=True)

    def _discard_token(self) -> None:
        """Forget the cached session, so the next login starts from scratch."""
        self._saved_token = None
        path = self._config.token_file
        if path is None:
            return
        try:
            path.unlink(missing_ok=True)
        except OSError as ex:
            _LOGGER.warning("Could not remove token cache %s: %s", path, ex)

    def _session(self) -> Any:
        """Return a logged-in manager, logging in or refreshing the token as needed."""
        c = self._config
        if self._manager is None and self._login_failed_at is not None:
            since = time.monotonic() - self._login_failed_at
            if since < c.login_retry_minutes * 60:
                wait = int((c.login_retry_minutes * 60 - since) / 60) + 1
                raise BluelinkError(
                    f"Logging in to Bluelink failed {int(since / 60)} min ago. To avoid getting the "
                    f"account locked, the server waits before trying again: try in about {wait} min. "
                    "If the password changed, update BLUELINK_PASSWORD and restart the server."
                )
        try:
            if self._manager is None:
                self._manager = self._manager_factory(
                    region=c.region,
                    brand=c.brand,
                    username=c.username,
                    password=c.password,
                    pin=c.pin,
                    geocode_api_enable=c.geocode,
                    token=self._load_token(),
                    language=c.language,
                )
            self._manager.check_and_refresh_token()
        except AuthenticationOTPRequired:
            # The library logs in with the password whenever it has no token, and an OTP
            # request leaves it without one. Without the backoff, every tool call would
            # make another full password login, which is what gets accounts locked.
            self._manager = None
            self._login_failed_at = time.monotonic()
            raise BluelinkError(
                "Bluelink asked for a one-time passcode. Log in once in the official app, then try again."
            ) from None
        except Exception as ex:
            # Drop the session so the next call starts from a clean login.
            self._manager = None
            if isinstance(ex, AuthenticationError):
                # Bluelink rejected the login: the cached session is no good, and retrying
                # straight away risks getting the account locked, so wait first.
                self._login_failed_at = time.monotonic()
                self._discard_token()
            elif isinstance(ex, OSError):
                # Bluelink was never reached, so there is nothing to protect the account from.
                _LOGGER.debug("Bluelink login error", exc_info=ex)
                raise BluelinkError(
                    "Could not reach Bluelink to log in. Check the network connection and try again "
                    f"({type(ex).__name__})."
                ) from ex
            raise BluelinkError(f"Could not log in to Bluelink: {explain(ex)}") from ex
        self._login_failed_at = None
        self._save_token()
        return self._manager

    def call(self, fn: Callable[[Any], T]) -> T:
        """Run fn(manager) under the lock with a valid session."""
        with self._lock:
            manager = self._session()
            try:
                return fn(manager)
            except BluelinkError:
                raise
            except Exception as ex:
                raise BluelinkError(f"Bluelink request failed: {explain(ex)}") from ex
            finally:
                self._save_token()

    # --- vehicles ------------------------------------------------------------

    def vehicles(self) -> list[Vehicle]:
        return self.call(lambda m: list(m.vehicles.values()))

    def resolve(self, manager: Any, vehicle: str | None, exact: bool = False) -> Vehicle:
        """Find a vehicle by ID, VIN or name. With one vehicle, the argument is optional.

        A unique part of a name also matches, unless `exact` is set and the account has
        more than one vehicle.
        """
        vehicles: list[Vehicle] = list(manager.vehicles.values())
        if not vehicles:
            raise BluelinkError("No vehicles are registered on this Bluelink account.")
        wanted = (vehicle or "").strip().lower()
        if not wanted:
            if len(vehicles) == 1:
                return vehicles[0]
            names = ", ".join(f"{v.name} ({v.id})" for v in vehicles)
            raise BluelinkError(f"This account has several vehicles; say which one: {names}")
        matches = [
            v
            for v in vehicles
            if wanted in (str(v.id).lower(), str(v.VIN or "").lower(), str(v.name or "").lower())
        ]
        if not matches and not (exact and len(vehicles) > 1):
            matches = [v for v in vehicles if wanted in str(v.name or "").lower()]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise BluelinkError(f"Several vehicles match {vehicle!r}; give the ID or VIN from list_vehicles.")
        hint = " Give the exact name, VIN or ID." if exact else ""
        raise BluelinkError(f"No vehicle matches {vehicle!r}. Use list_vehicles to see them.{hint}")

    def status(self, vehicle: str | None) -> Vehicle:
        """Return the vehicle with the state Bluelink last received from the car."""

        def run(m: Any) -> Vehicle:
            v = self.resolve(m, vehicle)
            age = time.monotonic() - self._cache_updated.get(v.id, float("-inf"))
            if age > self._config.cache_max_age_seconds:
                m.update_vehicle_with_cached_state(v.id)
                self._cache_updated[v.id] = time.monotonic()
            return v

        return self.call(run)

    def force_refresh(self, vehicle: str | None) -> Vehicle:
        """Wake the car and ask it for fresh state. Rate limited to protect its 12V battery."""

        def run(m: Any) -> Vehicle:
            v = self.resolve(m, vehicle)
            min_gap = self._config.min_refresh_minutes * 60
            since = time.monotonic() - self._last_force_refresh.get(v.id, float("-inf"))
            if since < min_gap:
                wait = int((min_gap - since) / 60) + 1
                raise BluelinkError(
                    f"The car was woken for a refresh {int(since / 60)} min ago. To spare its "
                    f"12V battery and the daily API quota, try again in about {wait} min, or use "
                    "get_vehicle_status for the last known state."
                )
            # Count the attempt even if it fails: a failed wake-up may still have reached the car.
            self._last_force_refresh[v.id] = time.monotonic()
            m.force_refresh_vehicle_state(v.id)
            m.update_vehicle_with_cached_state(v.id)
            self._cache_updated[v.id] = time.monotonic()
            return v

        return self.call(run)

    def trip_info(self, vehicle: str | None, period: str) -> Any:
        def run(m: Any) -> Any:
            v = self.resolve(m, vehicle)
            if len(period) == 6:
                m.update_month_trip_info(v.id, period)
                return v.month_trip_info
            m.update_day_trip_info(v.id, period)
            return v.day_trip_info

        return self.call(run)

    # --- commands ------------------------------------------------------------

    def command(
        self,
        vehicle: str | None,
        action: Callable[[Any, Vehicle], str | None],
        wait: bool,
        exact: bool = False,
    ) -> dict[str, Any]:
        """Send a remote command and, if asked, wait for the car to confirm it.

        Only sending and each status check hold the lock, so other tools keep working
        while the car takes its time to answer.
        """

        def send(m: Any) -> tuple[Vehicle, str | None]:
            v = self.resolve(m, vehicle, exact=exact)
            action_id = action(m, v)
            # Commands change state, so the next status read must go back to Bluelink.
            self._cache_updated.pop(v.id, None)
            return v, action_id

        v, action_id = self.call(send)
        result: dict[str, Any] = {"vehicle": v.name, "action_id": action_id}
        if not action_id:
            result["status"] = "sent"
            return result
        result["status"] = "pending"
        if not wait:
            return result
        deadline = time.monotonic() + self._command_wait_seconds
        while (left := deadline - time.monotonic()) > 0:
            self._sleep(min(self._poll_seconds, left))
            try:
                status = describe_order_status(
                    self.call(lambda m: m.check_action_status(v.id, action_id, synchronous=False))
                )
            except BluelinkError as ex:
                result["note"] = f"The command was sent, but checking on it failed: {ex}"
                return result
            if status not in ("pending", "unknown"):
                result["status"] = status
                return result
        return result

    def action_status(self, vehicle: str | None, action_id: str) -> str:
        def run(m: Any) -> str:
            v = self.resolve(m, vehicle)
            return describe_order_status(m.check_action_status(v.id, action_id))

        return self.call(run)


def describe_order_status(status: Any) -> str:
    if isinstance(status, ORDER_STATUS):
        return {
            ORDER_STATUS.PENDING: "pending",
            ORDER_STATUS.SUCCESS: "success",
            ORDER_STATUS.FAILED: "failed",
            ORDER_STATUS.TIMEOUT: "timeout",
        }.get(status, status.name.lower())
    return str(status).lower() if status is not None else "unknown"
