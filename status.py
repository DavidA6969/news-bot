#!/usr/bin/env python3
"""Writer helper for the agent operations dashboard.

Agents call this to update their own record in ``agents.json``. Every write
takes an exclusive file lock and lands atomically, so two agents finishing at
the same moment cannot interleave and corrupt the file.

From Python::

    import status
    status.start("trend-research", "Scanning last 7 days in niche")
    status.finish("trend-research", "3 topics selected, 2 rejected")

From a shell (this is how a Claude Code subagent calls it)::

    python3 status.py start  trend-research "Scanning last 7 days in niche"
    python3 status.py finish trend-research "3 topics selected, 2 rejected"
    python3 status.py fail   trend-research "RSS feed returned 503"
    python3 status.py log    trend-research "Rate limited, backing off" --level warn

The state file defaults to ``agents.json`` next to this script, so it resolves
the same way no matter which directory the caller is in. Override with the
``AGENT_STATE_FILE`` environment variable or ``--file``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

__all__ = [
    "start", "finish", "fail", "log",
    "block", "idle", "register", "snapshot",
    "StatusError", "UnknownAgent",
    "STATUSES", "LEVELS", "MAX_EVENTS",
]

STATUSES = ("idle", "working", "blocked", "done", "error")
LEVELS = ("info", "success", "warn", "error")
MAX_EVENTS = 500
LOCK_TIMEOUT = 10.0          # seconds to wait for a contended lock
LOCK_POLL = 0.02

try:                                            # POSIX
    import fcntl
except ImportError:                             # pragma: no cover
    fcntl = None
try:                                            # Windows
    import msvcrt
except ImportError:
    msvcrt = None

_DEFAULT_STATE = Path(__file__).resolve().parent / "agents.json"
_STATE_FILE = Path(os.environ.get("AGENT_STATE_FILE") or _DEFAULT_STATE)


class StatusError(RuntimeError):
    """Anything that stops a status update from being written."""


class UnknownAgent(StatusError):
    """The agent id is not present in agents.json."""


def set_state_file(path) -> Path:
    """Point the helper at a different agents.json."""
    global _STATE_FILE
    _STATE_FILE = Path(path)
    return _STATE_FILE


def state_file() -> Path:
    return _STATE_FILE


def _lock_file() -> Path:
    return _STATE_FILE.with_name(_STATE_FILE.name + ".lock")


# --------------------------------------------------------------------------
# time
# --------------------------------------------------------------------------
def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# locking
# --------------------------------------------------------------------------
def _try_lock(handle) -> bool:
    if fcntl is not None:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False
    if msvcrt is not None:                                  # pragma: no cover
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    return True                                             # see _fallback_lock


def _unlock(handle) -> None:
    if fcntl is not None:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
    elif msvcrt is not None:                                # pragma: no cover
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass


@contextmanager
def _fallback_lock(deadline):                               # pragma: no cover
    """Directory-based mutex for platforms with neither fcntl nor msvcrt."""
    path = Path(str(_lock_file()) + ".d")
    while True:
        try:
            path.mkdir()
            break
        except FileExistsError:
            try:                                            # break a stale lock
                if time.time() - path.stat().st_mtime > 60:
                    path.rmdir()
                    continue
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise StatusError("timed out waiting for %s" % path)
            time.sleep(LOCK_POLL)
    try:
        yield
    finally:
        try:
            path.rmdir()
        except OSError:
            pass


@contextmanager
def _locked(timeout: float = LOCK_TIMEOUT):
    """Hold an exclusive lock for the whole read-modify-write cycle."""
    deadline = time.monotonic() + timeout
    if fcntl is None and msvcrt is None:                     # pragma: no cover
        with _fallback_lock(deadline):
            yield
        return

    lock_path = _lock_file()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+")
    try:
        while not _try_lock(handle):
            if time.monotonic() >= deadline:
                raise StatusError(
                    "timed out after %gs waiting for the lock on %s — "
                    "another agent may be stuck mid-write" % (timeout, lock_path)
                )
            time.sleep(LOCK_POLL)
        try:
            yield
        finally:
            _unlock(handle)
    finally:
        handle.close()


# --------------------------------------------------------------------------
# read / write
# --------------------------------------------------------------------------
def _read() -> dict:
    path = _STATE_FILE
    if not path.exists():
        raise StatusError(
            "%s does not exist. Create it (see README.md) before agents report status." % path
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StatusError(
            "%s is not valid JSON (%s). Refusing to overwrite it — fix or restore "
            "the file first." % (path, exc)
        ) from exc
    except OSError as exc:
        raise StatusError("could not read %s: %s" % (path, exc)) from exc

    if not isinstance(raw, dict):
        raise StatusError("%s must contain a JSON object at the top level." % path)
    if not isinstance(raw.get("agents"), list):
        raise StatusError('%s has no "agents" array.' % path)
    events = raw.get("events")
    if events is None:
        raw["events"] = []
    elif not isinstance(events, list):
        raise StatusError('%s has an "events" key that is not an array.' % path)
    return raw


def _write(state: dict) -> None:
    """Serialise first, then swap the file in one atomic rename.

    A reader polling every 2s must never see a half-written file.
    """
    state["updated"] = _iso(_now())
    payload = json.dumps(state, indent=2, ensure_ascii=False) + "\n"
    path = _STATE_FILE
    tmp = path.with_name("%s.tmp.%d" % (path.name, os.getpid()))
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise StatusError("could not write %s: %s" % (path, exc)) from exc
    try:
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except (OSError, AttributeError):
        pass                                    # not supported everywhere; harmless


def _find(state: dict, agent_id: str) -> dict:
    for agent in state["agents"]:
        if isinstance(agent, dict) and agent.get("id") == agent_id:
            return agent
    known = [a.get("id") for a in state["agents"] if isinstance(a, dict) and a.get("id")]
    raise UnknownAgent(
        'no agent with id "%s" in %s. Known ids: %s'
        % (agent_id, _STATE_FILE, ", ".join(known) or "(none)")
    )


def _append_event(state: dict, agent_id: str, level: str, message: str, when: datetime) -> None:
    if level not in LEVELS:
        raise StatusError("level must be one of %s (got %r)" % (", ".join(LEVELS), level))
    state["events"].append(
        {"ts": _iso(when), "agent": agent_id, "level": level, "message": str(message)}
    )
    overflow = len(state["events"]) - MAX_EVENTS
    if overflow > 0:
        del state["events"][:overflow]          # oldest out first


def _num(value, default=0):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else default


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------
def start(agent_id: str, task: str = "") -> dict:
    """Mark an agent as working on ``task`` and stamp startedAt."""
    with _locked():
        state = _read()
        agent = _find(state, agent_id)
        now = _now()
        agent["status"] = "working"
        agent["currentTask"] = str(task) if task else None
        agent["startedAt"] = _iso(now)
        _append_event(state, agent_id, "info",
                      "Started — %s" % task if task else "Run started", now)
        _write(state)
        return dict(agent)


def finish(agent_id: str, output: str = "") -> dict:
    """Mark a run complete: updates lastRun/lastOutput, runCount and the rolling average."""
    with _locked():
        state = _read()
        agent = _find(state, agent_id)
        now = _now()
        started = _parse(agent.get("startedAt"))
        duration = (now - started).total_seconds() if started else None

        runs = int(_num(agent.get("runCount"), 0))
        if duration is not None:
            previous = agent.get("avgDurationSec")
            previous = float(previous) if isinstance(previous, (int, float)) and not isinstance(previous, bool) else None
            if previous is None or runs <= 0:
                average = duration
            else:
                average = (previous * runs + duration) / (runs + 1)
            agent["avgDurationSec"] = round(average, 1)

        agent["runCount"] = runs + 1
        agent["status"] = "done"
        agent["lastRun"] = _iso(now)
        agent["lastOutput"] = str(output) if output else None
        agent["currentTask"] = None
        agent["startedAt"] = None

        message = "Finished — %s" % output if output else "Run finished"
        if duration is not None:
            message += " (%ds)" % round(duration)
        _append_event(state, agent_id, "success", message, now)
        _write(state)
        return dict(agent)


def fail(agent_id: str, error: str = "") -> dict:
    """Mark a run failed: sets status error, increments failCount, records the reason."""
    with _locked():
        state = _read()
        agent = _find(state, agent_id)
        now = _now()
        agent["status"] = "error"
        agent["failCount"] = int(_num(agent.get("failCount"), 0)) + 1
        agent["lastRun"] = _iso(now)
        agent["lastOutput"] = str(error) if error else None
        agent["currentTask"] = None
        agent["startedAt"] = None
        _append_event(state, agent_id, "error",
                      "Failed — %s" % error if error else "Run failed", now)
        _write(state)
        return dict(agent)


def log(agent_id: str, message: str, level: str = "info") -> dict:
    """Append an event without touching the agent's status."""
    with _locked():
        state = _read()
        _find(state, agent_id)                  # validate the id before writing
        now = _now()
        _append_event(state, agent_id, level, message, now)
        _write(state)
        return {"ts": _iso(now), "agent": agent_id, "level": level, "message": str(message)}


# --- extensions beyond the four core calls -------------------------------
def block(agent_id: str, reason: str = "") -> dict:
    """Mark an agent as blocked — waiting on input, a quota, or a human."""
    with _locked():
        state = _read()
        agent = _find(state, agent_id)
        now = _now()
        agent["status"] = "blocked"
        if reason:
            agent["currentTask"] = str(reason)
        _append_event(state, agent_id, "warn",
                      "Blocked — %s" % reason if reason else "Blocked", now)
        _write(state)
        return dict(agent)


def idle(agent_id: str, note: str = "") -> dict:
    """Return an agent to idle, clearing a previous done/error state."""
    with _locked():
        state = _read()
        agent = _find(state, agent_id)
        now = _now()
        agent["status"] = "idle"
        agent["currentTask"] = None
        agent["startedAt"] = None
        _append_event(state, agent_id, "info", note or "Returned to idle", now)
        _write(state)
        return dict(agent)


def register(agent_id: str, name: str = "", role: str = "", depends_on=None) -> dict:
    """Add an agent record if it is missing. Existing records are left alone."""
    with _locked():
        state = _read()
        for agent in state["agents"]:
            if isinstance(agent, dict) and agent.get("id") == agent_id:
                return dict(agent)
        agent = {
            "id": agent_id,
            "name": name or agent_id,
            "role": role or "",
            "status": "idle",
            "currentTask": None,
            "startedAt": None,
            "lastRun": None,
            "lastOutput": None,
            "runCount": 0,
            "failCount": 0,
            "avgDurationSec": None,
            "dependsOn": list(depends_on or []),
        }
        state["agents"].append(agent)
        _append_event(state, agent_id, "info", "Registered", _now())
        _write(state)
        return dict(agent)


def snapshot(agent_id: str = None):
    """Read the current state without modifying it."""
    with _locked():
        state = _read()
        if agent_id is None:
            return state
        return dict(_find(state, agent_id))


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="status.py",
        description="Update an agent's record in agents.json.",
    )
    parser.add_argument("--file", help="path to agents.json (default: next to this script)")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name, help_text):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("agent_id")
        return p

    add("start", "mark the agent as working").add_argument("task", nargs="?", default="")
    add("finish", "mark the run complete").add_argument("output", nargs="?", default="")
    add("fail", "mark the run failed").add_argument("error", nargs="?", default="")

    p_log = add("log", "append an event only")
    p_log.add_argument("message")
    p_log.add_argument("--level", choices=list(LEVELS), default="info")

    add("block", "mark the agent as blocked").add_argument("reason", nargs="?", default="")
    add("idle", "return the agent to idle").add_argument("note", nargs="?", default="")

    p_reg = add("register", "add a new agent record")
    p_reg.add_argument("--name", default="")
    p_reg.add_argument("--role", default="")
    p_reg.add_argument("--depends-on", nargs="*", default=[])

    p_show = sub.add_parser("show", help="print current state")
    p_show.add_argument("agent_id", nargs="?")
    return parser


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    if args.file:
        set_state_file(args.file)

    try:
        if args.command == "start":
            agent = start(args.agent_id, args.task)
        elif args.command == "finish":
            agent = finish(args.agent_id, args.output)
        elif args.command == "fail":
            agent = fail(args.agent_id, args.error)
        elif args.command == "block":
            agent = block(args.agent_id, args.reason)
        elif args.command == "idle":
            agent = idle(args.agent_id, args.note)
        elif args.command == "register":
            agent = register(args.agent_id, args.name, args.role, args.depends_on)
        elif args.command == "log":
            event = log(args.agent_id, args.message, args.level)
            print("%s [%s] %s" % (event["agent"], event["level"], event["message"]))
            return 0
        elif args.command == "show":
            print(json.dumps(snapshot(args.agent_id), indent=2))
            return 0
        else:                                   # pragma: no cover
            raise StatusError("unknown command %r" % args.command)
    except StatusError as exc:
        print("status.py: %s" % exc, file=sys.stderr)
        return 1

    print("%s -> %s" % (agent["id"], agent["status"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
