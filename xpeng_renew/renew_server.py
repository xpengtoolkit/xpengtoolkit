#!/usr/bin/env python3
"""Renew XPENG credentials unattended.

Triggered by a Home Assistant automation (see docs/UNATTENDED_RENEWAL.md). One
call does what a person would: clear the login slider with the solver, mint a fresh
token + tenant pair, then hand it to the integration's ``xpeng.import_credentials``
service, which validates it before swapping anything.

The login code is the toolkit's own, vendored into vendor/ by
tools/sync_renew_vendor.py - the same code, so a renewal cannot take a different
path from a manual onboarding.

   POST /renew    run a renewal (bearer renew_token if set)
   GET  /healthz  counters and the last outcome
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
VENDOR = os.path.join(ROOT, "vendor")
TOOL = os.path.join(VENDOR, "tools", "onboard_email_login.py")
PAIR_SOURCE = os.path.join(VENDOR, "custom_components", "xpeng")
DEVICE_ID_FILE = "/data/device_id.txt"
NOTIFY_AFTER = 2

#: A pair that has already been minted is delivered on its own, without logging in
#: again: the login is what evicts the integration's MQTT session, so re-logging in
#: to fix an import failure only lengthens the outage (measured 2026-10-17: a
#: scheduled renewal logged in, the import failed with 502 while Home Assistant was
#: restarting, and the install stayed dark until the next attempt).
RETRY_IMPORT_SECONDS = 60
#: A failure *before* a pair exists (solver, captcha, login, mint) has evicted
#: nothing, so it can wait - and should, because every attempt costs a slider solve.
RETRY_LOGIN_SECONDS = 300


def _options() -> dict:
    path = os.environ.get("OPTIONS_PATH", "/data/options.json")
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:  # noqa: BLE001 - absent outside the Supervisor
        return {}


OPTIONS = _options()


def _setting(name: str, default: str = "") -> str:
    return str(os.environ.get(name.upper()) or OPTIONS.get(name) or default).strip()


def _setting_int(name: str, default: int) -> int:
    try:
        return int(float(_setting(name, str(default)) or default))
    except (TypeError, ValueError):
        return default


def _setting_bool(name: str, default: bool) -> bool:
    """A boolean option or environment variable.

    The Supervisor passes a real boolean, while a standalone ``docker run -e``
    passes a string, so both are accepted. ``false``, ``0`` and an empty string are
    off; anything else non-empty is on, which matches the Supervisor's parsing.
    """
    raw = os.environ.get(name.upper())
    if raw is None:
        raw = OPTIONS.get(name)
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() not in ("", "0", "false", "off", "no")


#: The port the solver's own API listens on. Home Assistant serves the solver's
#: sidebar panel on its own 8123, which is the address people copy.
_SOLVER_API_PORT = 8080


def _solver_url(raw: str) -> tuple[str, str]:
    """The solver's API address, corrected when the sidebar panel URL was given.

    `solver_url` is the one setting that depends on how the solver was installed,
    and the panel URL is its most natural wrong answer: it is what you copy out of
    the Home Assistant UI when you want "the address of the solver". It resolves,
    so it does not read as a typo - but that route is a GET-only ingress panel, so
    the tool's `POST <url>/solve` comes back

        405: Method Not Allowed

    which looks like a broken solver rather than a wrong address. That is exactly
    what a live install reported on 2026-10-13, after three days of the panel
    looking like the right thing to paste.

    `/app/<slug>` carries the slug, and the container hostname is mechanical from
    it (`571f4c18_xpeng_slider_solver` is served by `571f4c18-xpeng-slider-solver`),
    so that form is rewritten and the note says what it did. An ingress URL with a
    signed token in it carries nothing to derive, so it is left alone and the note
    names the addresses that do work. Every other value passes through untouched -
    a LAN address, a custom port or a hostname is never second-guessed.

    Returns ``(url, note)``; the note is empty when nothing needed saying.
    """
    url = (raw or "").strip().rstrip("/")
    if not url:
        return "", ""
    if "/app/" in url:
        slug = url.split("/app/", 1)[1].split("/")[0].split("?")[0]
        if slug:
            fixed = f"http://{slug.replace('_', '-').lower()}:{_SOLVER_API_PORT}"
            return fixed, (
                f"solver_url was the sidebar panel ({url}), which only answers GET - "
                f"POST /solve to it returns 405. Using the container hostname "
                f"instead: {fixed} - set solver_url to that to silence this."
            )
    if "/api/hassio_ingress/" in url or ":8123" in url:
        return url, (
            f"solver_url ({url}) is a Home Assistant address, not the solver's own. "
            f"The solver answers on port {_SOLVER_API_PORT}: its container hostname "
            f"http://<hash>-xpeng-slider-solver:{_SOLVER_API_PORT} (hyphens, not "
            f"underscores), or the HA host itself http://homeassistant.local:"
            f"{_SOLVER_API_PORT}."
        )
    return url, ""


EMAIL = _setting("email")
PASSWORD = _setting("password")
DEVICE_ID = _setting("device_id")
SOLVER_URL, SOLVER_URL_NOTE = _solver_url(_setting("solver_url"))
SOLVER_TOKEN = _setting("solver_token")
RENEW_TOKEN = _setting("renew_token")
PAIR_DIR = _setting("pair_dir", "/config/xpeng-secrets")
#: Pin delivery to one config entry by its id. Empty means "the entry for this
#: pair's VIN" (the title carries the last six characters), which is what makes a
#: second car on a second account work without touching the app.
ENTRY_ID = _setting("entry_id")
# What the integration may do to the car, applied when this app creates the
# config entry. Sent as a field on the import_step form, which is where the
# integration asks the question (it must not be a step of its own: this app
# requires `create_entry` back from the POST below). An older integration
# ignores an unknown field; a newer one stores it.
COMMAND_MODE = _setting("command_mode", "safe_plus") or "safe_plus"
# An override for the VIN used to create a config entry on a fresh install. The
# onboarding tool records one in the token file when the API gave it one, so this
# is only needed for a pair written before that, or by hand.
VIN = _setting("vin")
# How often to renew, in seconds, and 0 to disable. Inside the ~2h session cap with
# room for a retry, which is the number docs/UNATTENDED_RENEWAL.md works from.
RENEW_INTERVAL = _setting_int("renew_interval", 6000)
# How often to ask Home Assistant whether the credentials are still good, so a session
# that dies early is replaced on the next check rather than at the next tick.
INVALID_POLL_INTERVAL = _setting_int("invalid_poll_interval", 300)
#: Whether a successful renewal raises a persistent notification. A long-lived install
#: renews roughly 24 times a day, so the success card is a per-install choice; failures
#: still notify (after NOTIFY_AFTER consecutive attempts) either way. Default true keeps
#: the behaviour every existing install had.
NOTIFY_ON_SUCCESS = _setting_bool("notify_on_success", True)
CORE_API = _setting("core_api", "http://supervisor/core/api")
SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")

STATE = {
    "started_at": time.time(),
    "renewals": 0,
    "failures": 0,
    "consecutive_failures": 0,
    #: A pair was minted (so the integration's session was evicted) but could not
    #: be delivered. The scheduler retries the *delivery* every
    #: RETRY_IMPORT_SECONDS, without another login.
    "pending_import": False,
    "import_retries": 0,
    "running": False,
    "last": None,
    # Reported by /healthz so the schedule is as visible as it was when an
    # automation and its helper were the clock.
    "next_run": 0.0,
    "schedule_reason": "",
}
LOCK = threading.Lock()
#: Resolved once by _credentials_entity(); empty until then.
_CREDENTIALS_ENTITY: list[str] = []


def _log(message: str) -> None:
    print("[renew] %s" % message, flush=True)


def _device_id() -> str:
    """Keep one identity across renewals.

    A renewal is a login, so it mints a device id if none is given - and a fresh
    random one every time would keep changing the identity XPENG sees. Configure
    device_id, or the first renewal's value is remembered here.
    """
    if DEVICE_ID:
        return DEVICE_ID
    try:
        with open(DEVICE_ID_FILE, encoding="utf-8") as handle:
            remembered = handle.read().strip()
        if remembered:
            return remembered
    except FileNotFoundError:
        pass
    return ""


def _tail(primary: str, secondary: str = "", lines: int = 4, limit: int = 400) -> str:
    """Last non-empty ``lines`` of ``primary``, falling back to ``secondary``.

    Both streams matter. The tool writes its progress to stdout and its
    traceback to stderr, so reading only one of them hides exactly the half that
    explains a failure.

    A message longer than ``limit`` keeps its END. The specific part of a
    traceback, or of a browser launch failure, is at the bottom; trimming from
    the front threw away exactly the line that named the cause.
    """
    for text in (primary, secondary):
        kept = [line for line in (text or "").splitlines() if line.strip()][-lines:]
        if kept:
            joined = " | ".join(kept)
            if len(joined) <= limit:
                return joined
            return "..." + joined[-(limit - 3):]
    return ""


def _run_log_path() -> str:
    """Where the last run's full output is kept: beside this slot's pair dir.

    Both app slots map ``/config``, so a fixed filename had the second car's run
    overwrite the first car's log — and the log is the evidence a failed renewal
    leaves. Deriving it from ``pair_dir`` gives every slot its own file without
    another option to configure (issue #11).
    """
    return PAIR_DIR.rstrip("/") + "-last-run.log"


def _write_run_log(stdout: str, stderr: str) -> None:
    """Keep the onboarding tool's whole output in a readable file.

    The health endpoint can only carry a summary, and the useful part of a
    browser failure is long: Playwright prints the failing line, then a boxed
    hint. Chasing Chromium through truncated summaries cost several releases,
    so every run leaves its full output here instead. Written next to the pair
    directory the add-on publishes, which is inside the mapped config directory.
    """
    path = _run_log_path()
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(
                "# Full output of the onboarding tool from the last renewal.\n"
                "# Treat as sensitive: it describes the account's login attempts.\n"
                "# stdout\n%s\n# stderr\n%s\n" % (stdout, stderr)
            )
        os.chmod(path, 0o600)
    except Exception as exc:  # noqa: BLE001 - diagnostics must never fail a run
        _log("could not write %s: %s" % (path, exc))


def _mint_pair() -> tuple[bool, str]:
    """Run the toolkit's onboarding tool, which writes the pair next to itself."""
    cmd = [
        sys.executable, TOOL,
        "--email", EMAIL,
        "--password", PASSWORD,
        "--tools-dir", os.path.join(VENDOR, "tools"),
        "--device-id-out", DEVICE_ID_FILE,
        # guardian_token.mjs resolves playwright-core from
        # <playwright-dir>/node_modules, and this image installs it beside our
        # own code (see Dockerfile). The tool's default, /tmp/pwdir, has none,
        # which fails the run after the slider has already been solved.
        "--playwright-dir", ROOT,
        # The add-on has no display: the Guardian helper's default headful
        # launch fails outright with "launched a headed browser without having a
        # XServer running", which is what ended every unattended renewal right
        # after the slider had been solved.
        "--guardian-headless",
        "--save", "--mqtt",
    ]
    device_id = _device_id()
    if device_id:
        cmd += ["--device-id", device_id]
    if SOLVER_URL:
        cmd += ["--captcha-solver", SOLVER_URL]
        if SOLVER_TOKEN:
            cmd += ["--captcha-solver-token", SOLVER_TOKEN]

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    _write_run_log(proc.stdout or "", proc.stderr or "")
    # On failure stderr holds the traceback, and stdout holds only the progress
    # lines printed before it died. Reporting stdout alone is what reduced a
    # missing-certificate failure to a bare "[0] device id = ..." and sent the
    # 2026-10-10 investigation looking for a request that never arrived.
    if proc.returncode != 0:
        return False, "onboarding failed: " + _tail(
            proc.stderr, proc.stdout, lines=5, limit=900
        )
    return True, _tail(proc.stdout, proc.stderr, lines=3, limit=900)


def _publish_pair() -> str:
    """Copy the pair somewhere Home Assistant's Core process can read it."""
    os.makedirs(PAIR_DIR, exist_ok=True)
    for name in ("xpeng_tokens.json", "xpeng_tenant.json"):
        destination = os.path.join(PAIR_DIR, name)
        shutil.copyfile(os.path.join(PAIR_SOURCE, name), destination)
        os.chmod(destination, 0o600)
    return PAIR_DIR


def _call_service(path: str, payload: dict) -> tuple[int, str]:
    request = urllib.request.Request(
        CORE_API + path,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": "Bearer " + SUPERVISOR_TOKEN,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            return response.status, response.read().decode()[:400]
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()[:400]
    except Exception as exc:  # noqa: BLE001
        return 0, "%s: %s" % (type(exc).__name__, exc)


def _core_json(path: str, payload: dict | None = None, method: str = "POST"):
    """A Core API call whose response body has to survive intact.

    _call_service() truncates to 400 characters because what it returns goes into a
    log line. That is right for an error and useless for JSON, so this keeps the
    whole body and parses it.
    """
    request = urllib.request.Request(
        CORE_API + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={
            "Authorization": "Bearer " + SUPERVISOR_TOKEN,
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, json.loads(response.read().decode() or "null")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()[:400]
    except Exception as exc:  # noqa: BLE001
        return 0, "%s: %s" % (type(exc).__name__, exc)


def _existing_entry() -> dict | None:
    """This install's XPENG config entry, if it has one yet."""
    return _entry_report()[1]


def _entry_state() -> str:
    """Whether this pair's XPENG entry is present, absent, missing, or unknown.

    The distinction is the whole of the failed-renewal window: "no entry" means
    bootstrap one from the pair, while "could not ask" means Home Assistant is
    restarting or unreachable and the pair already minted should simply be
    *retried*, not used to create a second entry. Before this existed, a renewal
    that ran during an HA restart treated the 502 as "no entry", tried to start a
    config flow, failed, and left the integration evicted with a good pair on disk
    (measured 2026-10-17 21:48).

    "missing" is the explicit ``entry_id`` naming no enabled entry. That is a
    configuration error, not a fresh install: bootstrapping would create a second
    entry and hide the mistake, so delivery waits and reports it instead.
    """
    return _entry_report()[0]


def _select_entry(entries: list[dict]) -> dict | None:
    """The entry this pair belongs to, by the VIN suffix in its title.

    A config entry is titled ``XPENG <last six of the VIN>`` (the same suffix the
    credentials entity is built from), so two cars in one Home Assistant resolve to
    their own entries. A single-entry install keeps the old behaviour when the pair
    has no readable VIN, so an upgrade cannot start refusing deliveries.

    A VIN that IS readable but matches no title is not that case: this pair belongs
    to a car this Home Assistant does not know yet, so the answer is "absent" and
    the import bootstraps an entry. Falling back to the only entry imported car B's
    pair into car A's entry, and two renewal instances then rotated one account's
    credentials against each other (issue #18, measured 2026-10-30). ``entry_id``
    remains the way to say "this pair goes into that entry" for a title this
    integration did not write, such as a renamed entry.
    """
    vin = _vin_from_pair(PAIR_DIR).lower()
    if vin:
        suffix = vin[-6:]
        for entry in entries:
            if str(entry.get("title") or "").strip().lower().endswith(suffix):
                return entry
        return None
    return entries[0] if len(entries) == 1 else None


def _entry_report() -> tuple[str, dict | None]:
    """The delivery state, and the entry the pair should be imported into.

    One Home Assistant call answers both questions - which entry receives the pair
    and whether Home Assistant can be asked at all - so the import can name its
    target explicitly instead of relying on "the first entry", which is wrong on a
    multi-car install.
    """
    status, body = _core_json("/config/config_entries/entry", method="GET")
    if status != 200 or not isinstance(body, list):
        return "unknown", None
    entries = [
        entry
        for entry in body
        if isinstance(entry, dict)
        and entry.get("domain") == "xpeng"
        and not entry.get("disabled_by")
    ]
    if ENTRY_ID:
        match = next(
            (entry for entry in entries if str(entry.get("entry_id") or "") == ENTRY_ID),
            None,
        )
        if match is None:
            _log(
                "entry_id %s matches no enabled XPENG entry; check the option"
                % ENTRY_ID
            )
            return "missing", None
        return "present", match
    entry = _select_entry(entries)
    return ("present", entry) if entry is not None else ("absent", None)


def _vin_from_pair(pair_dir: str) -> str:
    """The VIN recorded in the token file, or "".

    Same two shapes the integration accepts: the tool's ``json`` wrapper, or a bare
    token object. Only a real VIN is returned (17 alphanumeric ASCII characters),
    so a placeholder cannot reach the config entry.
    """
    if VIN:
        return VIN.strip().upper()
    try:
        with open(
            os.path.join(pair_dir, "xpeng_tokens.json"), encoding="utf-8"
        ) as handle:
            raw = json.load(handle)
    except Exception:  # noqa: BLE001 - an unreadable pair is reported by the import
        return ""

    inner = raw
    if isinstance(raw, dict):
        for field in ("json", "jsonText"):
            candidate = raw.get(field)
            if isinstance(candidate, dict):
                inner = candidate
                break
            if isinstance(candidate, str) and candidate.lstrip().startswith("{"):
                try:
                    inner = json.loads(candidate[candidate.find("{"):])
                except Exception:  # noqa: BLE001
                    pass
                break
    if not isinstance(inner, dict):
        return ""

    candidates = [inner.get("vin"), inner.get("VIN")]
    for field in ("vehicle", "vehicleInfo", "data"):
        nested = inner.get(field)
        if isinstance(nested, dict):
            candidates.extend((nested.get("vin"), nested.get("VIN")))
    for candidate in candidates:
        value = str(candidate or "").strip().upper()
        if len(value) == 17 and value.isascii() and value.isalnum():
            return value
    return ""


def _bootstrap_entry(pair_dir: str) -> tuple[bool, str]:
    """Create the config entry through the config flow, without a human.

    This is the step that used to be manual, and it cannot go through
    ``xpeng.import_credentials``: on an install with no entry that service does not
    exist yet, because the integration's component-level setup only runs once an
    entry exists. Measured, not assumed (2026-10-11): with no entry, the xpeng
    service domain is absent from ``/api/services``, and starting a flow does not
    load it either. What *is* loaded on demand is the config flow, so driving it
    here creates the entry from the pair just minted - and creating the entry is
    what loads the component and registers the service for every renewal after it.
    """
    vin = _vin_from_pair(pair_dir)
    if not vin:
        return False, (
            "no VIN to create the config entry with, and no entry to import into: "
            "add the integration once by hand, or set this app's `vin` option"
        )

    status, body = _core_json("/config/config_entries/flow", {"handler": "xpeng"})
    if status != 200 or not isinstance(body, dict) or not body.get("flow_id"):
        return False, "could not start the config flow: %s %s" % (status, body)
    flow_id = body["flow_id"]

    # Which step this lands on is the integration's decision, not ours. Since 0.4.13 an
    # install with a pair already in `pair_dir` skips the method question and opens on
    # `import_tokens`; anything else opens on `user`, which asks the auth method and
    # nothing else. Sending `auth_method` to `import_tokens` is rejected by that step's
    # form schema:
    #
    #     400 {"errors":{"base":["not a valid option at 'auth_method'"]}}
    #
    # which is what happened on the first fresh install against 0.4.16: the pair was
    # minted, the entry was not created, and the install stayed half-finished with no
    # entities at all. So ask the step what it is, and answer only the question it asked.
    #
    # The VIN still rides along with both posts: `import_tokens` reads it out of the pair
    # (the field there is only a fallback) and `user` has no VIN question at all, but it
    # drops unknown keys rather than refusing them - so this post is safe on either
    # version, and the pair's VIN is what names the entry.
    if body.get("step_id") == "user":
        status, body = _core_json(
            "/config/config_entries/flow/%s" % flow_id,
            {"vin": vin, "auth_method": "import"},
        )
        if status != 200 or not isinstance(body, dict):
            return False, "the import step was not reached: %s %s" % (status, body)

    if body.get("step_id") != "import_tokens":
        return False, "the import step was not reached: %s %s" % (status, body)

    status, body = _core_json(
        "/config/config_entries/flow/%s" % flow_id,
        {
            "tokens_path": os.path.join(pair_dir, "xpeng_tokens.json"),
            "tenant_path": os.path.join(pair_dir, "xpeng_tenant.json"),
            "vin": vin,
            # The integration's command-permission question travels as an
            # optional field on this form, so a fresh install created here is
            # not silently left at Full access.
            "command_mode": COMMAND_MODE,
        },
    )
    if status != 200 or not isinstance(body, dict) or body.get("type") != "create_entry":
        return False, "the entry was not created: %s %s" % (status, body)
    _log("created the config entry from this pair (VIN %s)" % vin)
    return True, vin


def _notify(title: str, message: str) -> None:
    _call_service(
        "/services/persistent_notification/create",
        {"title": title, "message": message, "notification_id": "xpeng_renewal"},
    )


def _pair_on_disk() -> bool:
    """Whether a complete minted pair is waiting in PAIR_DIR to be delivered."""
    return all(
        os.path.isfile(os.path.join(PAIR_DIR, name))
        for name in ("xpeng_tokens.json", "xpeng_tenant.json")
    )


def _pair_is_fresh(max_age_s: int = 3600) -> bool:
    """Whether the pair on disk was minted recently enough to be worth delivering.

    A pair from days ago is almost certainly one that was already imported (the app
    leaves it behind), and delivering it again would replace a working credential
    with an older one. An hour is comfortably longer than any outage this exists to
    shorten, and comfortably shorter than the renewal interval.
    """
    if not _pair_on_disk():
        return False
    newest = 0.0
    for name in ("xpeng_tokens.json", "xpeng_tenant.json"):
        try:
            newest = max(newest, os.path.getmtime(os.path.join(PAIR_DIR, name)))
        except OSError:
            return False
    return (time.time() - newest) <= max_age_s


def _deliver_pair(pair_dir: str) -> dict:
    """Get a minted pair into Home Assistant. Never logs in.

    Four outcomes matter, and they are told apart by ``_entry_report()``:

    * ``present`` - import into the entry this pair belongs to (named explicitly,
      so a multi-car install cannot pick the wrong one);
    * ``absent``  - a fresh install. ``import_credentials`` does not exist until
      an entry does (component setup only runs once one exists), so drive the
      config flow instead and treat the created entry as the delivery. This is
      what makes the first renewal finish with no human;
    * ``missing`` - an explicit ``entry_id`` that names no enabled entry. That is
      a configuration error to report, not a fresh install to bootstrap;
    * ``unknown`` - Home Assistant is restarting or unreachable. The pair is
      minted and safe on disk, so report it as retryable *delivery* rather than
      as a failed renewal: another login would mint yet another pair and evict
      whatever session had just come back.

    ``retry_import`` in the result says whether another attempt at *this* pair
    is worth making without logging in again.
    """
    state, entry = _entry_report()

    if state == "absent":
        bootstrapped, outcome = _bootstrap_entry(pair_dir)
        if not bootstrapped:
            return {
                "ok": False,
                "error": outcome,
                "retry_import": True,
            }
        return {"ok": True, "bootstrapped": outcome}

    if state == "missing":
        return {
            "ok": False,
            "error": (
                "the configured entry_id (%s) matches no enabled XPENG entry; "
                "fix the option or clear it" % (ENTRY_ID or "?")
            ),
            "retry_import": True,
        }

    if state == "unknown":
        return {
            "ok": False,
            "error": (
                "Home Assistant was unreachable while delivering the fresh pair "
                "(it may be restarting); the pair is saved and will be retried "
                "without another login"
            ),
            "retry_import": True,
        }

    # ?return_response is mandatory, not a nicety: the integration registers this
    # service with SupportsResponse.ONLY, and Home Assistant refuses a call that
    # does not ask for the response with HTTP 400 "Service call requires responses
    # but caller did not ask for responses". The response is also the import
    # result - entry id, VIN, device id, client id.
    payload = {
        "tokens_path": os.path.join(pair_dir, "xpeng_tokens.json"),
        "tenant_path": os.path.join(pair_dir, "xpeng_tenant.json"),
    }
    if entry is not None:
        target = str(entry.get("entry_id") or "").strip()
        if target:
            payload["entry_id"] = target
    status, body = _call_service(
        "/services/xpeng/import_credentials?return_response", payload
    )
    if status != 200:
        # A 4xx is the pair being rejected (bad/expired tokens, a mismatch): a new
        # login is the only cure. 0/5xx is Home Assistant being unavailable, which
        # this pair survives - retry the delivery, do not mint again.
        return {
            "ok": False,
            "error": "import_credentials returned %s: %s" % (status, body),
            "retry_import": status == 0 or status >= 500,
        }
    return {"ok": True, "imported": body}


def renew() -> dict:
    """One full renewal: log in, mint a pair, deliver it. Never raises."""
    started = time.time()
    if not EMAIL or not PASSWORD:
        return {"ok": False, "error": "email/password are not configured"}

    try:
        ok, detail = _mint_pair()
        if not ok:
            # Nothing was minted, so nothing was evicted: retrying can wait, and
            # should - every attempt costs a slider solve.
            return {
                "ok": False,
                "error": detail,
                "retry_import": False,
                "retry_in": RETRY_LOGIN_SECONDS,
                "ms": int((time.time() - started) * 1000),
            }

        pair_dir = _publish_pair()
        result = _deliver_pair(pair_dir)
        result["onboarding"] = detail
        result["ms"] = int((time.time() - started) * 1000)
        return result
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "onboarding timed out", "ms": int((time.time() - started) * 1000)}
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": "%s: %s" % (type(exc).__name__, exc),
            "ms": int((time.time() - started) * 1000),
        }


def retry_pending_import() -> dict:
    """Deliver the pair already on disk, without logging in again."""
    started = time.time()
    if not _pair_on_disk():
        return {
            "ok": False,
            "error": "no minted pair on disk to import",
            "retry_import": False,
            "ms": 0,
        }
    result = _deliver_pair(PAIR_DIR)
    result["ms"] = int((time.time() - started) * 1000)
    return result


def _next_delay(result: dict) -> int:
    """How long to wait before the next attempt, from what the attempt returned."""
    if result.get("ok"):
        return RENEW_INTERVAL
    if result.get("retry_import"):
        return RETRY_IMPORT_SECONDS
    return max(RETRY_IMPORT_SECONDS, int(result.get("retry_in") or RETRY_LOGIN_SECONDS))


def _no_entry_yet() -> bool:
    """Whether Home Assistant answered *and* has no XPENG entry worth keeping.

    The distinction matters at startup: "no entry" is a fresh install that should be
    bootstrapped now, while "could not ask" is an API hiccup that must not set off a
    renewal - and `_existing_entry()` cannot tell them apart, because it answers None
    for both.

    A DISABLED entry counts as no entry. Home Assistant hides those from the UI but
    not from this list, so an install that disabled its old entry while moving to a
    new one looked finished: the bootstrap never ran, the health endpoint reported
    the first renewal thousands of seconds away, and nothing said why (measured
    2026-10-13 on a real install - the entry was also still holding the VIN as its
    unique_id, so the flow would have refused to create a replacement either way).
    """
    return _entry_state() == "absent"


def _credentials_entity() -> str:
    """The entity id of the integration's credentials sensor, discovered once.

    ``binary_sensor.xpeng_<VIN suffix>_credentials_valid`` - the same suffix the entry
    title uses - so with a VIN in the pair it costs one small request. Without one,
    /states is scanned once and the answer kept.
    """
    if _CREDENTIALS_ENTITY:
        return _CREDENTIALS_ENTITY[0]
    vin = _vin_from_pair(PAIR_DIR)
    if vin:
        _CREDENTIALS_ENTITY.append(
            "binary_sensor.xpeng_%s_credentials_valid" % vin[-6:].lower()
        )
        return _CREDENTIALS_ENTITY[0]
    status, body = _core_json("/states", method="GET")
    if status == 200 and isinstance(body, list):
        for state in body:
            entity_id = str(state.get("entity_id") or "") if isinstance(state, dict) else ""
            if entity_id.startswith("binary_sensor.xpeng_") and entity_id.endswith(
                "_credentials_valid"
            ):
                _CREDENTIALS_ENTITY.append(entity_id)
                break
    return _CREDENTIALS_ENTITY[0] if _CREDENTIALS_ENTITY else ""


def _credentials_invalid() -> bool:
    """Whether Home Assistant says the credentials are already dead.

    It is the only thing that knows: to this app a session retired early and one
    simply due look identical. Polling it is what keeps the old automation's
    "renew immediately when the credentials go invalid" behaviour without needing
    that automation to exist.
    """
    entity_id = _credentials_entity()
    if not entity_id:
        return False
    status, body = _core_json("/states/%s" % entity_id, method="GET")
    if status == 200 and isinstance(body, dict):
        return body.get("state") == "off"
    # Stale id (renamed entity, or a different VIN): forget it and rediscover.
    _CREDENTIALS_ENTITY.clear()
    return False


def _renewal_due(now: float, next_run: float, credentials_invalid: bool) -> str:
    """Why a renewal should happen now, or "" if it should not.

    Split out because it is the whole of the scheduling policy and it is worth being
    able to test without a clock or a broker: the interval decides as before, and an
    already-invalid credential set overrides it, which is what stops a session that
    died early from waiting out the rest of the interval.
    """
    if now >= next_run:
        return "due"
    if credentials_invalid:
        return "credentials already invalid"
    return ""


def _scheduler(tick: float | None = None) -> None:
    """Renew on a timer, so nothing outside this app has to call it.

    The tick is short and the renewal interval long, so the time between renewals is
    spent checking whether the credentials have already gone. A session that dies
    early is therefore replaced within one tick instead of waiting out the rest of the
    interval - which is the one thing the Home Assistant automation did that a plain
    timer cannot.

    `renew_interval: 0` disables this, for an install that would rather trigger
    renewals itself (a manual call, or its own automation).
    """
    if RENEW_INTERVAL <= 0:
        _log("scheduler disabled (renew_interval=0): renewals must be triggered")
        return

    # A fresh install should not wait out a whole interval for its first renewal -
    # that wait is what made "trigger it once by hand" a documented step.
    if _no_entry_yet():
        _log("no config entry yet: renewing now to create it")
        result = _run_locked()
        if not result["ok"] and result.get("retry_import"):
            STATE["pending_import"] = True
    elif _pair_on_disk() and _pair_is_fresh() and _credentials_invalid():
        # The add-on restarted between minting a pair and delivering it (a build,
        # a supervisor restart): the login has already evicted the session, so the
        # pair on disk is the thing to deliver - and doing it now, without a new
        # login, is what keeps this window short.
        STATE["pending_import"] = True
        _log(
            "credentials are invalid and a freshly minted pair is on disk: "
            "delivering it without another login"
        )

    STATE["next_run"] = time.time() + RENEW_INTERVAL
    # 10s floor so a misconfigured zero cannot become a hot loop. `tick` lets a test
    # drive the loop without waiting out a real interval.
    interval = max(10, INVALID_POLL_INTERVAL) if tick is None else tick
    _log(
        "scheduler: renew every %ds, credentials checked every %ds"
        % (RENEW_INTERVAL, interval)
    )
    while True:
        # Sleep until the next attempt is actually due, not just until the next
        # tick: a retryable delivery failure waits RETRY_IMPORT_SECONDS, and a
        # 300s poll would turn 60 seconds into five minutes of dark.
        sleep_for = interval
        if STATE["next_run"]:
            sleep_for = min(interval, max(5, STATE["next_run"] - time.time()))
        time.sleep(sleep_for)
        reason = _renewal_due(time.time(), STATE["next_run"], _credentials_invalid())
        if not reason:
            continue
        result = _run_locked()
        if result.get("error") == "a renewal is already running":
            _log("scheduler: a renewal is already running, will retry")
            continue
        delay = _next_delay(result)
        STATE["next_run"] = time.time() + delay
        STATE["schedule_reason"] = reason
        _log(
            "scheduler: %s (%s) ok=%s next in %ds%s"
            % (
                "delivered saved pair" if STATE["last"].get("kind") == "import"
                else "renewed",
                reason,
                result.get("ok"),
                delay,
                " (pending import)" if STATE["pending_import"] else "",
            )
        )


def _record_result(result: dict, kind: str = "renew") -> None:
    """Counters and the user-facing notification, for one attempt."""
    if kind == "renew":
        STATE["renewals"] += 1
    STATE["last"] = {**result, "at": time.time(), "kind": kind}
    if result["ok"]:
        STATE["consecutive_failures"] = 0
        _log("%s ok in %sms" % (kind, result.get("ms")))
        if NOTIFY_ON_SUCCESS:
            _notify("XPENG credentials renewed", "A fresh token pair was imported.")
        return
    STATE["failures"] += 1
    STATE["consecutive_failures"] += 1
    _log("%s failed: %s" % (kind, result.get("error")))
    if STATE["consecutive_failures"] >= NOTIFY_AFTER:
        _notify(
            "XPENG renewal failing",
            "The last %d delivery attempts failed. Most recent reason: %s"
            % (STATE["consecutive_failures"], str(result.get("error"))[:300]),
        )


def _run_locked() -> dict:
    if not LOCK.acquire(blocking=False):
        return {"ok": False, "error": "a renewal is already running"}
    try:
        STATE["running"] = True

        # A pair minted but not delivered is delivered on its own first. The login
        # that minted it already evicted the integration's session, so repeating
        # the login to fix a delivery failure would only lengthen the outage.
        if STATE["pending_import"]:
            STATE["import_retries"] += 1
            result = retry_pending_import()
            if result["ok"] or result.get("retry_import"):
                STATE["pending_import"] = not result["ok"]
                _record_result(result, kind="import")
                return result
            # The pair itself was refused (not a transient HA failure), so only a
            # fresh login can help now.
            STATE["pending_import"] = False
            _log(
                "the saved pair was refused (%s); minting a new one"
                % result.get("error")
            )

        result = renew()
        if not result["ok"] and result.get("retry_import"):
            STATE["pending_import"] = True
        _record_result(result)
        return result
    finally:
        STATE["running"] = False
        LOCK.release()


class Handler(BaseHTTPRequestHandler):
    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path.rstrip("/") not in ("/healthz", ""):
            return self._json(404, {"ok": False, "error": "GET /healthz"})
        return self._json(200, {
            "ok": True,
            "uptime_s": int(time.time() - STATE["started_at"]),
            "renewals": STATE["renewals"],
            "failures": STATE["failures"],
            "consecutive_failures": STATE["consecutive_failures"],
            "running": STATE["running"],
            "pending_import": STATE["pending_import"],
            "import_retries": STATE["import_retries"],
            "last": STATE["last"],
            "scheduler": {
                "interval_s": RENEW_INTERVAL,
                "next_run_s": max(0, int(STATE["next_run"] - time.time()))
                if STATE["next_run"]
                else None,
                "last_reason": STATE["schedule_reason"] or None,
            },
            "configured": {
                "email": EMAIL,
                "solver_url": SOLVER_URL,
                **({"solver_url_note": SOLVER_URL_NOTE} if SOLVER_URL_NOTE else {}),
                "pair_dir": PAIR_DIR,
                "device_id": "configured" if DEVICE_ID else "remembered/generated",
                "auth": "bearer" if RENEW_TOKEN else "open",
            },
        })

    def do_POST(self):  # noqa: N802
        if self.path.rstrip("/") != "/renew":
            return self._json(404, {"ok": False, "error": "POST /renew"})
        if RENEW_TOKEN and self.headers.get("Authorization") != "Bearer " + RENEW_TOKEN:
            return self._json(401, {"ok": False, "error": "unauthorised"})
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        result = _run_locked()
        # A manual trigger is a renewal: move the scheduled one out by the interval,
        # or the timer fires minutes later and logs in again (that happened on
        # 2026-10-17: a manual renew at 21:44, the scheduled run at 21:48 evicted the
        # session a second time while HA was restarting, and the import failed).
        STATE["next_run"] = time.time() + _next_delay(result)
        _log("manual trigger: next scheduled attempt in %ds" % _next_delay(result))
        return self._json(200 if result["ok"] else 502, result)

    def log_message(self, fmt, *args):  # route access logs through our prefix
        _log(fmt % args)


if __name__ == "__main__":
    port = int(os.environ.get("PORT") or OPTIONS.get("port") or 8090)
    if SOLVER_URL_NOTE:
        # Startup, not first renewal: a corrected address is worth seeing before
        # the run that would have failed cryptically three hours later.
        _log("[!] " + SOLVER_URL_NOTE)
    _log("listening on :%d (email=%s solver=%s auth=%s)"
         % (port, EMAIL or "<unset>", SOLVER_URL or "<unset>",
            "bearer" if RENEW_TOKEN else "open"))
    threading.Thread(target=_scheduler, name="scheduler", daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
