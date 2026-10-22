"""XPENG MQTT client - single persistent connection following bridge pattern."""
from __future__ import annotations

import json
import logging
import ssl
import threading
import time
from pathlib import Path
from typing import Any, Callable, Final

import paho.mqtt.client as mqtt

_LOGGER = logging.getLogger(__name__)

# Fallback directory for the mTLS material. Kept for callers that do not pass
# cert_dir; the integration passes the user-configured CONF_CERT_DIR instead.
CERT_DIR = Path(__file__).parent / "certs"

# Files required for mTLS, looked up inside the configured cert dir.
# ca-chain.crt is a public trust anchor; client-cert-0.pem and client-key.pem
# are app-level secrets the user must supply (never distributed with the
# project — see certs/README.md). The one list lives here and is imported by
# config_flow.py for its options-form validation.
CERT_FILES: Final = ("ca-chain.crt", "client-cert-0.pem", "client-key.pem")


def missing_cert_files(cert_dir: str | Path) -> list[Path]:
    """Return the required mTLS files that are missing from cert_dir.

    Paths are used as given (no ``~`` expansion) — absolute paths are expected,
    matching DEFAULT_CERT_DIR.
    """
    directory = Path(cert_dir)
    return [
        directory / name for name in CERT_FILES if not (directory / name).is_file()
    ]


def build_tls_context(cert_dir: str | Path) -> ssl.SSLContext:
    """Build the verifying mTLS context for the XPENG broker from cert_dir.

    Loads the client cert/key and the XPENG CA chain, then requires a verified
    peer (CERT_REQUIRED plus hostname checking) so the broker really is
    authenticated. The CA has to be loaded before verify_mode is set: with
    PROTOCOL_TLS_CLIENT verify_mode defaults to CERT_REQUIRED, so
    load_verify_locations() must run against that default rather than a
    verification-disabled context.

    Raises:
        FileNotFoundError: if any required file is missing, naming the missing
            file(s) and the directory searched.
    """
    directory = Path(cert_dir)
    missing = missing_cert_files(directory)
    if missing:
        raise FileNotFoundError(
            f"XPENG mTLS certificate file(s) missing from {directory}: "
            f"{', '.join(p.name for p in missing)}. "
            f"Required files: {', '.join(CERT_FILES)}. "
            "See custom_components/xpeng/certs/README.md for how to obtain "
            "them, or point the integration's 'Certificate directory' option "
            "at the directory holding them."
        )

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(
        str(directory / "client-cert-0.pem"),
        str(directory / "client-key.pem"),
    )
    ctx.load_verify_locations(cafile=str(directory / "ca-chain.crt"))
    # ca-chain.crt validates the broker's chain and the leaf CN matches the
    # host we dial, so demand a verified peer instead of accepting any cert.
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.check_hostname = True
    return ctx


#: Prefix of the app's device-scoped push channel. Unlike every other topic the
#: XPENG app uses, this one is keyed by the device identity rather than the
#: account uid: the app subscribes ``Xp/Msg/Push/<deviceId>`` on every connect at
#: QoS 2 (MqttClient$MqttConnectListener.onSuccess, app 3.11.0). It carries
#: push/IM notifications (a ``messageId`` + ``content`` envelope whose
#: ``content.msgType`` is ``"CMD"``), not vehicle state, and is never retained.
PUSH_TOPIC_PREFIX: Final = "Xp/Msg/Push/"

#: The account/takeover notice the broker pushes to ``$client/<clientId>``:
#: ``service_type 17`` (USER_LOGOUT_SERVICE) + ``code 4003007``
#: (ACCOUNT_KICK_OFF). It means "this account signed in on another device" -
#: in practice the local renewal app, which must log in to mint a fresh pair and
#: then imports it. The integration's job is to stay quiet and wait for that
#: import, not to treat it as an auth failure.
ACCOUNT_TAKEOVER_SERVICE: Final = 17
ACCOUNT_TAKEOVER_CODE: Final = 4003007

#: How long after our own reconnect a CONNECT refusal is read as "the broker is
#: still settling the previous session" rather than an auth problem. Measured
#: 2026-10-17: these bursts clear in 45-90 s with no action (both installs).
SETTLE_WINDOW_SECONDS: Final = 120
#: Quiet retries (same credential, no mint) before the auth-failure path may run.
SETTLE_RETRY_DELAYS: Final = (10, 20)
#: After a takeover notice, the renewal's import normally lands within seconds;
#: this grace covers a slow captcha solve, and suppresses minting meanwhile.
TAKEOVER_GRACE_SECONDS: Final = 300

#: Mint this many seconds before the server-issued expiry, so a password is never
#: used after it has lapsed while a connection attempt is being made. Live
#: measurements give a 60-90s lifetime.
CREDENTIAL_EXPIRY_MARGIN: Final = 15.0


def expires_at_from_account(account: dict | None) -> float:
    """Epoch seconds at which a minted MQTT credential stops being usable, or 0.

    ``createMqttAccount`` returns ``expireTime`` as epoch milliseconds (the app
    compares it with ``System.currentTimeMillis()``); ``expireInSec`` is the
    integration's derived "seconds from now" convenience. Zero means "unknown",
    which callers treat as expired - mint before use rather than guess.
    """
    if not isinstance(account, dict):
        return 0.0
    try:
        expire_time = float(account.get("expireTime") or 0.0)
    except (TypeError, ValueError):
        expire_time = 0.0
    if expire_time > 1e11:
        # Anything above ~year 5138 in seconds is really milliseconds.
        return expire_time / 1000.0
    try:
        expire_in = float(account.get("expireInSec") or 0.0)
    except (TypeError, ValueError):
        expire_in = 0.0
    if expire_in > 0:
        return time.time() + expire_in
    return 0.0


def is_account_takeover(notification: dict) -> bool:
    """True when a parsed push notification is the ACCOUNT_KICK_OFF notice."""
    if not isinstance(notification, dict):
        return False
    try:
        service = int(notification.get("service_type") or 0)
        code = int(notification.get("code") or 0)
    except (TypeError, ValueError):
        return False
    return service == ACCOUNT_TAKEOVER_SERVICE and code == ACCOUNT_TAKEOVER_CODE

#: Placeholders expanded in listener topics from the identity this install signs
#: as, so an identity-scoped topic can be typed instead of pasted:
#: ``Xp/Msg/Push/{device_id}`` (the device push channel) or
#: ``Mona/Msg/User/{user_id}`` (sister brand), for example.
TOPIC_PLACEHOLDERS: Final = ("device_id", "user_id")


def _device_id_from_client_id(client_id: str) -> str:
    """The device id embedded in an XPENG MQTT clientId, or "".

    Both clientId schemas put it in the fourth ``:``-delimited segment:
    ``xmart:app:<uid>:<deviceId>`` is what the API returns, and the app appends
    ``:2.0:V<versionName>`` before connecting. The broker's ACL is bound to that
    identity, so the segment is the safe source for an identity-scoped topic.
    """
    parts = (client_id or "").split(":")
    if len(parts) >= 4:
        return parts[3].strip()
    return ""


def unresolved_topic_placeholders(topic: str) -> list[str]:
    """Placeholder names still present in ``topic`` (empty when all resolved)."""
    return [name for name in TOPIC_PLACEHOLDERS if "{" + name + "}" in topic]


def substitute_topic_placeholders(
    topic: str, *, device_id: str = "", user_id: str = ""
) -> str:
    """Expand ``{device_id}`` / ``{user_id}`` in one listener topic.

    A placeholder with no value is left as typed; ``configure_listener()`` then
    drops that topic with a warning, because subscribing the literal string would
    only earn a refusal and repeat it on every reconnect.
    """
    values = {
        "device_id": (device_id or "").strip(),
        "user_id": (user_id or "").strip(),
    }
    out = topic
    for name, value in values.items():
        if value:
            out = out.replace("{" + name + "}", value)
    return out


def parse_listener_topics(
    raw: str, *, device_id: str = "", user_id: str = ""
) -> list[str]:
    """Parse newline/comma-separated MQTT topics from user input.

    ``{device_id}`` and ``{user_id}`` are expanded when a value is supplied, so an
    identity-scoped topic survives a credential refresh without being retyped.
    The 512-character cap is applied after expansion.
    """
    topics: list[str] = []
    for chunk in raw.replace(",", "\n").splitlines():
        topic = chunk.strip()
        if not topic:
            continue
        topic = substitute_topic_placeholders(
            topic, device_id=device_id, user_id=user_id
        )
        if len(topic) <= 512:
            topics.append(topic)
    return topics


class XPengMQTTClient:
    """Single persistent MQTT connection to the XPENG broker.

    Mirrors the working xpeng_mqtt_bridge.py pattern:
    - ONE client created at setup, paho loop_start() handles the network thread
    - paho handles auto-reconnect via reconnect_delay_set
    - Never create a new client on each poll
    """

    def __init__(
        self,
        client_id: str,
        password: str,
        server: str,
        user_id: str,
        device_id: str = "",
        cert_dir: str | Path | None = None,
        expires_at: float = 0.0,
    ) -> None:
        self._client_id = client_id
        self._password = password
        # When the password we hold was handed to us. A refusal is only a bug in
        # our rotation if the credential was still current at the time - which is
        # what this makes checkable, instead of guessing (measured 2026-10-17: 50
        # refusals overnight, cause unknown).
        self._password_set_at = time.time()
        # The password the CURRENT paho client was built with. The credential
        # refresh replaces `_password` every ~45s while the live client keeps
        # authenticating with this one, so the two drift apart - and a refusal
        # then means "you used a superseded password", not "the login expired".
        self._client_password = ""
        self._server = server
        self._user_id = user_id
        # The device identity the broker authenticated, used only to build
        # identity-scoped topics (PUSH_TOPIC_PREFIX). Derived from the clientId
        # when not supplied, so existing callers need no change.
        self._device_id = (device_id or "").strip() or _device_id_from_client_id(
            client_id
        )
        # Where the mTLS material lives. Falls back to the bundled certs dir
        # for callers that predate the cert_dir option.
        self._cert_dir = Path(cert_dir) if cert_dir else CERT_DIR

        self._client: mqtt.Client | None = None
        self._connected = False
        self._msg_seq = 0
        self._last_msg_ts: float = time.time()
        # Time of the last message on the PRIMARY vehicle-state topic only.
        # Distinct from _last_msg_ts, which any topic bumps (V2, listener
        # topics). The silence watchdog must use THIS one: otherwise chatter on
        # a side topic keeps the watchdog quiet while the vehicle-state fan-out
        # is stalled - the exact failure the watchdog exists to fix.
        self._last_state_msg_ts: float = time.time()
        # Time of the last state message that actually CARRIED data. A sleeping
        # car's ~248-byte stub resets `_last_state_msg_ts` but not this, so a
        # stall that only produces stubs cannot hide behind the reconnect
        # cycle. This is the clock staleness/escalation is measured on.
        self._last_meaningful_state_ts: float = time.time()
        self._state_callback: Callable[[dict[str, Any]], None] | None = None
        # Repairs one state frame before it is stored: sentinel_guard.py replaces
        # a wake frame's placeholder tyre pressures with the last real readings.
        # Injected so this module keeps no project imports of its own; None means
        # "trust every frame as it arrives".
        self._instant_sanitizer: Callable[[Any, Any], Any] | None = None
        self._auth_failure_callback: Callable[[], None] | None = None
        self._listener_callback: Callable[[str, str], None] | None = None
        # Raw-frame sink for the capture recorder. Called with bytes, before any
        # decoding, because the whole point is to keep the bytes bytes.
        self._capture_callback: Callable[[str, bytes, int, bool], None] | None = None
        self._subscription_result_callback: Callable[[str, bool, str], None] | None = None
        self._notification_callback: Callable[[dict[str, Any]], None] | None = None
        self._command_feedback_callback: Callable[[dict[str, Any]], None] | None = None
        self._last_full_content: dict[str, Any] | None = None

        # Listener state
        self._listener_enabled = False
        self._listener_topics: list[str] = []
        self._discovery_enabled = False
        self._subscription_mids: dict[int, str] = {}  # mid -> topic
        # Topics already announced at INFO, so a firehose logs one line each and no more.
        self._listener_logged_topics: set[str] = set()

        # Synchronous request/response correlation for send_command_wait().
        # Maps our outgoing msg_id -> {"event", "result", "service_type"}.
        self._pending_lock = threading.Lock()
        self._pending: dict[str, dict[str, Any]] = {}

        # Serialises the reconnect triggers (silence watchdog, auth-failure
        # callback, clientId rotation). Without this they stack and produce two
        # paho clients sharing one clientId that kick each other off the broker.
        self._reconnect_lock = threading.Lock()
        # Settling bookkeeping: a CONNECT refused while the previous session is
        # still being torn down (ours) or replaced (renewal) is retried with the
        # same credential before anything is minted. See SETTLE_RETRY_DELAYS.
        self._last_reconnect_at: float = 0.0
        self._settle_refusals: int = 0
        # When the credential in hand stops working, and the callback that mints a
        # new one on demand (the app's model: mint immediately before connecting,
        # never on a timer).
        self._expires_at: float = float(expires_at or 0.0)
        self._credentials_provider: Callable[[], dict[str, Any] | None] | None = None
        self._takeover_at: float = 0.0
        self._settle_timer: threading.Timer | None = None

    @property
    def connected(self) -> bool:
        """Return True if currently connected."""
        return self._connected

    @property
    def last_msg_ts(self) -> float:
        """Unix timestamp of the most recent incoming MQTT message (any topic)."""
        return self._last_msg_ts

    @property
    def last_state_msg_ts(self) -> float:
        """Unix timestamp of the last message on the primary vehicle-state topic.

        Use this - not ``last_msg_ts`` - for "is the car's data flowing?"
        decisions such as the silence watchdog. A side topic (V2 or a user
        listener topic) bumps ``last_msg_ts`` without delivering vehicle state,
        which would mask a stalled state fan-out.
        """
        return self._last_state_msg_ts

    @property
    def last_meaningful_state_ts(self) -> float:
        """When the last vehicle-state message actually carried data.

        Distinct from ``last_state_msg_ts``, which any state-topic message bumps.
        A sleeping car's ~248-byte stub counts for that one but not for this, so
        this is the one to measure staleness with.
        """
        return self._last_meaningful_state_ts

    @property
    def credentials_superseded(self) -> bool:
        """Whether the live client is authenticating with an old password.

        `_password` is replaced every ~45s by the credential refresh; the paho
        client keeps the value it was built with. When those differ, a broker
        refusal means the client presented a superseded password - and the fix
        is to rebuild the client with the password already in hand, not to mint
        another one. Observed 2026-10-07: eight `rc=Not authorized` refusals in
        twenty minutes, each followed by a cooldown-delayed API round trip, when
        the password needed was sitting in memory the whole time.
        """
        return bool(self._client_password) and self._password != self._client_password

    @property
    def send_topic(self) -> str:
        """MQTT topic to publish vehicle queries."""
        return f"Xmart/Send/User/{self._user_id}"

    @property
    def recv_topic(self) -> str:
        """MQTT topic to subscribe for vehicle responses."""
        return f"Xmart/Msg/User/{self._user_id}"

    @property
    def recv_topic_v2(self) -> str:
        """V2 protocol variant — empirically reachable from app-class cert
        on iot-app-global (probed 2026-06-07, SUBACK Granted QoS 1).
        Currently unparsed; messages route to listener path for observation.
        """
        return f"Xmart/MsgV2/User/{self._user_id}"

    @property
    def device_id(self) -> str:
        """The device id embedded in the authenticated clientId (or "")."""
        return self._device_id

    def set_device_id(self, device_id: str) -> None:
        """Set the device id when it could not be derived at construction.

        The account clientId normally carries it; this covers an older 3-field
        clientId, where the entry's resolved device id is the only source.
        """
        self._device_id = (device_id or "").strip()

    @property
    def push_topic(self) -> str:
        """The device-scoped push channel, or "" when the id is unknown.

        ``Xp/Msg/Push/<deviceId>`` is the topic the XPENG app subscribes on every
        connect. Vehicle state does not need it; it is exposed so a listener
        topic can be built from the same identity the broker authenticated
        (``Xp/Msg/Push/{device_id}`` in the options).
        """
        return f"{PUSH_TOPIC_PREFIX}{self._device_id}" if self._device_id else ""

    def set_state_callback(
        self, callback: Callable[[dict[str, Any]], None]
    ) -> None:
        """Set callback invoked from MQTT thread on full vehicle state."""
        self._state_callback = callback

    def set_instant_sanitizer(
        self, sanitizer: Callable[[Any, Any], Any]
    ) -> None:
        """Set the function that repairs a state frame before it is used.

        Called from the MQTT thread as ``sanitizer(instant, previous_content)``;
        it returns the same object when nothing needs repairing. See
        ``sentinel_guard.py``.
        """
        self._instant_sanitizer = sanitizer

    def set_auth_failure_callback(self, callback: Callable[[], None]) -> None:
        """Set callback invoked when MQTT broker rejects our credentials."""
        self._auth_failure_callback = callback

    def set_listener_callback(
        self, callback: Callable[[str, str], None]
    ) -> None:
        """Set callback for MQTT listener messages (topic, payload)."""
        self._listener_callback = callback

    def set_capture_callback(
        self, callback: Callable[[str, bytes, int, bool], None] | None
    ) -> None:
        """Set the raw-frame sink: (topic, payload, qos, retain).

        Called from paho's network thread for every listener message, before any
        decoding. Whatever it does must be non-blocking and must not raise; the
        capture recorder's ``submit()`` is both, deliberately.
        """
        self._capture_callback = callback

    def set_subscription_result_callback(
        self, callback: Callable[[str, bool, str], None]
    ) -> None:
        """Set callback for subscription results (topic, accepted, reason)."""
        self._subscription_result_callback = callback

    def set_notification_callback(
        self, callback: Callable[[dict[str, Any]], None]
    ) -> None:
        """Set callback for push notifications (msg_type=3)."""
        self._notification_callback = callback

    def set_command_feedback_callback(
        self, callback: Callable[[dict[str, Any]], None]
    ) -> None:
        """Set callback for command response messages (service_type != 19)."""
        self._command_feedback_callback = callback

    def configure_listener(
        self,
        enabled: bool,
        topics: list[str],
        discovery_enabled: bool,
        discovery_candidates: tuple[str, ...] = (),
        extra_topics: list[str] | None = None,
    ) -> None:
        """Configure the MQTT listener settings.

        Called before connect() to set up listener topics.
        If discovery is enabled, the discovery candidates are appended.

        ``extra_topics`` are the capture recorder's topics. They are subscribed
        whenever capture is on, independently of ``enabled`` - capture is not a
        diagnostic and does not need the listener's decode-and-fire machinery, so
        the two are configured separately and an install can have either alone.
        """
        self._listener_enabled = enabled
        self._listener_topics = []
        for topic in topics:
            expanded = substitute_topic_placeholders(
                topic, device_id=self._device_id, user_id=self._user_id
            )
            missing = unresolved_topic_placeholders(expanded)
            if missing:
                _LOGGER.warning(
                    "MQTT listener topic %r needs %s, which this install does "
                    "not know; dropping it",
                    topic,
                    ", ".join("{" + name + "}" for name in missing),
                )
                continue
            self._listener_topics.append(expanded)
        self._discovery_enabled = discovery_enabled
        if discovery_enabled:
            for candidate in discovery_candidates:
                if candidate not in self._listener_topics:
                    self._listener_topics.append(candidate)
        for topic in extra_topics or ():
            if topic not in self._listener_topics:
                self._listener_topics.append(topic)
        if enabled:
            _LOGGER.info(
                "MQTT listener configured: %d topics, discovery=%s",
                len(self._listener_topics),
                discovery_enabled,
            )
            # "#" subscribes to the entire broker, which on this one includes the car's
            # 1 Hz CAN broadcast (Xmart/Car/<vid>/State/CanPack1s) and every other
            # vehicle's traffic. It is allowed - it is the diagnostic's whole point - but
            # it is the setting that wedged a user's Home Assistant on 0.4.30, so say so
            # where the person who typed it will see it.
            if "#" in self._listener_topics:
                _LOGGER.warning(
                    "MQTT listener is subscribed to '#', which receives every message on "
                    "the broker, including the car's 1 Hz CAN stream. Diagnostic counters "
                    "and the rolling log stay exact, but expect heavy traffic; narrow the "
                    "topic list if Home Assistant feels slow."
                )

    def connect(self) -> None:
        """Create client, configure TLS, connect, start loop. Called ONCE.

        Mint-on-demand: if the server-issued credential has lapsed, a fresh one
        is minted here, immediately before the CONNECT - never on a timer. Runs
        on an executor/timer thread in every caller, so the provider may block.
        """
        if self._client is not None:
            self.disconnect()

        self._ensure_credentials()

        _LOGGER.info(
            "Creating XPENG MQTT client: server=%s user_id=%s",
            self._server,
            self._user_id,
        )

        client = mqtt.Client(
            client_id=self._client_id,
            protocol=mqtt.MQTTv311,
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            clean_session=True,
        )
        client.username_pw_set(self._client_id, self._password)
        self._client_password = self._password

        # mTLS setup — verifies the broker against ca-chain.crt.
        # build_tls_context() raises FileNotFoundError naming anything missing.
        client.tls_set_context(build_tls_context(self._cert_dir))

        # Callbacks
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.on_disconnect = self._on_disconnect
        client.on_subscribe = self._on_subscribe

        # Let paho handle reconnection
        # max_delay must stay BELOW the credential rotation interval
        # (mqtt_refresh_interval, 45s). paho retries with the password the
        # client was built with, so a retry scheduled further out than one
        # rotation always presents a retired password and is refused as
        # "Not authorized". _apply_current_credentials() re-syncs the password
        # before each attempt, which fixed the bulk of this; capping the backoff
        # closes the residual case where the retry is scheduled so far ahead
        # that a rotation lands in between (measured on the live install:
        # 16 refusals over 6h, ~9x better than the original 15-in-3h but not
        # zero). Cost: during a genuine broker outage paho reconnects every 30s
        # instead of every 60s. Each attempt is a cheap TCP connect that fails.
        client.reconnect_delay_set(min_delay=10, max_delay=30)

        # Parse host:port
        host, port_str = self._server.split(":")
        port = int(port_str)

        self._client = client
        client.connect(host, port, keepalive=60)
        client.loop_start()

        # Wait up to 10s for connection
        for _ in range(100):
            if self._connected:
                _LOGGER.info("XPENG MQTT connected successfully")
                return
            time.sleep(0.1)

        if not self._connected:
            _LOGGER.warning(
                "XPENG MQTT not connected after 10s, paho will keep retrying"
            )

    def stage_credentials(
        self,
        *,
        client_id: str = "",
        password: str = "",
        server: str = "",
        expires_at: float = 0.0,
    ) -> None:
        """Stage a minted account for the next connect, without connecting.

        ``connect()`` builds the paho client out of these fields, so a caller that
        already has a better account (a renewal, a rotation) only has to stage it
        and reconnect. Nothing else needs rebuilding when the identity has not
        moved - and a full config-entry reload, which is the alternative, tears
        every entity down and creates it again, so each renewal made the whole
        integration flap between ``unavailable`` and its values.
        """
        if client_id:
            self._client_id = client_id
            parts = client_id.split(":")
            if len(parts) >= 3:
                # The user id is the client channel: Xmart/Msg/User/<uid>.
                self._user_id = parts[2]
        if password:
            self._password = password
            self._password_set_at = time.time()
        if expires_at:
            self._expires_at = float(expires_at)
        if server:
            self._server = server

    def reconnect(self, reason: str = "", *, settle: bool = False) -> bool:
        """Tear down and rebuild the client, serialised against other callers.

        The silence watchdog, the auth-failure callback and the clientId-
        rotation path all want to reconnect. If two run at once they create
        overlapping paho clients with the same clientId and the broker evicts
        one whenever the other connects, so the session never settles. This
        takes a non-blocking lock: if a reconnect is already running, the call
        is a no-op and returns False.
        """
        if not self._reconnect_lock.acquire(blocking=False):
            _LOGGER.info(
                "MQTT reconnect already in progress — skipping (%s)",
                reason or "unspecified",
            )
            return False
        try:
            _LOGGER.info("MQTT reconnect (%s)", reason or "unspecified")
            self._cancel_settle_retry()
            if not settle:
                # Only a real trigger (watchdog, reload, credential import) opens
                # the settle window. A settle retry must not extend its own
                # excuse, or the auth-failure escalation could never resume.
                self._last_reconnect_at = time.time()
            # connect() tears down any existing client first.
            self.connect()
            return True
        finally:
            self._reconnect_lock.release()

    # ── mint on demand (the app's model) ──────────────────────────────────

    def set_credentials_provider(
        self, provider: Callable[[], dict[str, Any] | None] | None
    ) -> None:
        """Install the callback that mints a fresh credential when one is needed.

        The callback runs synchronously on whatever thread is about to connect -
        every connect path in the integration is already an executor or timer
        thread - and returns a dict with ``client_id``/``password``/``server``/
        ``expires_at`` (or None when minting failed). Minting on demand is what
        the XPENG app does: ``hasAccount()`` (uid + expireTime) gates a mint in
        ``checkConnect``, and a live session is never rotated.
        """
        self._credentials_provider = provider

    @property
    def expires_at(self) -> float:
        """Epoch seconds when the credential in hand expires (0 = unknown)."""
        return self._expires_at

    @property
    def credentials_expired(self) -> bool:
        """Whether a connection attempt needs a fresh credential first."""
        return self._expires_at <= 0.0 or (
            time.time() >= self._expires_at - CREDENTIAL_EXPIRY_MARGIN
        )

    def _ensure_credentials(self) -> None:
        """Mint before connecting when the credential in hand has lapsed."""
        provider = self._credentials_provider
        if provider is None or not self.credentials_expired:
            return
        try:
            minted = provider()
        except Exception:  # noqa: BLE001 - connecting with the old one may still work
            _LOGGER.warning(
                "On-demand MQTT credential mint failed; trying the credential in hand",
                exc_info=True,
            )
            return
        if not minted or not minted.get("password"):
            _LOGGER.warning(
                "On-demand MQTT credential mint returned nothing; trying the "
                "credential in hand"
            )
            return
        self.stage_credentials(
            client_id=str(minted.get("client_id") or minted.get("clientId") or ""),
            password=str(minted["password"]),
            server=str(minted.get("server") or minted.get("serverSsl") or ""),
            expires_at=float(minted.get("expires_at") or 0.0),
        )
        _LOGGER.debug(
            "Minted an MQTT credential on demand (expires in %.0fs)",
            self._expires_at - time.time(),
        )

    # ── settling: a refused CONNECT is not always an auth failure ──────────

    @property
    def settling(self) -> bool:
        """True while a refused CONNECT may just be the previous session ending.

        Two situations produce one: our own reconnect (watchdog, reload) and a
        takeover notice from the renewal app, which logs in to mint the new pair
        and imports it moments later. Minting cannot fix either, so the
        auth-failure path must not run yet.
        """
        now = time.time()
        return (
            now - self._last_reconnect_at <= SETTLE_WINDOW_SECONDS
            or now - self._takeover_at <= TAKEOVER_GRACE_SECONDS
        )

    def note_account_takeover(self, message: str = "") -> None:
        """Record the broker's ACCOUNT_KICK_OFF notice and stay quiet for a while.

        Called from the notification path. The renewal app logs in on this same
        account (that is how it renews), which evicts this client; it then imports
        a fresh pair. Logging in elsewhere is not an error here.
        """
        self._takeover_at = time.time()
        self._settle_refusals = 0
        _LOGGER.info(
            "Account signed in elsewhere%s - expecting a credential import; "
            "CONNECT retries stay quiet for %.0fs",
            f" ({message})" if message else "",
            TAKEOVER_GRACE_SECONDS,
        )

    def _cancel_settle_retry(self) -> None:
        timer = self._settle_timer
        self._settle_timer = None
        if timer is not None:
            timer.cancel()

    def _schedule_settle_retry(self, delay: float) -> None:
        self._cancel_settle_retry()
        timer = threading.Timer(delay, self._settle_retry)
        timer.daemon = True
        self._settle_timer = timer
        timer.start()

    def _settle_retry(self) -> None:
        """Retry the connection with the credential already staged (no mint)."""
        if self._connected or self._client is None:
            return
        self.reconnect("settling: retry after refused CONNECT", settle=True)

    def disconnect(self) -> None:
        """Disconnect and stop the network loop. Safe to call more than once.

        Order matters. paho sends the DISCONNECT packet from `disconnect()`, and
        `loop_stop()` stops the thread that would write it - so stopping the loop
        first can leave the broker holding the session. A session the broker
        still attributes to this clientId is exactly what makes a later
        reconnect look like a second subscriber under one account.

        `loop_stop()` joins the network thread, so it can block; everything
        before it here is therefore non-blocking, and the thread reference is
        dropped before the join so a hung join cannot leave this object claiming
        to own a live client.
        """
        client = self._client
        if client is None:
            return
        self._cancel_settle_retry()
        _LOGGER.info(
            "Disconnecting XPENG MQTT client (clientId=%s)", self._client_id
        )
        self._client = None
        self._connected = False
        self._subscription_mids.clear()
        # Detach callbacks first: if the thread outlives this call, it must not
        # keep feeding the coordinator, the watchdog or the auth-failure path as
        # a ghost of a client nobody owns any more.
        for attr in ("on_connect", "on_message", "on_disconnect", "on_subscribe"):
            try:
                setattr(client, attr, None)
            except Exception:  # noqa: BLE001 - teardown must never raise
                pass
        try:
            client.disconnect()
        except Exception:  # noqa: BLE001
            _LOGGER.debug("MQTT disconnect() raised; continuing teardown", exc_info=True)
        try:
            client.loop_stop()
        except Exception:  # noqa: BLE001
            _LOGGER.debug("MQTT loop_stop() raised", exc_info=True)

    def _next_msg_id(self) -> str:
        """Build the next unique outgoing message id."""
        self._msg_seq = (self._msg_seq + 1) % 1000
        ts_ms = int(time.time() * 1000)
        return f"11{self._user_id}{ts_ms}{self._msg_seq:03d}"

    def _publish(
        self,
        vin: str,
        service_type: int,
        msg_content: dict,
        msg_type: int,
        msg_id: str,
    ) -> int:
        """Build and publish a command payload. Returns the paho rc."""
        payload = json.dumps({
            "target_id": vin,
            "msg_id": msg_id,
            "protocol_ver": "2.0",
            "msg_content": msg_content,
            "service_type": service_type,
            "msg_type": msg_type,
        })

        result = self._client.publish(self.send_topic, payload, qos=1)
        _LOGGER.info(
            "Sent command: service_type=%s msg_content=%s rc=%s msg_id=%s",
            service_type, msg_content, result.rc, msg_id,
        )
        return result.rc

    def send_command(
        self, vin: str, service_type: int, msg_content: dict, msg_type: int = 2
    ) -> bool:
        """Send a command to the vehicle via MQTT (fire-and-forget)."""
        if not self._client or not self._connected:
            _LOGGER.warning("Cannot send command: not connected")
            return False
        rc = self._publish(vin, service_type, msg_content, msg_type, self._next_msg_id())
        return rc == 0

    def send_command_wait(
        self,
        vin: str,
        service_type: int,
        msg_content: dict,
        msg_type: int = 2,
        timeout: float = 10.0,
    ) -> dict[str, Any] | None:
        """Send a command/query and block until the matching response arrives.

        Correlates the reply by msg_id (exact) with a fallback to the next
        inbound message bearing the same service_type. Returns the parsed
        response dict, or None on timeout / not connected.

        MUST be called from an executor thread — it blocks on a
        threading.Event and would stall the event loop otherwise.
        """
        if not self._client or not self._connected:
            _LOGGER.warning("Cannot send command (wait): not connected")
            return None

        msg_id = self._next_msg_id()
        event = threading.Event()
        entry: dict[str, Any] = {
            "event": event,
            "result": None,
            "service_type": service_type,
        }
        # Register BEFORE publishing so a fast reply can't race ahead of us.
        with self._pending_lock:
            self._pending[msg_id] = entry
        try:
            rc = self._publish(vin, service_type, msg_content, msg_type, msg_id)
            if rc != 0:
                return None
            if event.wait(timeout):
                return entry["result"]
            _LOGGER.warning(
                "send_command_wait timed out after %ss (service_type=%s msg_id=%s)",
                timeout, service_type, msg_id,
            )
            return None
        finally:
            with self._pending_lock:
                self._pending.pop(msg_id, None)

    def _resolve_pending(self, data: dict[str, Any], stype: Any) -> None:
        """Wake any send_command_wait() waiter awaiting this inbound message."""
        # Notifications and outbound command echoes are not acknowledgements.
        if data.get("msg_type") != 1:
            return
        with self._pending_lock:
            if not self._pending:
                return
            msg_id = data.get("msg_id")
            key: str | None = None
            if msg_id and msg_id in self._pending:
                key = msg_id
            else:
                # Fallback: first waiter expecting this service_type. The car
                # does not always echo our msg_id on state responses.
                for k, ent in self._pending.items():
                    if ent["service_type"] == stype:
                        key = k
                        break
            if key is None:
                return
            entry = self._pending[key]
            if entry["event"].is_set():
                return  # Never overwrite the first response while its waiter wakes.
            entry["result"] = data
            entry["event"].set()

    def send_query(self, vin: str) -> None:
        """Publish a vehicle state query."""
        if not self._client or not self._connected:
            _LOGGER.debug("Cannot send query: not connected")
            return
        self.send_command(vin, service_type=19, msg_content={}, msg_type=4)
        _LOGGER.debug("Published state query for VIN %s", vin)

    # ── Listener topic subscription ──────────────────────────

    def _subscribe_listener_topics(self) -> None:
        """Subscribe to configured listener topics after connect."""
        if not self._listener_enabled or not self._listener_topics:
            return
        if not self._client:
            return

        _LOGGER.info(
            "Subscribing to %d listener topics", len(self._listener_topics)
        )
        for topic in self._listener_topics:
            try:
                result, mid = self._client.subscribe(topic, qos=0)
                if mid is not None:
                    self._subscription_mids[mid] = topic
                _LOGGER.info(
                    "Subscribed to listener topic: %s (mid=%s, rc=%s)",
                    topic, mid, result,
                )
            except Exception:
                _LOGGER.warning(
                    "Failed to subscribe to listener topic: %s", topic,
                    exc_info=True,
                )

    # ── paho callbacks (called from network thread) ──────────

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: Any,
        rc: Any,
        properties: Any = None,
    ) -> None:
        """Handle connection. CallbackAPIVersion.VERSION2 returns ReasonCode."""
        rc_str = str(rc)
        if rc_str == "Success" or rc_str == "0" or rc == 0:
            _LOGGER.info("XPENG MQTT connected, subscribing to %s and %s",
                         self.recv_topic, self.recv_topic_v2)
            self._connected = True
            self._last_msg_ts = time.time()
            self._settle_refusals = 0
            self._cancel_settle_retry()
            client.subscribe(self.recv_topic, qos=1)
            # V2 protocol — log payloads via the listener path so we can
            # characterise their shape before deciding how to parse them.
            client.subscribe(self.recv_topic_v2, qos=1)
            # Subscribe to listener topics after primary subscription
            self._subscribe_listener_topics()
        else:
            age = time.time() - self._password_set_at
            # Two very different problems, told apart by one number: a password
            # the rotation superseded mid-connect (our bug, and a fixable one) or
            # one that is seconds old and refused anyway (the broker/account
            # side). The 45s rotation is the line to read them against.
            self._connected = False
            # A refused CONNECT does not necessarily raise _on_disconnect, so the
            # sync there may never have run. Do it here too, before anything
            # else: whichever way the retry resolves, it should present the
            # credential we actually hold.
            self._apply_current_credentials()

            if self.settling and self._settle_refusals < len(SETTLE_RETRY_DELAYS):
                # The previous session is still being torn down (ours) or
                # replaced (renewal). Minting cannot help - measured 2026-10-17:
                # these bursts clear in 45-90 s on their own. Retry quietly with
                # the same credential instead of falling into the minting loop.
                delay = SETTLE_RETRY_DELAYS[self._settle_refusals]
                self._settle_refusals += 1
                _LOGGER.warning(
                    "XPENG MQTT connection refused: rc=%s (%s); credential "
                    "staged %.0fs ago - session still settling, retry %d/%d in %ds "
                    "without minting",
                    rc,
                    rc_str,
                    age,
                    self._settle_refusals,
                    len(SETTLE_RETRY_DELAYS),
                    delay,
                )
                self._schedule_settle_retry(delay)
                return

            if self.settling:
                # Short retries spent, but the grace window is still open (a
                # renewal import can take a captcha solve). Keep retrying quietly;
                # once the grace expires this branch stops matching and the normal
                # auth path - including escalation - resumes.
                delay = SETTLE_RETRY_DELAYS[-1] * 2
                _LOGGER.warning(
                    "XPENG MQTT connection refused: rc=%s (%s); credential "
                    "staged %.0fs ago - still settling, retrying in %ds without "
                    "minting",
                    rc,
                    rc_str,
                    age,
                    delay,
                )
                self._schedule_settle_retry(delay)
                return

            _LOGGER.error(
                "XPENG MQTT connection refused: rc=%s (rc_str=%s; credential "
                "staged %.0fs ago - %s)",
                rc,
                rc_str,
                age,
                "superseded by our own rotation"
                if age > 60
                else "current, so the broker refused a fresh credential",
            )
            # Trigger credential refresh on a refusal that is not a settling one
            if self._auth_failure_callback:
                _LOGGER.warning("Triggering credential refresh callback")
                self._auth_failure_callback()
            else:
                _LOGGER.warning("No auth_failure_callback set — auto-refresh not configured")

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: Any = None,
        rc: Any = None,
        properties: Any = None,
    ) -> None:
        """Handle disconnection. Paho will auto-reconnect."""
        self._connected = False
        self._subscription_mids.clear()
        rc_str = str(rc) if rc is not None else "None"
        if rc_str in ("Normal disconnection", "0"):
            _LOGGER.debug("XPENG MQTT disconnected normally")
        else:
            _LOGGER.warning(
                "XPENG MQTT disconnected: rc=%s (paho will auto-reconnect)",
                rc_str,
            )
        # Before paho retries, make sure it will present the password we hold
        # rather than the one the client was built with.
        self._apply_current_credentials()

    def _apply_current_credentials(self) -> None:
        """Put the current password onto the live client for paho's next attempt.

        `username_pw_set` sets what the CONNECT packet will carry, so calling it
        again on an existing client changes the credential paho presents when it
        retries. That matters because the credential refresh retires the previous
        password every ~45s while paho's retry is up to 60s away
        (`reconnect_delay_set(10, 30)`), so without this a retry presents a
        password the broker has already retired and is refused as "Not
        authorized" - 15 times in three hours on 2026-10-07, none of them a
        genuinely expired login. The first retry is ~10s out, comfortably inside
        the rotation window, so it lands on a valid credential.

        `_client_password` is updated too: it records what the client will
        present, so `credentials_superseded` stays an accurate signal rather than
        claiming a staleness that has just been corrected.
        """
        client = self._client
        if client is None or not self._password:
            return
        try:
            client.username_pw_set(self._client_id, self._password)
            self._client_password = self._password
        except Exception:
            _LOGGER.debug("Could not refresh live client credentials", exc_info=True)

    def _on_subscribe(
        self,
        client: mqtt.Client,
        userdata: Any,
        mid: Any,
        reason_codes: Any,
        properties: Any = None,
    ) -> None:
        """Handle subscription acknowledgement. Track discovery results."""
        mid_int = int(mid) if mid is not None else -1
        topic = self._subscription_mids.pop(mid_int, None)

        _LOGGER.info(
            "SUBACK mid=%s topic=%s reason_codes=%s type=%s pending=%s",
            mid, topic, reason_codes, type(reason_codes).__name__,
            list(self._subscription_mids.values()),
        )

        if topic is None:
            # This is the primary recv_topic subscription, ignore
            return

        # Check reason codes for acceptance/rejection
        # MQTTv3.1.1 SUBACK returns granted QoS (0,1,2) or 128 for failure
        accepted = True
        reason = "Granted"
        if reason_codes is not None:
            codes = reason_codes if isinstance(reason_codes, list) else [reason_codes]
            for code in codes:
                code_val = int(code) if hasattr(code, '__int__') else -1
                code_str = str(code)
                # MQTTv3.1.1: 0/1/2 = granted QoS, 128 = failure
                # MQTTv5: >= 128 = error
                if code_val >= 128:
                    accepted = False
                    reason = code_str
                    break
                elif code_val in (0, 1, 2):
                    reason = f"Granted QoS {code_val}"
                else:
                    # Check string representation for errors
                    if "error" in code_str.lower() or "not" in code_str.lower():
                        accepted = False
                        reason = code_str
                        break
                    reason = code_str

        if self._subscription_result_callback:
            try:
                self._subscription_result_callback(topic, accepted, reason)
            except Exception:
                _LOGGER.exception(
                    "Error in subscription result callback for %s", topic
                )

    def _on_message(
        self,
        client: mqtt.Client,
        userdata: Any,
        msg: mqtt.MQTTMessage,
    ) -> None:
        """Handle incoming message. Route to vehicle state or listener."""
        self._last_msg_ts = time.time()
        topic = msg.topic
        payload_bytes = msg.payload

        # Check if this is a listener message (not our primary recv_topic)
        if topic != self.recv_topic:
            self._handle_listener_message(
                topic,
                payload_bytes,
                qos=int(msg.qos or 0),
                retain=bool(getattr(msg, "retain", False)),
            )
            return

        # Primary vehicle-state topic: this is the only thing that counts as
        # "the car is delivering data" for the silence watchdog.
        self._last_state_msg_ts = time.time()

        # Primary vehicle state message handling
        try:
            payload_len = len(payload_bytes)
            data = json.loads(payload_bytes.decode("utf-8"))
            stype = data.get("service_type")
            mtype = data.get("msg_type")

            # Wake any synchronous waiter (send_command_wait) for this reply
            # before the type-specific handling below runs.
            self._resolve_pending(data, stype)

            content = data.get("msg_content", {})
            code = content.get("code") if isinstance(content, dict) else None

            # Push notifications (msg_type=3) — fire notification callback
            if mtype == 3:
                _LOGGER.info(
                    "Push notification: service_type=%s content=%s",
                    stype,
                    json.dumps(content) if isinstance(content, dict) else str(content)[:500],
                )
                self._fire_notification(data)
                return

            # Command responses (service_type != 19) — log and fire feedback callback
            if stype != 19:
                _LOGGER.info(
                    "Command response: service_type=%s msg_type=%s content=%s",
                    stype, mtype,
                    json.dumps(content) if isinstance(content, dict) else str(content)[:500],
                )
                self._fire_command_feedback(data)
                return

            if mtype != 1:
                _LOGGER.debug(
                    "Ignoring message: service_type=%s msg_type=%s", stype, mtype
                )
                return

            if code != 200:
                _LOGGER.info("Non-200 state response: code=%s msg=%s",
                    code, content.get("msg", "") if isinstance(content, dict) else "")
                return

            # A wake frame carries all four tyre pressures as 0 before the TPMS
            # sensors answer (see sentinel_guard.py). Carry the last real
            # readings forward rather than publishing four flat tyres that
            # reinflate two seconds later; a real (low) pressure is left alone.
            # Injected rather than imported: this module is loaded both as a
            # package and as a top-level module by tools/ and tests/, and has no
            # project imports of its own.
            raw_instant = content.get("instant", {})
            instant = raw_instant
            if self._instant_sanitizer is not None:
                instant = self._instant_sanitizer(raw_instant, self._last_full_content)
                if instant is not raw_instant:
                    content = {**content, "instant": instant}

            # A true full state has ext + sufficient other sections.
            # We require ext (vehicle mode/sentinel/etc.) plus at least one
            # of: charge, drive, power, odometer, door — this handles both
            # charging mode (charge populated) and driving mode (drive/power
            # populated but charge may be empty since the car isn't plugged in).
            # During charge-only partials the car sends ONLY a "charge" dict
            # with empty ext — treat those as partial, not full state.
            has_ext = bool(instant.get("ext"))
            has_vehicle_data = any(
                bool(instant.get(k))
                for k in ("charge", "drive", "power", "odometer", "door")
            )
            is_full = has_ext and has_vehicle_data
            # A first response can contain useful state without the ext block.
            # Requiring a full baseline first discarded those values forever on
            # installs that only received partial pushes. Empty sleeping-car
            # acknowledgements still cannot seed a baseline.
            has_initial_state = any(
                isinstance(instant.get(section), dict) and instant[section]
                for section in (
                    "charge", "drive", "power", "odometer", "door", "hvac",
                    "seat", "tpms", "window", "tail", "ext",
                )
            )
            initial_partial = self._last_full_content is None and has_initial_state

            if is_full or initial_partial:
                soc = instant.get("charge", {}).get("battery_soc") if instant.get("charge") else None
                ext = instant.get("ext", {})
                protect = instant.get("protectMode", {})
                _LOGGER.info(
                    "%s state: SOC=%s%% (%dB) ext=%s protect=%s keys=%s",
                    "Initial partial" if initial_partial and not is_full else "Full",
                    soc, payload_len,
                    json.dumps(ext),
                    json.dumps(protect),
                    list(instant.keys()),
                )
                # Pass the full parsed data to the coordinator callback. It may
                # refuse the state as a replay of an older snapshot (see
                # replay_guard.py); only an accepted message may become the
                # baseline for later partials and re-stamp the "data arrived"
                # clock the silence watchdog escalates on.
                vehicle_data: dict[str, Any] = {
                    "vin": data.get("target_id", ""),
                    "msg_content": content,
                    "has_fresh_energy_data": bool(
                        instant.get("charge") or instant.get("odometer")
                    ),
                }
                if self._state_callback is None or self._state_callback(vehicle_data) is not False:
                    # Store as last full content for merging with partial responses
                    self._last_full_content = content
                    # This is the point where a message stops being a ping and
                    # becomes data: stamp it, so staleness can be measured
                    # without stubs resetting the clock.
                    self._last_meaningful_state_ts = time.time()
            elif self._last_full_content is not None:
                # Partial response — merge non-empty instant sections into last
                # full state so sensors update with whatever data the car sent.
                merged = dict(self._last_full_content)
                merged_instant = dict(merged.get("instant", {}))
                for key, val in instant.items():
                    if val:
                        previous = merged_instant.get(key)
                        merged_instant[key] = (
                            {**previous, **val}
                            if isinstance(previous, dict) and isinstance(val, dict)
                            else val
                        )
                merged["instant"] = merged_instant
                if isinstance(content.get("interval"), dict):
                    merged_interval = dict(merged.get("interval", {}))
                    for key, val in content["interval"].items():
                        previous = merged_interval.get(key)
                        merged_interval[key] = (
                            {**previous, **val}
                            if isinstance(previous, dict) and isinstance(val, dict)
                            else val
                        )
                    merged["interval"] = merged_interval
                if "digital_vehicle" in content:
                    merged["digital_vehicle"] = content["digital_vehicle"]

                # Nothing new is not data. A sleeping car answers a query with a
                # short ack - 244 B on a G6, measured 2026-10-16 - whose
                # ``instant`` is empty and whose ``interval`` block repeats what
                # we already hold. Deciding that by shape is what failed before:
                # the old test only called a message a stub when it carried no
                # ``interval`` at all, so this ack was merged instead, and merging
                # re-stamps ``_last_meaningful_state_ts`` - the clock the silence
                # watchdog escalates on. The one deliberate force-upload that
                # wakes a parked car therefore never fired, and every sensor sat
                # at its last value for hours while the diagnostics reported data
                # seconds old.
                #
                # Comparing the candidate against what is already held cannot drop
                # a real change: anything different - a section the car filled in,
                # an interval field that moved, a value that went to zero - still
                # merges below.
                if merged == self._last_full_content:
                    _LOGGER.debug(
                        "Stub response (%dB) — nothing new to merge", payload_len
                    )
                    return
                _LOGGER.debug(
                    "Partial state (%dB, keys=%s) — merged with last full state",
                    payload_len,
                    list(instant.keys()),
                )
                vehicle_data = {
                    "vin": data.get("target_id", ""),
                    "msg_content": merged,
                    # Only true if THIS message's own instant carried fresh
                    # charge/odometer data — a partial that only refreshes
                    # e.g. door/window/ext must not be mistaken for a fresh
                    # battery/range reading (see mqtt_stale gate in
                    # coordinator._merge_widget_state_if_mqtt_stale).
                    "has_fresh_energy_data": bool(
                        instant.get("charge") or instant.get("odometer")
                    ),
                }
                # A partial can carry the stale snapshot's charge too, so the
                # same acceptance test applies: a refused state must not stamp
                # the clock the watchdog escalates on, or a fanout that only
                # replays an old snapshot never ages into its force-upload.
                if self._state_callback is None or self._state_callback(vehicle_data) is not False:
                    # Keep the accepted merge as the next partial's baseline.
                    # Otherwise one partial silently undoes the previous one.
                    self._last_full_content = merged
                    # This is the point where a message stops being a ping and
                    # becomes data: stamp it, so staleness can be measured
                    # without stubs resetting the clock.
                    self._last_meaningful_state_ts = time.time()
            else:
                _LOGGER.debug(
                    "Minimal response (no prior data), %d bytes — ignoring",
                    payload_len,
                )

        except Exception:
            _LOGGER.exception("Error parsing XPENG MQTT message")

    def _handle_listener_message(
        self,
        topic: str,
        payload: bytes,
        *,
        qos: int = 0,
        retain: bool = False,
    ) -> None:
        """Handle a message received on a listener topic.

        Order matters here. The capture sink is offered the frame first, as bytes
        and with no interpretation, because it is the only consumer that needs
        fidelity - everything below this point treats the payload as text.

        Then the expensive half is skipped unless something actually wants it. On
        0.4.30 the decode ran for every frame, including the car's 1 Hz binary CAN
        broadcast, and that alone was a measurable share of the load: a UTF-8
        decode with ``errors="replace"`` allocates a full replacement string per
        frame, and the ``json.loads`` after it raises per frame. A payload that
        does not begin with ``{`` cannot be the JSON a push notification arrives
        in, so for binary frames with no listener callback registered there is
        nothing below worth doing.
        """
        if self._capture_callback is not None:
            try:
                self._capture_callback(topic, payload, qos, retain)
            except Exception:  # noqa: BLE001 - capture must not drop the connection
                _LOGGER.exception("Capture callback failed for %s", topic)

        if self._listener_callback is None and payload[:16].lstrip()[:1] != b"{":
            return

        try:
            # Try to decode as UTF-8 text/JSON
            payload_str = payload.decode("utf-8", errors="replace")
        except Exception:
            payload_str = f"<binary {len(payload)} bytes>"

        # One line per message is fine for a handful of topics. Against "#" - which the
        # listener accepts, and which includes the car's 1 Hz CAN broadcast - it is a log
        # flood on its own, before any of the entity work. First message per topic is INFO
        # so a new topic is still discoverable; the rest drop to debug.
        if topic in self._listener_logged_topics:
            _LOGGER.debug(
                "Listener message on %s (%d bytes): %s",
                topic, len(payload), payload_str[:500],
            )
        else:
            self._listener_logged_topics.add(topic)
            _LOGGER.info(
                "Listener message on %s (%d bytes): %s",
                topic, len(payload), payload_str[:500],
            )

        # Check for push notification (msg_type=3)
        try:
            data = json.loads(payload_str)
            if isinstance(data, dict) and data.get("msg_type") == 3:
                self._fire_notification(data)
        except (json.JSONDecodeError, ValueError):
            pass

        if self._listener_callback:
            self._listener_callback(topic, payload_str)

    def _fire_notification(self, data: dict[str, Any]) -> None:
        """Extract push notification fields and invoke callback."""
        content = data.get("msg_content", {})
        if not isinstance(content, dict):
            content = {}
        notification = {
            "service_type": data.get("service_type"),
            "msg_id": data.get("msg_id", ""),
            "code": content.get("code"),
            "message": content.get("msg", ""),
        }
        # Include any extra fields from msg_content
        for key, val in content.items():
            if key not in ("code", "msg"):
                notification[key] = val
        _LOGGER.info("Push notification parsed: %s", notification)
        if self._notification_callback:
            self._notification_callback(notification)

    def _fire_command_feedback(self, data: dict[str, Any]) -> None:
        """Extract command response fields and invoke callback."""
        content = data.get("msg_content", {})
        if not isinstance(content, dict):
            content = {}
        feedback = {
            "service_type": data.get("service_type"),
            "msg_type": data.get("msg_type"),
            "msg_id": data.get("msg_id", ""),
            "code": content.get("code"),
            "message": content.get("msg", ""),
            "content": content,
        }
        if self._command_feedback_callback:
            self._command_feedback_callback(feedback)
