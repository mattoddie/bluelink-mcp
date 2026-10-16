"""MCP server for Hyundai Bluelink and Kia Connect."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("bluelink-mcp")
except PackageNotFoundError:  # running from a source tree without installing
    __version__ = "0.0.0"
