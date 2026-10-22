"""The audit log (BLUELINK_AUDIT_LOG): one JSON line per call of a tool that acts on the car."""

from __future__ import annotations

import datetime as dt
import functools
import inspect
import json
import logging
import sys
import threading
from collections.abc import Awaitable, Callable
from typing import Any, TextIO

from .config import AUDIT_LOG_STDERR, open_audit_log

log = logging.getLogger(__name__)

REDACTED = "[redacted]"
# Arguments never written to the log. `confirm` is the car's name or the end of its VIN.
SECRET_ARGUMENTS = {"confirm", "pin", "password", "token"}
# Result fields worth keeping. Nothing else from the result is logged, so no status,
# and no location, ever reaches the file.
RESULT_FIELDS = ("vehicle", "action_id", "status")
VIN_LENGTH = 17
VIN_SUFFIX = 6


def redact_vehicle(value: Any) -> Any:
    """A VIN given as the vehicle argument, shortened to its last 6 characters."""
    if isinstance(value, str) and len(value.strip()) == VIN_LENGTH and value.strip().isalnum():
        return "…" + value.strip()[-VIN_SUFFIX:]
    return value


def redact(arguments: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, value in arguments.items():
        if name in SECRET_ARGUMENTS:
            out[name] = REDACTED if value not in (None, "") else value
        elif name == "vehicle":
            out[name] = redact_vehicle(value)
        else:
            out[name] = value
    return out


class AuditLog:
    """Appends entries to a file (opened for each entry, so it can be rotated) or to stderr."""

    def __init__(self, target: str, stderr: TextIO | None = None):
        self.target = target
        self.stderr = stderr
        self._lock = threading.Lock()
        self._warned = False

    def record(
        self,
        tool: str,
        arguments: dict[str, Any],
        result: Any = None,
        error: BaseException | None = None,
    ) -> None:
        entry: dict[str, Any] = {
            "time": dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "tool": tool,
            "arguments": redact(arguments),
        }
        if isinstance(result, dict):
            kept = {k: result[k] for k in RESULT_FIELDS if k in result}
            # A status result carries the whole vehicle summary (VIN included): keep the name.
            if isinstance(kept.get("vehicle"), dict):
                kept["vehicle"] = kept["vehicle"].get("name")
            entry["result"] = kept
        if error is None:
            entry["outcome"] = "ok"
        else:
            entry["outcome"] = "error"
            entry["error"] = str(error) or type(error).__name__
        self._write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    def _write(self, line: str) -> None:
        with self._lock:
            try:
                if self.target == AUDIT_LOG_STDERR:
                    stream = self.stderr or sys.stderr
                    stream.write(line)
                    stream.flush()
                else:
                    with open_audit_log(self.target) as f:
                        f.write(line)
            except OSError as ex:
                # A failed write doesn't fail the tool call: the command may already be on its way.
                if not self._warned:
                    self._warned = True
                    log.warning(
                        "Could not write the audit log (%s): %s. Tool calls carry on; "
                        "this warning is not repeated.",
                        self.target,
                        ex.strerror or type(ex).__name__,
                    )


def audited(audit: AuditLog | None, skip: tuple[str, ...] = ()):
    """Wrap a tool function so each call is recorded under its name, whether it succeeds or fails.

    The wrapper keeps the function's signature, so the MCP server sees the same arguments.
    Parameters in `skip` (filled in by the server, not the model) are left out of the entry.
    """

    def wrap(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        if audit is None:
            return fn
        tool = fn.__name__
        signature = inspect.signature(fn)

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            bound = signature.bind_partial(*args, **kwargs)
            bound.apply_defaults()
            arguments = {k: v for k, v in bound.arguments.items() if k not in skip}
            try:
                result = await fn(*args, **kwargs)
            except BaseException as ex:
                audit.record(tool, arguments, error=ex)
                raise
            audit.record(tool, arguments, result=result)
            return result

        return wrapper

    return wrap
