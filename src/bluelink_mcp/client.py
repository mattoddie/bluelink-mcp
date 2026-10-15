"""A thread-safe wrapper around hyundai_kia_connect_api's VehicleManager.

The library is synchronous and keeps login state on the manager, so every call
goes through one lock. The login token is cached on disk because repeated
password logins can get a Bluelink account temporarily blocked.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from hyundai_kia_connect_api import Vehicle, VehicleManager
from hyundai_kia_connect_api.const import ORDER_STATUS
from hyundai_kia_connect_api.exceptions import AuthenticationOTPRequired
from hyundai_kia_connect_api.Token import Token
from mcp.server.mcpserver.exceptions import ToolError

from .config import Config

_LOGGER = logging.getLogger(__name__)

T = TypeVar("T")


class BluelinkError(ToolError):
    """An error with a message that is safe and useful to show the assistant."""


class BluelinkClient:
    def __init__(self, config: Config, manager_factory: Callable[..., Any] = VehicleManager):
        self._config = config
        self._lock = threading.RLock()
        self._manager_factory = manager_factory
        self._manager: Any = None
        self._saved_token: dict | None = None
        self._cache_updated: dict[str, float] = {}
        self._last_force_refresh: dict[str, float] = {}

    # --- session -------------------------------------------------------------

    def _load_token(self) -> Token | None:
        path = self._config.token_file
        if path is None or not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError) as ex:
            _LOGGER.warning("Ignoring unreadable token cache %s: %s", path, ex)
            return None
        # A cache written for another account must not be reused.
        if data.get("username") != self._config.username:
            return None
        self._saved_token = data
        return Token.from_dict(data)

    def _save_token(self) -> None:
        path: Path | None = self._config.token_file
        token = getattr(self._manager, "token", None)
        if path is None or not isinstance(token, Token):
            return
        data = token.to_dict()
        if data == self._saved_token:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(data, f)
            os.replace(tmp, path)
            self._saved_token = data
        except OSError as ex:
            _LOGGER.warning("Could not write token cache %s: %s", path, ex)

    def _session(self) -> Any:
        """Return a logged-in manager, logging in or refreshing the token as needed."""
        if self._manager is None:
            c = self._config
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
        try:
            self._manager.check_and_refresh_token()
        except AuthenticationOTPRequired:
            raise BluelinkError(
                "Bluelink asked for a one-time passcode. Log in once in the Bluelink app, then try again."
            ) from None
        except Exception as ex:
            # Drop the session so the next call starts from a clean login.
            self._manager = None
            raise BluelinkError(f"Could not log in to Bluelink: {ex}") from ex
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
                raise BluelinkError(f"Bluelink request failed: {ex}") from ex
            finally:
                self._save_token()

    # --- vehicles ------------------------------------------------------------

    def vehicles(self) -> list[Vehicle]:
        return self.call(lambda m: list(m.vehicles.values()))

    def resolve(self, manager: Any, vehicle: str | None) -> Vehicle:
        """Find a vehicle by ID, VIN or name. With one vehicle, the argument is optional."""
        vehicles: list[Vehicle] = list(manager.vehicles.values())
        if not vehicles:
            raise BluelinkError("No vehicles are registered on this Bluelink account.")
        if not vehicle:
            if len(vehicles) == 1:
                return vehicles[0]
            names = ", ".join(f"{v.name} ({v.id})" for v in vehicles)
            raise BluelinkError(f"This account has several vehicles; say which one: {names}")
        wanted = vehicle.strip().lower()
        for v in vehicles:
            if wanted in (str(v.id).lower(), str(v.VIN or "").lower(), str(v.name or "").lower()):
                return v
        matches = [v for v in vehicles if wanted in str(v.name or "").lower()]
        if len(matches) == 1:
            return matches[0]
        raise BluelinkError(f"No vehicle matches {vehicle!r}. Use list_vehicles to see them.")

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
            m.force_refresh_vehicle_state(v.id)
            self._last_force_refresh[v.id] = time.monotonic()
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
        action: Callable[[Any, str], str | None],
        wait: bool,
        timeout: int = 90,
    ) -> dict[str, Any]:
        """Send a remote command and, if asked, wait for the car to confirm it."""

        def run(m: Any) -> dict[str, Any]:
            v = self.resolve(m, vehicle)
            action_id = action(m, v.id)
            # Commands change state, so the next status read must go back to Bluelink.
            self._cache_updated.pop(v.id, None)
            result: dict[str, Any] = {"vehicle": v.name, "action_id": action_id}
            if not action_id:
                result["status"] = "sent"
                return result
            if not wait:
                result["status"] = "pending"
                return result
            status = m.check_action_status(v.id, action_id, synchronous=True, timeout=timeout)
            result["status"] = describe_order_status(status)
            return result

        return self.call(run)

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
