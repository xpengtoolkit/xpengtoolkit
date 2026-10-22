"""Typed errors and error codes shared by the API client and the pollers.

Standard library only, and free of Home Assistant imports, so the handling that
matters here can be tested without either. `xpeng_api` needs `cryptography` and
is not importable in a bare Python; this is.

Why it exists: XPENG answers `12010101 "Login status expired"` once the access
token ages out, and from then on *every* API call fails — including the MQTT
credential refresh. The REST path has always recognised that code and recovered
by calling `refresh_token`. The credential path did not, and it logged its
failures at DEBUG, so on 2026-10-06 it stopped minting credentials for 41 minutes
and the only visible sign was a credential age climbing to 40m. Recognising the
code is the whole fix, so the code lives in one place and both callers use it.
"""

from __future__ import annotations

TOKEN_EXPIRED_CODES: frozenset[int] = frozenset({12010101, 12010104, 12010202})
"""XPENG error codes meaning "log in again".

12010101 is the observed one ("Login status expired. Please log in again");
12010104 is treated identically by the REST path and has been since before this
module existed, so it is kept rather than narrowed to what has been seen.

12010202 is what `/sso/app/v3/token/refresh` answers once the *session* behind a
refresh token has been retired by a later login on the same account:

    POST /sso/app/v3/token/refresh -> HTTP 401
    {"code": 12010202, "data": null,
     "msg": "Anmeldestatus abgelaufen. Bitte melden Sie sich erneut an",
     "desc": "Login status has expired, please login again"}

Observed 2026-10-10 against a pair minted hours earlier. It is a different code
from the 12010101 the same dead token earns on an ordinary endpoint, and while
it was missing from this set the refresh path classified it as a transient
connection error: the typed LoginExpiredError never fired, so a dead session was
retried every 45 s instead of being surfaced as "re-authenticate" (87 attempts
an hour for 15 hours, with the cause named nowhere in the log).
"""


def is_token_expired_response(data: object) -> bool:
    """Whether an API response payload reports an expired login token.

    Deliberately tolerant of shape: these payloads arrive as dicts with an
    integer `code` from some endpoints and as wrapped or re-parsed JSON from
    others, and a false negative here is what caused the outage.
    """
    if not isinstance(data, dict):
        return False
    return data.get("code") in TOKEN_EXPIRED_CODES


class LoginExpiredError(Exception):
    """The OAuth login token is no longer valid.

    A distinct type rather than a ValueError, because the caller has to tell
    "log in again" apart from "this response is malformed". The MQTT credential
    refresh depends on that distinction: indistinguishably wrapped in a generic
    error, it was swallowed at DEBUG for 41 minutes.
    """

    def __init__(self, code: int, message: str = "") -> None:
        detail = f": {message}" if message else ""
        super().__init__(f"login expired (code {code}){detail}")
        self.code = code
        self.message = message


def command_response_message(code: object) -> str:
    """English display text for MQTT feedback; retain the vendor text as an attribute.

    MQTT success is 200. Code 8 is the documented unsupported seat command;
    unknown codes remain visible without presenting untranslated API text as UI.
    """
    if code == 200:
        return "Command accepted"
    if code == 10401:
        return "Charging could not be started (code 10401)"
    if code == 8:
        return "Command not supported by this vehicle (code 8)"
    if code is None:
        return "Command response received"
    return f"Command rejected by vehicle (code {code})"
