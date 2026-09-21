#!/usr/bin/env python3
"""The production-partner ledger for the Etsy shop.

Etsy allows you to work with a manufacturer — a **production partner** — for
items you designed, provided you disclose them. It prohibits dropshipping and
reselling: sourcing ready-made goods and listing them as your own gets shops
suspended. The difference is whether *you* designed the thing.

So this ledger exists to keep the legitimate model straight. Every partner
records who they are, where they are, and exactly what role they play, because
those three are what Etsy requires you to disclose. It also tracks whether you
have actually held a sample, since listing a product you have never seen is how
a shop earns its first one-star review.

    python3 suppliers.py add --name "..." --location "..." --role "..." \
        --makes "..." --moq 25 --lead-days 14
    python3 suppliers.py sample <partner-id> --state received --notes "..."
    python3 suppliers.py show
    python3 suppliers.py disclosure <partner-id>
    python3 suppliers.py check <partner-id>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

__all__ = ["load", "add", "get", "set_sample", "disclosure", "ready",
           "SupplierError", "SAMPLE_STATES"]

HERE = Path(__file__).resolve().parent
STORE = HERE / "suppliers.json"
SAMPLE_STATES = ("none", "ordered", "received", "rejected")

# Sourcing sites are where ready-made goods come from. A "partner" that is one
# of these is a reseller relationship, not a production partner.
RESELL_HINTS = ("aliexpress", "alibaba", "temu", "dhgate", "amazon.",
                "wish.com", "1688.com", "shein")


class SupplierError(RuntimeError):
    """A partner record could not be read or written."""


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _slug(name):
    out = re.sub(r"[^a-z0-9]+", "-", str(name).strip().lower()).strip("-")
    return out or "partner"


def load(path=None):
    path = Path(path) if path else STORE
    if not path.exists():
        return {"partners": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SupplierError("%s is not valid JSON (%s)" % (path, exc)) from exc
    if not isinstance(data, dict) or not isinstance(data.get("partners"), list):
        raise SupplierError('%s must contain {"partners": [...]}' % path)
    return data


def _save(data, path=None):
    path = Path(path) if path else STORE
    tmp = path.with_name(path.name + ".tmp.%d" % os.getpid())
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def add(name, location, role, makes="", contact="", moq=None, lead_days=None,
        unit_cost=None, notes="", path=None):
    """Record a production partner. The three disclosure fields are mandatory."""
    missing = [label for label, value in
               (("--name", name), ("--location", location), ("--role", role))
               if not (value or "").strip()]
    if missing:
        raise SupplierError(
            "%s required. Etsy makes you disclose a production partner's name, "
            "location and the role they play — a partner you cannot describe in "
            "those terms is not one you can legally use."
            % ", ".join(missing))

    blob = " ".join(str(v).lower() for v in (name, contact, notes, makes))
    hit = next((h for h in RESELL_HINTS if h in blob), None)
    if hit:
        raise SupplierError(
            '"%s" looks like a %s sourcing listing rather than a production '
            "partner. Buying ready-made goods there and listing them is "
            "dropshipping, which Etsy prohibits and suspends shops for. A "
            "production partner manufactures something *you* designed. If this "
            "really is a manufacturer who will build to your spec, record their "
            "company name rather than the marketplace you found them on."
            % (name, hit))

    data = load(path)
    partner_id = _slug(name)
    if any(p.get("id") == partner_id for p in data["partners"]):
        raise SupplierError('a partner with id "%s" already exists' % partner_id)

    record = {
        "id": partner_id,
        "name": name.strip(),
        "location": location.strip(),
        "role": role.strip(),
        "makes": (makes or "").strip(),
        "contact": (contact or "").strip(),
        "moq": moq, "leadDays": lead_days, "unitCost": unit_cost,
        "notes": (notes or "").strip(),
        "sample": {"state": "none", "at": None, "notes": ""},
        "etsyPartnerId": None,     # the numeric id Etsy gives you in Shop Manager
        "addedAt": _now(),
    }
    data["partners"].append(record)
    _save(data, path)
    return record


def get(partner_id, path=None):
    for partner in load(path)["partners"]:
        if partner.get("id") == partner_id:
            return partner
    known = [p.get("id") for p in load(path)["partners"]]
    raise SupplierError('no partner "%s". Known: %s'
                        % (partner_id, ", ".join(known) or "(none)"))


def set_sample(partner_id, state, notes="", path=None):
    if state not in SAMPLE_STATES:
        raise SupplierError("sample state must be one of %s" % ", ".join(SAMPLE_STATES))
    data = load(path)
    for partner in data["partners"]:
        if partner.get("id") == partner_id:
            partner["sample"] = {"state": state, "at": _now(), "notes": (notes or "").strip()}
            _save(data, path)
            return partner
    raise SupplierError('no partner "%s"' % partner_id)


def set_etsy_id(partner_id, etsy_id, path=None):
    data = load(path)
    for partner in data["partners"]:
        if partner.get("id") == partner_id:
            partner["etsyPartnerId"] = int(etsy_id)
            _save(data, path)
            return partner
    raise SupplierError('no partner "%s"' % partner_id)


def disclosure(partner_id, path=None):
    """The sentence that has to appear in the listing."""
    partner = get(partner_id, path)
    return ("Made with the help of a production partner: %s (%s) — %s."
            % (partner["name"], partner["location"], partner["role"]))


def ready(partner_id, path=None):
    """Is this partner safe to list against? Returns (ok, [reasons])."""
    partner = get(partner_id, path)
    blockers = []
    if partner["sample"]["state"] != "received":
        blockers.append(
            "no sample received (state: %s). Listing a product you have never "
            "held is how a shop earns its first one-star review."
            % partner["sample"]["state"])
    if not partner.get("etsyPartnerId"):
        blockers.append(
            "no Etsy production partner id. Add the partner in Etsy Shop Manager "
            "→ Settings → Production partners, then record the numeric id with: "
            "suppliers.py etsy-id %s <id>" % partner_id)
    return (not blockers), blockers


def main(argv=None):
    parser = argparse.ArgumentParser(prog="suppliers.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="record a production partner")
    p_add.add_argument("--name", required=True)
    p_add.add_argument("--location", required=True, help="city and country, as Etsy requires")
    p_add.add_argument("--role", required=True, help="exactly what they do in your process")
    p_add.add_argument("--makes", default="")
    p_add.add_argument("--contact", default="")
    p_add.add_argument("--moq", type=int, default=None)
    p_add.add_argument("--lead-days", type=int, default=None)
    p_add.add_argument("--unit-cost", type=float, default=None)
    p_add.add_argument("--notes", default="")

    p_s = sub.add_parser("sample", help="update sample status")
    p_s.add_argument("partner_id")
    p_s.add_argument("--state", required=True, choices=list(SAMPLE_STATES))
    p_s.add_argument("--notes", default="")

    p_e = sub.add_parser("etsy-id", help="record Etsy's numeric production partner id")
    p_e.add_argument("partner_id")
    p_e.add_argument("etsy_id", type=int)

    sub.add_parser("show", help="list every partner")
    p_d = sub.add_parser("disclosure", help="the sentence for the listing")
    p_d.add_argument("partner_id")
    p_c = sub.add_parser("check", help="is this partner ready to list against?")
    p_c.add_argument("partner_id")

    args = parser.parse_args(argv)
    try:
        if args.command == "add":
            got = add(args.name, args.location, args.role, args.makes, args.contact,
                      args.moq, args.lead_days, args.unit_cost, args.notes)
            print('added "%s" (id: %s)' % (got["name"], got["id"]))
            print("next: order a sample, then  python3 suppliers.py sample %s --state ordered"
                  % got["id"])
        elif args.command == "sample":
            got = set_sample(args.partner_id, args.state, args.notes)
            print("%s sample: %s" % (got["id"], got["sample"]["state"]))
        elif args.command == "etsy-id":
            got = set_etsy_id(args.partner_id, args.etsy_id)
            print("%s -> Etsy partner id %d" % (got["id"], got["etsyPartnerId"]))
        elif args.command == "show":
            partners = load()["partners"]
            if not partners:
                print("no partners recorded")
                return 0
            for partner in partners:
                ok, blockers = ready(partner["id"])
                print("%-22s %-22s sample=%-9s %s" % (
                    partner["id"], partner["location"], partner["sample"]["state"],
                    "READY" if ok else "not ready"))
                for blocker in blockers:
                    print("    - %s" % blocker)
        elif args.command == "disclosure":
            print(disclosure(args.partner_id))
        else:
            ok, blockers = ready(args.partner_id)
            if ok:
                print("READY — %s" % disclosure(args.partner_id))
                return 0
            print("NOT READY:")
            for blocker in blockers:
                print("  - %s" % blocker)
            return 2
        return 0
    except SupplierError as exc:
        print("suppliers.py: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
