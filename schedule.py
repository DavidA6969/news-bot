#!/usr/bin/env python3
"""Decide when finished videos go live.

This spaces releases so a channel publishes on a steady, human-legible rhythm
and stays well inside YouTube's API limits. It is **not** an attempt to look
un-automated: uploads go through the YouTube Data API with your own OAuth
token, so YouTube knows perfectly well they are API uploads, and that is
allowed. Nothing here tries to hide that, and you should not add anything that
does — evading platform detection is against YouTube's Terms of Service, and
cadence is not what puts a channel at risk anyway. Content is.

Typical use::

    python3 schedule.py plan --days 7            # show the next week's slots
    python3 schedule.py next                     # the next publish time, ISO-8601

Configuration lives in ``schedule.json`` next to this file; ``--write-config``
creates one with the defaults below.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:                                       # pragma: no cover
    ZoneInfo = None

__all__ = ["load_config", "plan", "next_slot", "ScheduleError", "DEFAULT_CONFIG"]

DEFAULT_CONFIG = {
    "timezone": "UTC",
    # Two a day most days, one on the quieter days. Weights pick between them.
    "slots_per_day": {"1": 2, "2": 5},
    # Local-time windows a release may land in, earliest first.
    "windows": [["09:00", "11:30"], ["17:00", "19:30"]],
    # Never publish two videos closer together than this.
    "min_gap_hours": 5,
    # Days to skip entirely (0 = Monday, matching datetime.weekday()).
    "skip_weekdays": [],
    # Upper bound, so a config typo can never schedule a flood.
    "max_per_day": 3,
}

CONFIG_PATH = Path(__file__).resolve().parent / "schedule.json"


class ScheduleError(RuntimeError):
    """The schedule configuration cannot be used."""


# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------
def _parse_hhmm(value, label):
    try:
        hh, mm = str(value).split(":")
        return time(int(hh), int(mm))
    except (ValueError, AttributeError):
        raise ScheduleError('%s must look like "09:00" (got %r)' % (label, value))


def load_config(path=None):
    """Read schedule.json, falling back to the defaults, and validate it."""
    cfg = dict(DEFAULT_CONFIG)
    path = Path(path) if path else CONFIG_PATH
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ScheduleError("%s is not valid JSON (%s)" % (path, exc)) from exc
        if not isinstance(loaded, dict):
            raise ScheduleError("%s must contain a JSON object" % path)
        cfg.update(loaded)

    windows = cfg.get("windows")
    if not isinstance(windows, list) or not windows:
        raise ScheduleError('"windows" must be a non-empty list of ["HH:MM", "HH:MM"] pairs')
    parsed = []
    for i, w in enumerate(windows):
        if not isinstance(w, (list, tuple)) or len(w) != 2:
            raise ScheduleError('windows[%d] must be a ["HH:MM", "HH:MM"] pair' % i)
        start = _parse_hhmm(w[0], "windows[%d][0]" % i)
        end = _parse_hhmm(w[1], "windows[%d][1]" % i)
        if end <= start:
            raise ScheduleError("windows[%d] ends at or before it starts (%s..%s)" % (i, w[0], w[1]))
        parsed.append((start, end))
    cfg["_windows"] = parsed

    weights = cfg.get("slots_per_day")
    if not isinstance(weights, dict) or not weights:
        raise ScheduleError('"slots_per_day" must map a count to its weight, e.g. {"1": 2, "2": 5}')
    counts = {}
    for key, weight in weights.items():
        try:
            n, w = int(key), float(weight)
        except (TypeError, ValueError):
            raise ScheduleError('"slots_per_day" keys and weights must be numbers (got %r: %r)' % (key, weight))
        if n < 0:
            raise ScheduleError('"slots_per_day" counts cannot be negative')
        if w < 0:
            raise ScheduleError('"slots_per_day" weights cannot be negative')
        counts[n] = w
    if not any(counts.values()):
        raise ScheduleError('"slots_per_day" needs at least one non-zero weight')
    cap = int(cfg.get("max_per_day", 3))
    over = [n for n in counts if n > cap]
    if over:
        raise ScheduleError(
            "slots_per_day asks for %d a day but max_per_day is %d — raise the cap "
            "deliberately if you really mean it" % (max(over), cap)
        )
    if max(counts) > len(parsed):
        raise ScheduleError(
            "slots_per_day can reach %d but only %d window(s) are defined — "
            "add a window per daily slot" % (max(counts), len(parsed))
        )
    cfg["_counts"] = counts

    if ZoneInfo is None:                                   # pragma: no cover
        cfg["_tz"] = timezone.utc
    else:
        try:
            cfg["_tz"] = ZoneInfo(str(cfg.get("timezone", "UTC")))
        except Exception as exc:
            raise ScheduleError('unknown timezone %r (%s)' % (cfg.get("timezone"), exc)) from exc

    skip = cfg.get("skip_weekdays") or []
    if not isinstance(skip, list) or any(not isinstance(d, int) or not 0 <= d <= 6 for d in skip):
        raise ScheduleError('"skip_weekdays" must be a list of 0-6 integers (0 = Monday)')
    cfg["_skip"] = set(skip)

    gap = cfg.get("min_gap_hours", 0)
    if not isinstance(gap, (int, float)) or gap < 0:
        raise ScheduleError('"min_gap_hours" must be a non-negative number')
    return cfg


# --------------------------------------------------------------------------
# planning
# --------------------------------------------------------------------------
def _pick_count(counts, rng):
    options = sorted(counts)
    weights = [counts[n] for n in options]
    return rng.choices(options, weights=weights, k=1)[0]


def plan(days=7, start=None, seed=None, config=None, now=None):
    """Return the publish slots for the next ``days`` days, as aware UTC datetimes.

    Deterministic for a given ``seed``, so a plan can be reproduced and tested.
    Slots already in the past relative to ``now`` are dropped.
    """
    cfg = config if config and "_windows" in config else load_config()
    if days < 1:
        return []
    rng = random.Random(seed)
    tz = cfg["_tz"]
    gap = timedelta(hours=float(cfg.get("min_gap_hours", 0)))

    now = now or datetime.now(timezone.utc)
    first = (start or now.astimezone(tz)).date()

    slots = []
    for offset in range(days):
        day = first + timedelta(days=offset)
        if day.weekday() in cfg["_skip"]:
            continue
        count = _pick_count(cfg["_counts"], rng)
        for window_index in range(count):
            w_start, w_end = cfg["_windows"][window_index]
            span = (w_end.hour * 60 + w_end.minute) - (w_start.hour * 60 + w_start.minute)
            minute = w_start.hour * 60 + w_start.minute + rng.randrange(span)
            local = datetime.combine(day, time(minute // 60, minute % 60), tzinfo=tz)
            when = local.astimezone(timezone.utc)
            if when <= now:
                continue
            if slots and when - slots[-1] < gap:
                pushed = slots[-1] + gap                   # space it out rather than bunch up
                window_end = datetime.combine(day, w_end, tzinfo=tz).astimezone(timezone.utc)
                if pushed > window_end:
                    continue                               # would leave its window: publish once today
                when = pushed
            slots.append(when)
    return slots


def next_slot(seed=None, config=None, now=None):
    """The next publish time, or None if nothing is scheduled in the next 14 days."""
    upcoming = plan(days=14, seed=seed, config=config, now=now)
    return upcoming[0] if upcoming else None


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def main(argv=None):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help="path to schedule.json")
    common.add_argument("--seed", type=int, help="fix the randomness so a plan is reproducible")

    parser = argparse.ArgumentParser(prog="schedule.py", parents=[common],
                                     description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_plan = sub.add_parser("plan", parents=[common], help="print the upcoming publish slots")
    p_plan.add_argument("--days", type=int, default=7)

    sub.add_parser("next", parents=[common], help="print the next publish time only")
    sub.add_parser("write-config", parents=[common], help="write schedule.json with the defaults")

    args = parser.parse_args(argv)
    try:
        if args.command == "write-config":
            target = Path(args.config) if args.config else CONFIG_PATH
            if target.exists():
                print("schedule.py: %s already exists — not overwriting" % target, file=sys.stderr)
                return 1
            target.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n", encoding="utf-8")
            print("wrote %s" % target)
            return 0

        cfg = load_config(args.config)
        if args.command == "next":
            when = next_slot(seed=args.seed, config=cfg)
            if when is None:
                print("schedule.py: nothing scheduled in the next 14 days", file=sys.stderr)
                return 1
            print(_iso(when))
            return 0

        slots = plan(days=args.days, seed=args.seed, config=cfg)
        if not slots:
            print("schedule.py: no slots in the next %d days" % args.days, file=sys.stderr)
            return 1
        tz = cfg["_tz"]
        day = None
        for when in slots:
            local = when.astimezone(tz)
            label = local.strftime("%a %Y-%m-%d")
            if label != day:
                day = label
                print(label)
            print("  %s local   %s" % (local.strftime("%H:%M"), _iso(when)))
        per_day = {}
        for when in slots:
            per_day.setdefault(when.astimezone(tz).date(), 0)
            per_day[when.astimezone(tz).date()] += 1
        counts = sorted(per_day.values())
        print("\n%d slots over %d days (%d-%d a day)" %
              (len(slots), len(per_day), counts[0], counts[-1]))
        return 0
    except ScheduleError as exc:
        print("schedule.py: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
