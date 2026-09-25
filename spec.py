#!/usr/bin/env python3
"""The production spec, read from spec.json and never from memory.

Every number the pipeline delivers against lives in `spec.json`. This module
is the only way to reach it, so that "where does 14 Mbps come from" has one
answer. Anything that wants a limit asks here; nothing re-states one.

`get` takes a dotted path so a caller names what it wants in the spec's own
words -- `spec.get("video.bitrate_kbps")` -- and raises rather than returning
a default, because a silent default is how a pipeline drifts away from the
document it is supposed to be delivering against.
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = HERE / "spec.json"

_CACHE = {}


class SpecError(Exception):
    pass


def load(path=None):
    path = Path(path or SPEC)
    key = str(path)
    if key not in _CACHE:
        if not path.exists():
            raise SpecError("%s is missing. It is the source of truth for every "
                            "production value; the pipeline will not guess." % path)
        try:
            _CACHE[key] = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SpecError("%s is not valid JSON: %s" % (path, exc)) from exc
    return _CACHE[key]


def get(dotted, path=None):
    """One value, named the way spec.json names it. Missing is an error."""
    node = load(path)
    for part in str(dotted).split("."):
        if not isinstance(node, dict) or part not in node:
            raise SpecError("spec.json has no %r (looking for %r)" % (part, dotted))
        node = node[part]
    return node


def band(dotted, path=None):
    """A [low, high] pair, checked to actually be one."""
    got = get(dotted, path)
    if not (isinstance(got, list) and len(got) == 2):
        raise SpecError("%s should be a [low, high] pair, got %r" % (dotted, got))
    low, high = got
    if low > high:
        raise SpecError("%s is back to front: %r" % (dotted, got))
    return float(low), float(high)


def within(dotted, value, path=None):
    low, high = band(dotted, path)
    return low <= float(value) <= high


def pct_of(dotted, whole, path=None):
    """A spec value given in pixels, as the share of a dimension the style stores."""
    return 100.0 * float(get(dotted, path)) / float(whole)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(json.dumps(load(), indent=2))
        return 0
    try:
        for dotted in argv:
            print("%s = %s" % (dotted, json.dumps(get(dotted))))
    except SpecError as exc:
        print("spec.py: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
