#!/usr/bin/env python3
"""Create Etsy listings through the Open API v3.

OAuth 2.0 with PKCE against your own shop. Listings are created as **drafts**:
that is what the API does, and it is also what you want — a human looks at the
photos and the price before anything goes live.

Before your first run
---------------------
1. https://www.etsy.com/developers/register — create an app, get the keystring.
2. Personal App approval first; **Commercial Access** is a separate, manually
   reviewed request. Until it is granted your app is limited.
3. ``export ETSY_API_KEY=<keystring>`` then ``python3 etsy.py login``.

What this refuses to do
-----------------------
Etsy prohibits dropshipping and reselling: sourcing ready-made goods and
listing them as your own gets shops suspended. It does allow **production
partners** — a manufacturer making something *you designed* — provided you
disclose their name, location and role.

So a listing declaring ``who_made="someone_else"`` for a non-supply,
non-vintage item is refused here, because that is reselling. Work through
``suppliers.py`` instead: design the item, record the partner, disclose them.

    python3 etsy.py login
    python3 etsy.py whoami
    python3 etsy.py draft --title "..." --description-file d.txt --price 24.00 \
        --quantity 10 --taxonomy 1234 --who-made i_did --when-made made_to_order \
        --partner linen-works --dry-run
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.client
import json
import os
import secrets
import socket
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

__all__ = ["draft_listing", "build_listing", "load_credentials", "EtsyError",
           "ResellRefused", "SCOPES"]

SCOPES = "listings_r listings_w shops_r"          # no billing, no deletes
AUTH_HOST, AUTH_PATH = "www.etsy.com", "/oauth/connect"
TOKEN_HOST, TOKEN_PATH = "api.etsy.com", "/v3/public/oauth/token"
API_HOST, API_BASE = "openapi.etsy.com", "/v3/application"

HERE = Path(__file__).resolve().parent
TOKEN_FILE = HERE / "etsy_token.json"

WHO_MADE = ("i_did", "someone_else", "collective")
VINTAGE_YEARS = 20               # Etsy's threshold for "vintage"
MAX_TITLE = 140
MAX_TAGS = 13
MAX_TAG_LEN = 20


class EtsyError(RuntimeError):
    """A listing could not be created."""


class ResellRefused(EtsyError):
    """The listing describes reselling, which Etsy prohibits."""


def _https(host, method, path, body=None, headers=None, timeout=60):
    conn = http.client.HTTPSConnection(host, timeout=timeout)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        return resp.status, dict(resp.getheaders()), resp.read()
    finally:
        conn.close()


def _json_or_raise(status, payload, what):
    try:
        data = json.loads(payload.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        raise EtsyError("%s: unreadable response (HTTP %d)" % (what, status))
    if status >= 400:
        msg = data.get("error") or data.get("error_description") or str(data)[:200]
        if status == 429:
            raise EtsyError("%s: rate limited by Etsy. The API allows a limited "
                            "number of calls per day and per second — back off and "
                            "retry later." % what)
        raise EtsyError("%s failed (HTTP %d): %s" % (what, status, msg))
    return data


def _api_key():
    key = os.environ.get("ETSY_API_KEY")
    if not key:
        raise EtsyError("ETSY_API_KEY is not set. Register an app at "
                        "https://www.etsy.com/developers/register and export its keystring.")
    return key


def _save_token(data, path=None):
    path = Path(path) if path else TOKEN_FILE
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


class _Callback(BaseHTTPRequestHandler):
    result = None

    def do_GET(self):                                     # noqa: N802
        _Callback.result = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        ok = "code" in _Callback.result
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"<body style='font:14px system-ui;padding:40px'>" +
                         (b"<h3>Connected.</h3><p>Back to the terminal.</p>" if ok
                          else b"<h3>Authorisation failed.</h3>") + b"</body>")

    def log_message(self, *args):
        pass


def login(token_path=None, open_browser=True):
    """Etsy's OAuth requires PKCE; this runs it once and stores a refresh token."""
    key = _api_key()
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(24)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    redirect = "http://127.0.0.1:%d/" % port

    url = "https://%s%s?%s" % (AUTH_HOST, AUTH_PATH, urllib.parse.urlencode({
        "response_type": "code", "client_id": key, "redirect_uri": redirect,
        "scope": SCOPES, "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
    }))
    print("Open this to connect your shop:\n\n%s\n" % url)
    if open_browser:
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass

    server = HTTPServer(("127.0.0.1", port), _Callback)
    server.timeout = 300
    _Callback.result = None
    server.handle_request()
    server.server_close()
    got = _Callback.result
    if not got:
        raise EtsyError("timed out waiting for the browser")
    if got.get("state", [None])[0] != state:
        raise EtsyError("state mismatch on the OAuth callback — aborting")
    if "code" not in got:
        raise EtsyError("authorisation denied: %s" % got.get("error", ["unknown"])[0])

    body = json.dumps({
        "grant_type": "authorization_code", "client_id": key,
        "redirect_uri": redirect, "code": got["code"][0], "code_verifier": verifier,
    }).encode()
    status, _, payload = _https(TOKEN_HOST, "POST", TOKEN_PATH, body,
                                {"Content-Type": "application/json"})
    token = _json_or_raise(status, payload, "token exchange")
    token["obtained_at"] = int(time.time())
    _save_token(token, token_path)
    print("Connected. Refresh token stored in %s (mode 600) — do not commit it."
          % (token_path or TOKEN_FILE))
    return token


def load_credentials(token_path=None):
    path = Path(token_path) if token_path else TOKEN_FILE
    if not path.exists():
        raise EtsyError("not connected yet — run: python3 etsy.py login")
    try:
        token = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise EtsyError("%s is not valid JSON (%s); run login again" % (path, exc)) from exc
    age = time.time() - token.get("obtained_at", 0)
    if token.get("access_token") and age < token.get("expires_in", 3600) - 120:
        return token["access_token"]
    body = json.dumps({"grant_type": "refresh_token", "client_id": _api_key(),
                       "refresh_token": token["refresh_token"]}).encode()
    status, _, payload = _https(TOKEN_HOST, "POST", TOKEN_PATH, body,
                                {"Content-Type": "application/json"})
    fresh = _json_or_raise(status, payload, "token refresh")
    token.update(fresh)
    token["obtained_at"] = int(time.time())
    _save_token(token, path)
    return token["access_token"]


def _call(method, path, body=None, token=None, transport=None, token_path=None):
    send = transport or _https
    access = token or load_credentials(token_path)
    headers = {"x-api-key": _api_key(), "Authorization": "Bearer " + access}
    payload = None
    if body is not None:
        payload = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(payload))
    status, _, raw = send(API_HOST, method, API_BASE + path, payload, headers)
    return _json_or_raise(status, raw, "%s %s" % (method, path))


# --------------------------------------------------------------------------
# the policy gate
# --------------------------------------------------------------------------
def _is_vintage(when_made, today=None):
    """Etsy counts an item as vintage at 20+ years old.

    Derived from the years in the value rather than matched against a list of
    enum strings, so it stays right as the calendar moves and does not depend on
    guessing Etsy's exact vocabulary. "before_2007" means made earlier than
    2007, so the newest it can be is 2006.
    """
    import datetime
    import re as _re
    years = [int(y) for y in _re.findall(r"(1[89]\d{2}|20\d{2})", str(when_made))]
    if not years:
        return False                                   # made_to_order and friends
    newest = max(years)
    if str(when_made).strip().lower().startswith("before_"):
        newest -= 1
    cutoff = (today or datetime.date.today()).year - VINTAGE_YEARS
    return newest <= cutoff


def _check_not_reselling(who_made, when_made, is_supply, partner):
    """Refuse the listing shapes Etsy suspends shops for.

    Handmade items must be made or designed by you. If somebody else made a
    ready-made item and you are simply listing it, that is reselling. The legal
    route is a production partner building *your* design, disclosed.
    """
    if who_made not in WHO_MADE:
        raise EtsyError("who_made must be one of %s" % ", ".join(WHO_MADE))
    # the more specific complaint first: it names the actual mistake
    if partner and who_made == "someone_else":
        raise ResellRefused(
            "a production partner means you designed the item, so who_made should "
            'be "i_did" (or "collective"). Declaring someone_else alongside a '
            "partner describes reselling their product, which Etsy prohibits."
        )
    vintage = _is_vintage(when_made)
    if who_made == "someone_else" and not is_supply and not vintage:
        raise ResellRefused(
            'who_made="someone_else" on a non-supply, non-vintage item describes '
            "reselling a ready-made product, which Etsy prohibits and suspends "
            "shops for. Two legitimate shapes: you designed it and a disclosed "
            "production partner makes it (who_made=\"i_did\" plus --partner), or "
            "it genuinely is a craft supply or 20+ year old vintage piece — say "
            "so with --is-supply or a vintage --when-made."
        )
    return True


def build_listing(title, description, price, quantity, taxonomy_id,
                  who_made="i_did", when_made="made_to_order", is_supply=False,
                  tags=None, materials=None, shipping_profile_id=None,
                  partner=None, partner_disclosure=None, partner_etsy_id=None):
    """Validate the listing and build the createDraftListing body."""
    title = (title or "").strip()
    if not title:
        raise EtsyError("a title is required")
    if len(title) > MAX_TITLE:
        raise EtsyError("title is %d characters; Etsy allows %d" % (len(title), MAX_TITLE))
    description = (description or "").strip()
    if not description:
        raise EtsyError("a description is required")
    try:
        price = round(float(price), 2)
    except (TypeError, ValueError):
        raise EtsyError("price must be a number")
    if price <= 0:
        raise EtsyError("price must be above 0")
    if not isinstance(quantity, int) or quantity < 1:
        raise EtsyError("quantity must be a positive integer")
    if not taxonomy_id:
        raise EtsyError("a taxonomy_id is required — Etsy's category for the item")

    tags = [t.strip() for t in (tags or []) if t and t.strip()]
    if len(tags) > MAX_TAGS:
        raise EtsyError("Etsy allows %d tags; %d given" % (MAX_TAGS, len(tags)))
    too_long = [t for t in tags if len(t) > MAX_TAG_LEN]
    if too_long:
        raise EtsyError("tags must be %d characters or fewer: %s"
                        % (MAX_TAG_LEN, ", ".join(too_long)))

    _check_not_reselling(who_made, when_made, is_supply, partner)

    if partner:
        if not partner_disclosure:
            raise EtsyError(
                "a production partner must be disclosed in the listing. Get the "
                "sentence from: python3 suppliers.py disclosure %s" % partner)
        if partner_disclosure.strip() not in description:
            description = description.rstrip() + "\n\n" + partner_disclosure.strip()

    body = {
        "quantity": quantity,
        "title": title,
        "description": description,
        "price": price,
        "who_made": who_made,
        "when_made": when_made,
        "taxonomy_id": int(taxonomy_id),
        "is_supply": bool(is_supply),
        "state": "draft",
    }
    if tags:
        body["tags"] = tags
    if materials:
        body["materials"] = [m.strip() for m in materials if m and m.strip()]
    if shipping_profile_id:
        body["shipping_profile_id"] = int(shipping_profile_id)
    if partner_etsy_id:
        body["production_partner_ids"] = [int(partner_etsy_id)]
    return body


def whoami(token=None, transport=None, token_path=None):
    return _call("GET", "/users/me", None, token, transport, token_path)


def draft_listing(shop_id, dry_run=False, token=None, transport=None,
                  agent=None, token_path=None, **fields):
    """Create one draft listing. Returns the created resource."""
    body = build_listing(**fields)
    if dry_run:
        return {"dryRun": True, "shop_id": shop_id, "request": body}
    reporter = _Reporter(agent, body["title"])
    reporter.start()
    try:
        got = _call("POST", "/shops/%s/listings" % shop_id, body,
                    token, transport, token_path)
    except EtsyError as exc:
        reporter.fail(str(exc))
        raise
    state = got.get("state", "?")
    reporter.finish("draft %s (%s)" % (got.get("listing_id", "?"), state))
    return got


class _Reporter:
    def __init__(self, agent_id, label):
        self.agent, self.label, self.mod = agent_id, label, None
        if not agent_id:
            return
        try:
            sys.path.insert(0, str(HERE))
            import status
            self.mod = status
        except Exception:
            print("etsy.py: status.py not usable — continuing without dashboard reporting",
                  file=sys.stderr)

    def _safe(self, fn):
        if not self.mod:
            return
        try:
            fn()
        except Exception as exc:
            print("etsy.py: dashboard update failed: %s" % exc, file=sys.stderr)

    def start(self):
        self._safe(lambda: self.mod.start(self.agent, "Drafting listing: %s" % self.label[:60]))

    def finish(self, msg):
        self._safe(lambda: self.mod.finish(self.agent, msg))

    def fail(self, msg):
        self._safe(lambda: self.mod.fail(self.agent, msg[:300]))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="etsy.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_login = sub.add_parser("login", help="connect your shop once")
    p_login.add_argument("--no-browser", action="store_true")
    sub.add_parser("whoami", help="show the connected user and shop")

    p_d = sub.add_parser("draft", help="create a draft listing")
    p_d.add_argument("--shop-id")
    p_d.add_argument("--title", required=True)
    p_d.add_argument("--description", default="")
    p_d.add_argument("--description-file")
    p_d.add_argument("--price", required=True)
    p_d.add_argument("--quantity", type=int, default=1)
    p_d.add_argument("--taxonomy", required=True, help="Etsy taxonomy (category) id")
    p_d.add_argument("--who-made", default="i_did", choices=list(WHO_MADE))
    p_d.add_argument("--when-made", default="made_to_order")
    p_d.add_argument("--is-supply", action="store_true")
    p_d.add_argument("--tags", nargs="*", default=[])
    p_d.add_argument("--materials", nargs="*", default=[])
    p_d.add_argument("--shipping-profile", type=int, default=None)
    p_d.add_argument("--partner", help="production partner id from suppliers.py")
    p_d.add_argument("--dry-run", action="store_true")
    p_d.add_argument("--agent", help="report to this dashboard agent id")

    args = parser.parse_args(argv)
    try:
        if args.command == "login":
            login(open_browser=not args.no_browser)
            return 0
        if args.command == "whoami":
            print(json.dumps(whoami(), indent=2))
            return 0

        description = args.description
        if args.description_file:
            description = Path(args.description_file).read_text(encoding="utf-8")

        disclosure = partner_etsy_id = None
        if args.partner:
            sys.path.insert(0, str(HERE))
            import suppliers
            ok, blockers = suppliers.ready(args.partner)
            if not ok:
                print("etsy.py: partner %s is not ready to list against:" % args.partner,
                      file=sys.stderr)
                for blocker in blockers:
                    print("  - %s" % blocker, file=sys.stderr)
                return 1
            disclosure = suppliers.disclosure(args.partner)
            partner_etsy_id = suppliers.get(args.partner)["etsyPartnerId"]

        result = draft_listing(
            args.shop_id, args.dry_run, agent=args.agent,
            title=args.title, description=description, price=args.price,
            quantity=args.quantity, taxonomy_id=args.taxonomy,
            who_made=args.who_made, when_made=args.when_made, is_supply=args.is_supply,
            tags=args.tags, materials=args.materials,
            shipping_profile_id=args.shipping_profile,
            partner=args.partner, partner_disclosure=disclosure,
            partner_etsy_id=partner_etsy_id)

        if args.dry_run:
            print("DRY RUN — nothing was sent. Etsy would receive:")
            print(json.dumps(result["request"], indent=2))
            return 0
        print("Draft created: listing %s (%s)" % (result.get("listing_id"), result.get("state")))
        print("It is a DRAFT. Review the photos and price in Shop Manager, then publish.")
        return 0
    except ResellRefused as exc:
        print("etsy.py: %s" % exc, file=sys.stderr)
        return 2
    except EtsyError as exc:
        print("etsy.py: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
