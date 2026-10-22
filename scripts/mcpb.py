"""Build the MCP Bundle (.mcpb) that installs bluelink-mcp into Claude Desktop.

uv run python scripts/mcpb.py manifest          # refresh the tool list in mcpb/manifest.json
uv run python scripts/mcpb.py manifest --check  # fail if it is out of date (CI)
uv run python scripts/mcpb.py pack 1.2.3        # write dist/bluelink-mcp-1.2.3.mcpb

The bundle uses the MCPB "uv" server type: it holds the source, pyproject.toml and uv.lock,
and the host (Claude Desktop) installs Python and the locked dependencies with uv. So one
bundle serves every platform.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path
from typing import Any

import anyio

from bluelink_mcp.config import Config
from bluelink_mcp.server import build_server

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "mcpb" / "manifest.json"
STAGE = ROOT / ".build" / "mcpb"
DIST = ROOT / "dist"

# Files copied into the bundle, besides the source package and the files in mcpb/.
PROJECT_FILES = ("pyproject.toml", "uv.lock", "README.md", "LICENSE")
BUNDLE_FILES = ("icon.png", "server.py")

EVERY_TOOL = {
    "BLUELINK_USERNAME": "docs@example.com",
    "BLUELINK_PASSWORD": "docs",
    "BLUELINK_TOKEN_FILE": "",
    "BLUELINK_ALLOW_WRITES": "true",
    "BLUELINK_ALLOW_UNLOCK": "true",
    "BLUELINK_ALLOW_LOCATION": "true",
}


def summary(text: str | None) -> str:
    """The first paragraph of a tool's description, on one line."""
    first = (text or "").strip().split("\n\n")[0]
    return " ".join(line.strip() for line in first.splitlines())


def tools() -> list[dict[str, str]]:
    server = build_server(Config.from_env(EVERY_TOOL))
    listed = anyio.run(server.list_tools)
    return [
        {"name": t.name, "description": summary(t.description)} for t in sorted(listed, key=lambda t: t.name)
    ]


def render(manifest: dict[str, Any]) -> str:
    return json.dumps({**manifest, "tools": tools()}, indent=2, ensure_ascii=False) + "\n"


def manifest_command(check: bool) -> None:
    current = MANIFEST.read_text()
    text = render(json.loads(current))
    if check:
        if text != current:
            sys.exit("mcpb/manifest.json is out of date. Run: uv run python scripts/mcpb.py manifest")
        print("mcpb/manifest.json is up to date")
        return
    MANIFEST.write_text(text)
    print("wrote mcpb/manifest.json")


def uv(*args: str, cwd: Path) -> str:
    return subprocess.run(["uv", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def set_lock_version(lock: Path, version: str) -> None:
    """Give the project's own entry in uv.lock the bundle's version, so the lock stays current."""
    text = lock.read_text()
    new, count = re.subn(
        r'(\[\[package\]\]\nname = "bluelink-mcp"\nversion = )"[^"]*"',
        lambda m: f'{m.group(1)}"{version}"',
        text,
    )
    if count != 1:
        sys.exit("mcpb: could not find bluelink-mcp in uv.lock")
    lock.write_text(new)


def pack(version: str) -> Path:
    version = version.removeprefix("v")
    shutil.rmtree(STAGE, ignore_errors=True)
    STAGE.mkdir(parents=True)

    manifest = json.loads(MANIFEST.read_text())
    if render(manifest) != MANIFEST.read_text():
        sys.exit("mcpb/manifest.json is out of date. Run: uv run python scripts/mcpb.py manifest")
    manifest["version"] = version
    (STAGE / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    for name in BUNDLE_FILES:
        shutil.copy2(MANIFEST.parent / name, STAGE / name)
    for name in PROJECT_FILES:
        shutil.copy2(ROOT / name, STAGE / name)
    shutil.copytree(
        ROOT / "src" / "bluelink_mcp",
        STAGE / "src" / "bluelink_mcp",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )

    # The installed package reports this version (bluelink-mcp's startup log and serverInfo).
    uv("version", "--frozen", version, cwd=STAGE)
    normalised = tomllib.loads((STAGE / "pyproject.toml").read_text())["project"]["version"]
    set_lock_version(STAGE / "uv.lock", normalised)

    DIST.mkdir(exist_ok=True)
    out = DIST / f"bluelink-mcp-{version}.mcpb"
    out.unlink(missing_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(p for p in STAGE.rglob("*") if p.is_file()):
            zf.write(path, path.relative_to(STAGE).as_posix())
    print(f"wrote {out.relative_to(ROOT)}")
    return out


def main() -> None:
    args = sys.argv[1:]
    if args[:1] == ["manifest"] and args[1:] in ([], ["--check"]):
        manifest_command(check=args[1:] == ["--check"])
    elif args[:1] == ["pack"] and len(args) == 2:
        pack(args[1])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
