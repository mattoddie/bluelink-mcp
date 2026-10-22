"""Check and stamp server.json, which lists bluelink-mcp in the MCP Registry.

uv run python scripts/registry.py check                  # fail if server.json drifts from the bundle and config (CI)
python3 scripts/registry.py stamp 1.2.3 <bundle-sha256>  # write a release's version, image tag and bundle hash

server.json in the repository carries the 0.0.0-dev placeholder, like mcpb/manifest.json. The
release workflow stamps the real version and publishes it. `stamp` needs only the standard
library, so it runs without installing the project.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SERVER_JSON = ROOT / "server.json"
MANIFEST = ROOT / "mcpb" / "manifest.json"
DOCKERFILE = ROOT / "Dockerfile"

NAME = "io.github.mattoddie/bluelink-mcp"
IMAGE = "ghcr.io/mattoddie/bluelink-mcp"
RELEASES = "https://github.com/mattoddie/bluelink-mcp/releases/download"
# The variable whose value server.json and the bundle fix rather than ask the user for.
FIXED = {"MCP_TRANSPORT": "stdio"}


def load() -> dict[str, Any]:
    return json.loads(SERVER_JSON.read_text())


def package(server: dict[str, Any], registry_type: str) -> dict[str, Any]:
    found = [p for p in server["packages"] if p["registryType"] == registry_type]
    if len(found) != 1:
        sys.exit(f"server.json: expected one {registry_type} package, found {len(found)}")
    return found[0]


def stamp(version: str, sha256: str) -> None:
    version = version.removeprefix("v")
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        sys.exit(f"stamp: {sha256!r} is not a SHA-256 hex digest")
    server = load()
    server["version"] = version
    package(server, "oci")["identifier"] = f"{IMAGE}:{version}"
    mcpb = package(server, "mcpb")
    mcpb["identifier"] = f"{RELEASES}/v{version}/bluelink-mcp-{version}.mcpb"
    mcpb["fileSha256"] = sha256
    SERVER_JSON.write_text(json.dumps(server, indent=2, ensure_ascii=False) + "\n")
    print(f"stamped server.json with {version}")


def expected_default(option: dict[str, Any]) -> str | None:
    """A bundle setting's default as server.json writes it: a string, with no empty defaults."""
    default = option.get("default")
    if isinstance(default, bool):
        return str(default).lower()
    return default or None


def check() -> None:
    from bluelink_mcp.config import BRANDS, REGIONS

    errors: list[str] = []
    server = load()
    manifest = json.loads(MANIFEST.read_text())
    version = server["version"]

    if server["name"] != NAME:
        errors.append(f"name must be {NAME}")
    if len(server["description"]) > 100:
        errors.append("description must be 100 characters or fewer")
    if f'io.modelcontextprotocol.server.name="{NAME}"' not in DOCKERFILE.read_text():
        errors.append("the Dockerfile must label the image io.modelcontextprotocol.server.name")

    oci = package(server, "oci")
    if oci["identifier"] != f"{IMAGE}:{version}":
        errors.append(f"the oci identifier must be {IMAGE}:{version}")
    mcpb = package(server, "mcpb")
    if mcpb["identifier"] != f"{RELEASES}/v{version}/bluelink-mcp-{version}.mcpb":
        errors.append("the mcpb identifier must point at this version's release asset")

    # The image's variables must be the bundle's, with the same required, secret and default
    # settings, so the registry entry and the Claude Desktop form ask for the same things.
    env = {v["name"]: v for v in oci.get("environmentVariables", [])}
    bundle_env = manifest["server"]["mcp_config"]["env"]
    if set(env) != set(bundle_env):
        errors.append(
            "environment variables differ from mcpb/manifest.json: "
            f"only in server.json {sorted(set(env) - set(bundle_env))}, "
            f"only in the manifest {sorted(set(bundle_env) - set(env))}"
        )
    for name, value in bundle_env.items():
        if name not in env:
            continue
        var = env[name]
        if name in FIXED:
            if value != FIXED[name] or var.get("value") != FIXED[name]:
                errors.append(f"{name} must be fixed to {FIXED[name]!r} in both files")
            continue
        key = re.fullmatch(r"\$\{user_config\.(\w+)\}", value)
        option = manifest["user_config"].get(key.group(1)) if key else None
        if option is None:
            errors.append(f"{name}: the manifest doesn't map it to a user_config setting")
            continue
        for field, want in (
            ("isRequired", bool(option.get("required"))),
            ("isSecret", bool(option.get("sensitive"))),
            ("default", expected_default(option)),
        ):
            have = var.get(field, None if field == "default" else False)
            if have != want:
                errors.append(f"{name}: {field} is {have!r}, but the manifest says {want!r}")
        if (option["type"] == "boolean") != (var.get("format") == "boolean"):
            errors.append(f"{name}: format must be boolean exactly when the manifest setting is")

    for name, allowed in (("BLUELINK_REGION", REGIONS), ("BLUELINK_BRAND", BRANDS)):
        if name in env and sorted(env[name].get("choices", [])) != sorted(allowed):
            errors.append(f"{name}: choices must be {sorted(allowed)}")

    config = (ROOT / "src" / "bluelink_mcp" / "config.py").read_text()
    errors += [f"{name} isn't read by config.py" for name in env if f'"{name}"' not in config]

    if errors:
        sys.exit("server.json is out of step:\n- " + "\n- ".join(errors))
    print("server.json is consistent with mcpb/manifest.json, the config and the Dockerfile")


def main() -> None:
    args = sys.argv[1:]
    if args == ["check"]:
        check()
    elif args[:1] == ["stamp"] and len(args) == 3:
        stamp(args[1], args[2])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
