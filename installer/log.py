"""Run-scoped debug log — REQ-5, REQ-19."""

import datetime
import sys
from pathlib import Path

_log_path: Path | None = None


def init(path: Path) -> None:
    """Open (or append to) the log file and write a run-start header."""
    global _log_path
    _log_path = path
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(f"\n## Run {_now()}\n\n")


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _write(level: str, msg: str) -> None:
    if _log_path is None:
        return
    with _log_path.open("a") as f:
        f.write(f"{_now()} {level:<5} {msg}\n")


def info(msg: str) -> None:
    print(msg)
    sys.stdout.flush()
    _write("INFO", msg)


def ok(msg: str) -> None:
    print(msg)
    sys.stdout.flush()
    _write("OK", msg)


def warning(msg: str) -> None:
    print(msg)
    sys.stdout.flush()
    _write("WARN", msg)


def error(msg: str) -> None:
    print(msg)
    sys.stdout.flush()
    _write("ERROR", msg)


def secret(label: str, value: str) -> None:
    """Write a sensitive value to the log file only — never to the console (REQ-9)."""
    _write("SECRET", f"{label}: {value}")
