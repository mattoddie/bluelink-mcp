import datetime as dt
import json
import logging
import os
import stat
import threading

import anyio
import pytest
from hyundai_kia_connect_api import Vehicle
from hyundai_kia_connect_api.const import ORDER_STATUS
from hyundai_kia_connect_api.exceptions import APIError, AuthenticationError, AuthenticationOTPRequired
from hyundai_kia_connect_api.Token import Token
from mcp.server.mcpserver.exceptions import ToolError

from bluelink_mcp.__main__ import MAX_BODY_BYTES, healthcheck_url, http_app, transport_security
from bluelink_mcp.client import BluelinkClient, check_supported
from bluelink_mcp.config import Config, ConfigError, log_level
from bluelink_mcp.server import build_server


class FakeManager:
    """Stands in for VehicleManager; records the calls the server makes."""

    instances: list["FakeManager"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.token = kwargs.get("token")
        self.calls: list[tuple] = []
        car = Vehicle(id="v1", name="Santa Fe", model="SANTA FE PHEV", year=2022, VIN="KMHXYZ")
        car.ev_battery_percentage = 80
        car.is_locked = True
        car.fuel_level = 55.0
        car.location = (51.5, -0.12, dt.datetime(2026, 10, 15, 8, 0))
        self.vehicles = {"v1": car}
        FakeManager.instances.append(self)

    def check_and_refresh_token(self):
        if self.token is None:
            self.calls.append(("login",))
            self.token = Token(
                username=self.kwargs["username"],
                # Like the library, the token carries the password and PIN.
                password=self.kwargs["password"],
                pin=self.kwargs["pin"],
                access_token="abc",
                valid_until=dt.datetime(2030, 1, 1, tzinfo=dt.UTC),
            )
        return False

    def update_vehicle_with_cached_state(self, vid):
        self.calls.append(("cached", vid))

    def force_refresh_vehicle_state(self, vid):
        self.calls.append(("force", vid))

    def lock(self, vid):
        self.calls.append(("lock", vid))
        return "act-1"

    def unlock(self, vid):
        self.calls.append(("unlock", vid))
        return "act-3"

    def start_climate(self, vid, options):
        self.calls.append(("climate", vid, options))
        return "act-2"

    def check_action_status(self, vid, action_id, synchronous=False, timeout=120):
        return ORDER_STATUS.SUCCESS


ENV = {"BLUELINK_USERNAME": "me@example.com", "BLUELINK_PASSWORD": "secret", "BLUELINK_PIN": "1234"}


def make(tmp_path, factory=FakeManager, client_kwargs=None, **extra):
    FakeManager.instances.clear()
    env = {**ENV, "BLUELINK_TOKEN_FILE": str(tmp_path / "token.json"), **extra}
    config = Config.from_env(env)
    # Commands poll for confirmation; don't make the tests wait between checks.
    client_kwargs = {"poll_seconds": 0, **(client_kwargs or {})}
    client = BluelinkClient(config, manager_factory=factory, **client_kwargs)
    return build_server(config, client), config


def call(server, name, args=None):
    async def go():
        result = await server.call_tool(name, args or {})
        return result.structured_content, result

    return anyio.run(go)


def tool_names(server):
    return {t.name for t in anyio.run(server.list_tools)}


def test_config_defaults_to_hyundai_europe_read_only(tmp_path):
    config = Config.from_env(ENV)
    assert (config.region, config.brand) == (1, 2)
    assert not config.allow_writes
    assert "secret" not in repr(config)


def test_config_rejects_unlock_without_writes():
    with pytest.raises(ConfigError):
        Config.from_env({**ENV, "BLUELINK_ALLOW_UNLOCK": "true"})


def test_read_only_server_has_no_command_tools(tmp_path):
    server, _ = make(tmp_path)
    names = tool_names(server)
    assert "get_vehicle_status" in names
    assert not names & {"lock_vehicle", "unlock_vehicle", "start_climate", "start_charging"}


def test_unlock_needs_its_own_switch(tmp_path):
    server, _ = make(tmp_path, BLUELINK_ALLOW_WRITES="true")
    assert "lock_vehicle" in tool_names(server)
    assert "unlock_vehicle" not in tool_names(server)
    server, _ = make(tmp_path, BLUELINK_ALLOW_WRITES="true", BLUELINK_ALLOW_UNLOCK="true")
    assert "unlock_vehicle" in tool_names(server)


def test_status_and_token_cache(tmp_path):
    server, config = make(tmp_path)
    data, _ = call(server, "get_vehicle_status")
    assert data["battery"]["ev_battery_percent"] == 80
    assert data["summary"]["locked"] is True
    # Unknown fields are dropped rather than returned as nulls.
    assert "tyres" not in data

    saved = json.loads(config.token_file.read_text())
    assert saved["access_token"] == "abc"
    assert stat.S_IMODE(os.stat(config.token_file).st_mode) == 0o600
    # The password and PIN are never written to disk.
    assert (saved["password"], saved["pin"]) == (None, None)
    assert "secret" not in config.token_file.read_text()
    assert "1234" not in config.token_file.read_text()

    # A second server reuses the cached token instead of logging in again.
    server2, _ = make(tmp_path)
    call(server2, "list_vehicles")
    assert ("login",) not in FakeManager.instances[-1].calls


def test_status_is_cached_briefly(tmp_path):
    server, _ = make(tmp_path)
    call(server, "get_vehicle_status")
    call(server, "get_energy_usage")
    assert FakeManager.instances[-1].calls.count(("cached", "v1")) == 1


def test_force_refresh_is_rate_limited(tmp_path):
    server, _ = make(tmp_path)
    call(server, "refresh_vehicle_status")
    with pytest.raises(ToolError, match="12V battery"):
        call(server, "refresh_vehicle_status")
    assert FakeManager.instances[-1].calls.count(("force", "v1")) == 1


def test_location(tmp_path):
    server, _ = make(tmp_path, BLUELINK_ALLOW_LOCATION="true")
    data, _ = call(server, "get_vehicle_location")
    assert (data["latitude"], data["longitude"]) == (51.5, -0.12)
    assert "openstreetmap.org" in data["map"]


def test_location_is_off_by_default(tmp_path):
    server, _ = make(tmp_path)
    assert "get_vehicle_location" not in tool_names(server)
    data, _ = call(server, "get_vehicle_status")
    assert "51.5" not in json.dumps(data)
    with pytest.raises(ConfigError, match="BLUELINK_GEOCODE needs BLUELINK_ALLOW_LOCATION"):
        Config.from_env({**ENV, "BLUELINK_GEOCODE": "true"})


@pytest.mark.parametrize(
    ("precision", "expected"),
    [(None, (51.5012, -0.1235)), ("2", (51.5, -0.12)), ("0", (52, 0)), ("6", (51.501234, -0.123456))],
)
def test_location_precision(tmp_path, precision, expected):
    class PreciseManager(FakeManager):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.vehicles["v1"].location = (51.50123449, -0.12345649, dt.datetime(2026, 10, 15, 8, 0))

    extra = {"BLUELINK_LOCATION_PRECISION": precision} if precision is not None else {}
    server, _ = make(tmp_path, factory=PreciseManager, BLUELINK_ALLOW_LOCATION="true", **extra)
    data, _ = call(server, "get_vehicle_location")
    assert (data["latitude"], data["longitude"]) == expected
    assert f"mlat={expected[0]}&mlon={expected[1]}" in data["map"]


@pytest.mark.parametrize("precision", ["-1", "16", "four"])
def test_location_precision_is_validated(precision):
    with pytest.raises(ConfigError, match="BLUELINK_LOCATION_PRECISION"):
        Config.from_env({**ENV, "BLUELINK_LOCATION_PRECISION": precision})


def test_lock_waits_for_confirmation(tmp_path):
    server, _ = make(tmp_path, BLUELINK_ALLOW_WRITES="true")
    data, _ = call(server, "lock_vehicle", {"vehicle": "santa"})
    assert data == {"vehicle": "Santa Fe", "action_id": "act-1", "status": "success"}


def test_start_climate_options(tmp_path):
    server, _ = make(tmp_path, BLUELINK_ALLOW_WRITES="true")
    call(
        server,
        "start_climate",
        {"temperature": 21.3, "heated_steering_wheel": True, "front_left_seat": "high_heat"},
    )
    _, _, options = next(c for c in FakeManager.instances[-1].calls if c[0] == "climate")
    assert options.set_temp == 21.5
    assert options.heating == 3
    assert options.steering_wheel == 1
    assert options.front_left_seat == 8
    assert options.front_right_seat is None

    with pytest.raises(ToolError, match="between 14 and 30"):
        call(server, "start_climate", {"temperature": 40})


def test_unknown_vehicle_message(tmp_path):
    server, _ = make(tmp_path)
    with pytest.raises(ToolError, match="No vehicle matches"):
        call(server, "get_vehicle_status", {"vehicle": "ioniq"})


# --- token cache and login -------------------------------------------------


def write_cache(config, **fields):
    data = {
        "username": "me@example.com",
        "password": "old-password",
        "pin": "0000",
        "access_token": "cached",
        "valid_until": "2030-01-01T00:00:00+00:00",
        **fields,
    }
    config.token_file.write_text(json.dumps(data))


def test_cached_token_gets_the_configured_password_and_pin(tmp_path):
    server, config = make(tmp_path)
    write_cache(config)
    call(server, "list_vehicles")
    token = FakeManager.instances[-1].kwargs["token"]
    assert (token.access_token, token.password, token.pin) == ("cached", "secret", "1234")
    # A cache from an older version is rewritten without the password and PIN.
    saved = json.loads(config.token_file.read_text())
    assert (saved["password"], saved["pin"]) == (None, None)
    assert saved["access_token"] == "cached"
    assert "old-password" not in config.token_file.read_text()


class RejectingManager(FakeManager):
    def check_and_refresh_token(self):
        self.calls.append(("login",))
        raise AuthenticationError("bad password, response body: s3cr3t-fragment")


def test_failed_login_discards_cache_and_backs_off(tmp_path):
    server, config = make(tmp_path, factory=RejectingManager)
    write_cache(config)
    with pytest.raises(ToolError, match="rejected the login") as err:
        call(server, "list_vehicles")
    assert "s3cr3t-fragment" not in str(err.value)
    assert "AuthenticationError" in str(err.value)
    assert not config.token_file.exists()

    # No new login attempt until the backoff is over.
    with pytest.raises(ToolError, match="waits before trying again"):
        call(server, "list_vehicles")
    assert len(FakeManager.instances) == 1


class OTPManager(FakeManager):
    """Like the library: with no token, every check logs in with the password, then asks for an OTP."""

    logins = 0

    def check_and_refresh_token(self):
        if self.token is None:
            OTPManager.logins += 1
            raise AuthenticationOTPRequired("OTP required to refresh token")
        return False


def test_otp_request_backs_off_instead_of_logging_in_on_every_call(tmp_path):
    OTPManager.logins = 0
    server, _ = make(tmp_path, factory=OTPManager)
    with pytest.raises(ToolError, match="one-time passcode"):
        call(server, "list_vehicles")
    for _ in range(4):
        with pytest.raises(ToolError, match="waits before trying again"):
            call(server, "list_vehicles")
    assert OTPManager.logins == 1


class OfflineManager(FakeManager):
    def check_and_refresh_token(self):
        self.calls.append(("login",))
        raise ConnectionError("connection refused by 10.0.0.1")


def test_network_errors_during_login_do_not_back_off(tmp_path):
    server, config = make(tmp_path, factory=OfflineManager)
    write_cache(config)
    for _ in range(2):
        with pytest.raises(ToolError, match="Could not reach Bluelink to log in") as err:
            call(server, "list_vehicles")
        assert "10.0.0.1" not in str(err.value)
    # Each call tried again, and the cached session was kept.
    assert len(FakeManager.instances) == 2
    assert config.token_file.exists()


def test_other_login_errors_do_not_back_off(tmp_path):
    def broken(**kwargs):
        raise APIError("Unknown brand, raw details")

    server, _ = make(tmp_path, factory=broken)
    for _ in range(2):
        with pytest.raises(ToolError, match="Could not log in.*APIError"):
            call(server, "list_vehicles")


def test_login_backoff_can_be_turned_off(tmp_path):
    server, _ = make(tmp_path, factory=RejectingManager, BLUELINK_LOGIN_RETRY_MINUTES="0")
    for _ in range(2):
        with pytest.raises(ToolError, match="rejected the login"):
            call(server, "list_vehicles")
    assert len(FakeManager.instances) == 2


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[1, 2]",
        '{"username": "me@example.com"}',
        '{"username": "me@example.com", "access_token": "a", "valid_until": "garbage"}',
    ],
)
def test_bad_token_cache_is_ignored(tmp_path, content):
    server, config = make(tmp_path)
    config.token_file.write_text(content)
    call(server, "list_vehicles")
    assert FakeManager.instances[-1].kwargs["token"] is None
    assert ("login",) in FakeManager.instances[-1].calls


def test_manager_construction_errors_are_wrapped(tmp_path):
    def broken(**kwargs):
        raise APIError("Unknown brand, raw details")

    server, _ = make(tmp_path, factory=broken)
    with pytest.raises(ToolError, match="Could not log in.*APIError"):
        call(server, "list_vehicles")


def test_token_cache_leaves_no_temporary_files(tmp_path):
    server, config = make(tmp_path)
    call(server, "list_vehicles")
    assert [p.name for p in tmp_path.iterdir()] == ["token.json"]
    assert stat.S_IMODE(os.stat(config.token_file).st_mode) == 0o600


def test_unsupported_region_and_brand_is_a_config_error():
    check_supported(Config.from_env(ENV))
    with pytest.raises(ConfigError, match="genesis is not supported in BLUELINK_REGION=europe-cci"):
        check_supported(
            Config.from_env({**ENV, "BLUELINK_REGION": "europe-cci", "BLUELINK_BRAND": "genesis"})
        )


# --- errors ----------------------------------------------------------------


class FailingManager(FakeManager):
    def update_vehicle_with_cached_state(self, vid):
        raise APIError("resMsg: internal detail 12345")

    def force_refresh_vehicle_state(self, vid):
        self.calls.append(("force", vid))
        raise APIError("car did not answer")


def test_library_errors_are_not_passed_through(tmp_path):
    server, _ = make(tmp_path, factory=FailingManager)
    with pytest.raises(ToolError) as err:
        call(server, "get_vehicle_status")
    assert str(err.value).endswith("Bluelink request failed: Bluelink returned an error. (APIError)")
    assert "12345" not in str(err.value)


def test_failed_force_refresh_is_rate_limited_too(tmp_path):
    server, _ = make(tmp_path, factory=FailingManager)
    with pytest.raises(ToolError, match="APIError"):
        call(server, "refresh_vehicle_status")
    with pytest.raises(ToolError, match="12V battery"):
        call(server, "refresh_vehicle_status")
    assert FakeManager.instances[-1].calls.count(("force", "v1")) == 1


# --- commands --------------------------------------------------------------


class SlowCarManager(FakeManager):
    """The car confirms the lock only once someone has read the status in the meantime."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.status_read = threading.Event()

    def update_vehicle_with_cached_state(self, vid):
        super().update_vehicle_with_cached_state(vid)
        if ("lock", vid) in self.calls:
            self.status_read.set()

    def check_action_status(self, vid, action_id, synchronous=False, timeout=120):
        assert not synchronous
        return ORDER_STATUS.SUCCESS if self.status_read.is_set() else ORDER_STATUS.PENDING


def test_other_tools_run_while_a_command_waits(tmp_path):
    server, _ = make(
        tmp_path,
        factory=SlowCarManager,
        client_kwargs={"command_wait_seconds": 10, "poll_seconds": 0.01},
        BLUELINK_ALLOW_WRITES="true",
    )
    results = {}

    async def go():
        async def lock():
            results["lock"] = (await server.call_tool("lock_vehicle", {})).structured_content

        async with anyio.create_task_group() as tg:
            tg.start_soon(lock)
            while not FakeManager.instances or ("lock", "v1") not in FakeManager.instances[-1].calls:
                await anyio.sleep(0.01)
            with anyio.fail_after(5):
                results["status"] = (await server.call_tool("get_vehicle_status", {})).structured_content

    anyio.run(go)
    assert results["status"]["summary"]["locked"] is True
    assert results["lock"]["status"] == "success"


def test_command_returns_pending_when_the_car_is_slow(tmp_path):
    server, _ = make(
        tmp_path,
        factory=SlowCarManager,
        client_kwargs={"command_wait_seconds": 0.05, "poll_seconds": 0.01},
        BLUELINK_ALLOW_WRITES="true",
    )
    data, _ = call(server, "lock_vehicle")
    assert data == {"vehicle": "Santa Fe", "action_id": "act-1", "status": "pending"}


def test_start_climate_in_usa_sends_fahrenheit(tmp_path):
    server, _ = make(
        tmp_path, BLUELINK_ALLOW_WRITES="true", BLUELINK_REGION="usa", BLUELINK_MIN_COMMAND_SECONDS="0"
    )
    sent = []
    for celsius in (21, 14, 30):
        data, _ = call(server, "start_climate", {"temperature": celsius})
        sent.append(data["temperature_sent"])
    temps = [c[2].set_temp for c in FakeManager.instances[-1].calls if c[0] == "climate"]
    assert temps == [70, 62, 82]
    assert sent == ["70 °F", "62 °F", "82 °F"]


def test_start_climate_in_canada_respects_older_cars(tmp_path):
    server, _ = make(tmp_path, BLUELINK_ALLOW_WRITES="true", BLUELINK_REGION="canada")
    call(server, "list_vehicles")
    FakeManager.instances[-1].vehicles["v1"].year = 2019
    with pytest.raises(ToolError, match="lowest temperature is 16"):
        call(server, "start_climate", {"temperature": 15})
    call(server, "start_climate", {"temperature": 16})
    _, _, options = next(c for c in FakeManager.instances[-1].calls if c[0] == "climate")
    assert options.set_temp == 16


# --- vehicles and formatting -----------------------------------------------


class TwoCarManager(FakeManager):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.vehicles["v2"] = Vehicle(id="v2", name="Ioniq 5", model="IONIQ 5", year=2023, VIN="KMHABC")


def test_blank_vehicle_means_the_only_car(tmp_path):
    server, _ = make(tmp_path)
    data, _ = call(server, "get_vehicle_status", {"vehicle": "   "})
    assert data["vehicle"]["id"] == "v1"


def test_blank_vehicle_is_ambiguous_with_several_cars(tmp_path):
    server, _ = make(tmp_path, factory=TwoCarManager)
    with pytest.raises(ToolError, match="say which one"):
        call(server, "get_vehicle_status", {"vehicle": " "})


def test_unlock_needs_an_exact_vehicle_with_several_cars(tmp_path):
    server, _ = make(
        tmp_path, factory=TwoCarManager, BLUELINK_ALLOW_WRITES="true", BLUELINK_ALLOW_UNLOCK="true"
    )
    # Part of a name is fine for locking...
    data, _ = call(server, "lock_vehicle", {"vehicle": "santa"})
    assert data["vehicle"] == "Santa Fe"
    # ...but not for unlocking.
    with pytest.raises(ToolError, match="exact name"):
        call(server, "unlock_vehicle", {"vehicle": "santa"})
    assert not any(c[0] == "unlock" for c in FakeManager.instances[-1].calls)
    data, _ = call(server, "unlock_vehicle", {"vehicle": "santa fe", "confirm": "Santa Fe"})
    assert (data["vehicle"], data["status"]) == ("Santa Fe", "success")
    data, _ = call(server, "unlock_vehicle", {"vehicle": "KMHABC", "confirm": "KMHABC"})
    assert data["vehicle"] == "Ioniq 5"


def test_fuel_range_has_its_unit(tmp_path):
    class PetrolManager(FakeManager):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.vehicles["v1"].fuel_driving_range = (420, "km")

    server, _ = make(tmp_path, factory=PetrolManager)
    data, _ = call(server, "get_vehicle_status")
    assert data["fuel"]["fuel_range"] == {"value": 420, "unit": "km"}


# --- HTTP settings ---------------------------------------------------------

TOKEN = "t" * 24


def http_config(**extra):
    return Config.from_env({**ENV, "MCP_TRANSPORT": "http", "MCP_AUTH_TOKEN": TOKEN, **extra})


async def inner_app(scope, receive, send):
    """Stands in for the MCP app: echoes the request body."""
    message = await receive()
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b"inner:" + message.get("body", b"")})


def request(app, method="POST", path="/mcp", headers=None, body=b"{}", chunks=None):
    """Send one request to an ASGI app; return (status, headers, body)."""
    headers = {"host": "127.0.0.1:8080", **(headers or {})}
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [(k.lower().encode(), v.encode("latin-1")) for k, v in headers.items() if v is not None],
    }
    pending = [{"type": "http.request", "body": c, "more_body": True} for c in (chunks or [])]
    pending.append({"type": "http.request", "body": body, "more_body": False})
    out = {"status": None, "headers": {}, "body": b""}

    async def receive():
        return pending.pop(0) if pending else {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.start":
            out["status"] = message["status"]
            out["headers"] = {k.decode(): v.decode() for k, v in message.get("headers", [])}
        else:
            out["body"] += message.get("body", b"")

    anyio.run(app, scope, receive, send)
    return out["status"], out["headers"], out["body"]


def guarded(**extra):
    return http_app(http_config(**extra), inner_app)


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "",
        "Bearer",
        "Bearer ",
        "Bearer wrong-token-0000000000",
        f"Basic {TOKEN}",
        TOKEN,
        f"Bearer {TOKEN}x",
    ],
)
def test_bearer_auth_rejects_missing_or_wrong_tokens(authorization):
    status, headers, body = request(guarded(), headers={"authorization": authorization})
    assert status == 401
    assert headers["www-authenticate"] == "Bearer"
    assert b"inner" not in body


@pytest.mark.parametrize("scheme", ["Bearer", "bearer", "BEARER", "bEaReR"])
def test_bearer_auth_accepts_the_token_with_any_case_of_scheme(scheme):
    status, _, body = request(guarded(), headers={"authorization": f"{scheme} {TOKEN}"})
    assert (status, body) == (200, b"inner:{}")


def test_health_needs_no_token_or_allowed_host():
    app = guarded(MCP_ALLOWED_HOSTS="bluelink.example.com")
    status, _, body = request(app, method="GET", path="/health", headers={"host": "evil.example"}, body=b"")
    assert (status, body) == (200, b"inner:")


AUTH = {"authorization": f"Bearer {TOKEN}"}


@pytest.mark.parametrize("method", ["GET", "DELETE", "PUT"])
def test_mcp_is_post_only(method):
    status, headers, _ = request(guarded(), method=method, headers=AUTH)
    assert status == 405
    assert headers["allow"] == "POST"


def test_mcp_needs_a_json_body_within_the_limit():
    assert request(guarded(), headers=AUTH, body=b"{not json")[0] == 400
    assert request(guarded(), headers=AUTH, body=b"x" * (MAX_BODY_BYTES + 1))[0] == 413
    big = b'{"a": "' + b"x" * (MAX_BODY_BYTES - 20) + b'"}'
    assert request(guarded(), headers=AUTH, chunks=[big[:100]], body=big[100:])[0] == 200


@pytest.mark.parametrize(
    ("host", "allowed"),
    [
        ("bluelink.example.com", True),
        ("bluelink.example.com:8443", True),  # an entry without a port allows any port
        ("BLUELINK.example.com", True),
        ("192.168.1.20:8080", True),
        ("192.168.1.20:9090", False),
        ("192.168.1.20", False),
        ("localhost:1234", True),  # localhost:*
        ("[::1]:8080", True),
        ("evil.example", False),
        ("bluelink.example.com.evil.example", False),
        ("", False),
        (None, False),
    ],
)
def test_host_allow_list(host, allowed):
    app = guarded(MCP_ALLOWED_HOSTS="bluelink.example.com, 192.168.1.20:8080, localhost:*, [::1]")
    status, _, _ = request(app, headers={**AUTH, "host": host})
    assert status == (200 if allowed else 421)


@pytest.mark.parametrize(
    ("origin", "allowed"),
    [
        ("https://bluelink.example.com", True),
        ("http://bluelink.example.com", True),
        ("https://bluelink.example.com:443", True),
        ("https://bluelink.example.com:8443", False),  # other ports must be listed
        ("http://192.168.1.20:8080", True),
        ("http://192.168.1.20", False),
        ("http://localhost:5173", True),  # localhost:*
        ("https://claude.ai", True),  # MCP_ALLOWED_ORIGINS
        ("https://claude.ai:444", False),
        ("http://claude.ai", False),
        ("http://127.0.0.1:6274", True),  # http://127.0.0.1:* in MCP_ALLOWED_ORIGINS
        ("https://evil.example", False),
        ("null", False),
        ("", False),
        ("file://", False),
        ("https://bluelink.example.com/path", False),
    ],
)
def test_origin_allow_list(origin, allowed):
    app = guarded(
        MCP_ALLOWED_HOSTS="bluelink.example.com, 192.168.1.20:8080, localhost:*",
        MCP_ALLOWED_ORIGINS="https://claude.ai, http://127.0.0.1:*",
    )
    status, _, _ = request(app, headers={**AUTH, "host": "bluelink.example.com", "origin": origin})
    assert status == (200 if allowed else 403)


def test_without_allowed_hosts_the_sdk_default_applies():
    assert transport_security(Config.from_env(ENV)) is None
    # With hosts set, our own check replaces the SDK's.
    assert not transport_security(http_config(MCP_ALLOWED_HOSTS="h")).enable_dns_rebinding_protection
    status, _, _ = request(guarded(), headers={**AUTH, "host": "anything", "origin": "https://evil.example"})
    assert status == 200
    with pytest.raises(ConfigError, match="MCP_ALLOWED_ORIGINS needs MCP_ALLOWED_HOSTS"):
        Config.from_env({**ENV, "MCP_ALLOWED_ORIGINS": "https://claude.ai"})


def test_auth_token_minimum_length():
    with pytest.raises(ConfigError, match="at least 16 characters"):
        http_config(MCP_AUTH_TOKEN="short-token")
    assert http_config(MCP_AUTH_TOKEN="x" * 16).auth_token == "x" * 16


def test_secrets_can_come_from_files(tmp_path):
    (tmp_path / "password").write_text("pass word \n")
    (tmp_path / "pin").write_text("4321\n")
    (tmp_path / "token").write_text(TOKEN + "\n")
    config = Config.from_env(
        {
            "BLUELINK_USERNAME": "me@example.com",
            "BLUELINK_PASSWORD_FILE": str(tmp_path / "password"),
            "BLUELINK_PIN_FILE": str(tmp_path / "pin"),
            "MCP_AUTH_TOKEN_FILE": str(tmp_path / "token"),
        }
    )
    assert (config.password, config.pin, config.auth_token) == ("pass word ", "4321", TOKEN)
    with pytest.raises(ConfigError, match="not both"):
        Config.from_env({**ENV, "BLUELINK_PASSWORD_FILE": str(tmp_path / "password")})
    with pytest.raises(ConfigError, match="Could not read BLUELINK_PIN_FILE"):
        Config.from_env({**ENV, "BLUELINK_PIN": "", "BLUELINK_PIN_FILE": str(tmp_path / "missing")})


@pytest.mark.parametrize(
    ("env", "url"),
    [
        ({}, None),
        ({"MCP_TRANSPORT": "stdio"}, None),
        ({"MCP_TRANSPORT": "http"}, "http://127.0.0.1:8080/health"),
        (
            {"MCP_TRANSPORT": "http", "MCP_HTTP_HOST": "0.0.0.0", "MCP_HTTP_PORT": "9000"},
            "http://127.0.0.1:9000/health",
        ),
        ({"MCP_TRANSPORT": "http", "MCP_HTTP_HOST": "::"}, "http://[::1]:8080/health"),
        ({"MCP_TRANSPORT": "http", "MCP_HTTP_HOST": "10.0.0.5"}, "http://10.0.0.5:8080/health"),
        ({"MCP_TRANSPORT": "http", "MCP_HTTP_HOST": "fd00::5"}, "http://[fd00::5]:8080/health"),
    ],
)
def test_healthcheck_url(env, url):
    assert healthcheck_url(env) == url


@pytest.mark.parametrize(
    ("raw", "level", "library"),
    [
        (None, logging.INFO, logging.CRITICAL + 1),
        ("warning", logging.WARNING, logging.CRITICAL + 1),
        ("ERROR", logging.ERROR, logging.CRITICAL + 1),
        ("debug", logging.DEBUG, logging.DEBUG),
    ],
)
def test_log_level(raw, level, library):
    assert log_level({} if raw is None else {"LOG_LEVEL": raw}) == (level, library)


def test_log_level_is_validated():
    with pytest.raises(ConfigError, match="LOG_LEVEL must be one of"):
        log_level({"LOG_LEVEL": "verbose"})
