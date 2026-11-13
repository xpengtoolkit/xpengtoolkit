#!/usr/bin/env python3
"""Exercise the Google sign-in config flow without Home Assistant or network.

Config flows are the highest-risk part of an integration to get wrong — a bug
crashes the setup UI, and you only find out when a user reports it. This test
stubs Home Assistant and the network calls so the Google path can be walked end
to end and the entry data asserted.

Run from the repo root:

    python3 tests/test_config_flow.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import tempfile
import types
import pathlib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _component  # noqa: E402

PKG_DIR = _component.component_dir()


# ── Home Assistant stubs ──────────────────────────────────────────────────

class _ConfigFlowResult(dict):
    pass


class ConfigFlow:
    """Minimal stand-in. Records instead of driving HA."""

    context: dict = {}  # HA sets this from outside; the flow's own __init__ does not.

    def __init_subclass__(cls, **kwargs):
        kwargs.pop("domain", None)
        super().__init_subclass__(**kwargs)

    def __init__(self):
        self.hass = None  # set by the test
        self.context = {}
        self.flow_id = "test-flow"

    def async_show_form(self, *, step_id, data_schema=None, description_placeholders=None, errors=None):
        return _ConfigFlowResult(
            type="form",
            step_id=step_id,
            description_placeholders=description_placeholders,
            errors=errors or {},
            # Kept so a check can prove a form carries the fields it should.
            # The command-permission question is answered on an existing form,
            # so "is the field there, and is it optional?" is the whole design.
            data_schema=data_schema,
        )

    def async_create_entry(self, *, title, data, options=None):
        return _ConfigFlowResult(type="create_entry", title=title, data=data, options=options)

    async def async_set_unique_id(self, unique_id):
        self._unique_id = unique_id

    def _abort_if_unique_id_configured(self):
        pass

    def async_abort(self, *, reason):
        return _ConfigFlowResult(type="abort", reason=reason)


class ConfigEntry:
    def __init__(self, data=None, entry_id="entry-1", options=None):
        self.data = data or {}
        self.options = options or {}
        self.entry_id = entry_id


class OptionsFlow:
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__()

    # The options form is a real screen too: without these the flow's own
    # `async_show_form`/`async_create_entry` calls would raise AttributeError,
    # which is why no test walked it before the ABRP token was added.
    def async_show_form(self, **kwargs):
        return ConfigFlow.async_show_form(self, **kwargs)

    def async_create_entry(self, **kwargs):
        return ConfigFlow.async_create_entry(self, **kwargs)


def callback(fn):
    return fn


def _sel(*args, **kwargs):
    return object()


def _install_ha_stubs():
    ha = types.ModuleType("homeassistant")

    ce = types.ModuleType("homeassistant.config_entries")
    ce.ConfigFlow = ConfigFlow
    ce.ConfigEntry = ConfigEntry
    ce.ConfigFlowResult = _ConfigFlowResult
    ce.OptionsFlow = OptionsFlow

    core = types.ModuleType("homeassistant.core")
    core.callback = callback

    helpers = types.ModuleType("homeassistant.helpers")
    selector = types.ModuleType("homeassistant.helpers.selector")
    selector.TextSelector = _sel
    selector.TextSelectorConfig = _sel
    selector.SelectSelector = _sel
    selector.SelectSelectorConfig = _sel
    selector.SelectSelectorMode = types.SimpleNamespace(DROPDOWN="dropdown")

    sys.modules["homeassistant"] = ha
    sys.modules["homeassistant.config_entries"] = ce
    sys.modules["homeassistant.core"] = core
    sys.modules["homeassistant.helpers"] = helpers
    sys.modules["homeassistant.helpers.selector"] = selector
    helpers.selector = selector
    ha.config_entries = ce
    ha.core = core
    ha.helpers = helpers


def _install_voluptuous_stub():
    vol = types.ModuleType("voluptuous")

    class _Marker:
        def __init__(self, key, default=None):
            self.key = key
            self.default = default

    def Schema(x, *a, **k):
        return x

    vol.Schema = Schema
    vol.Required = lambda key, default=None: _Marker(key, default)
    vol.Optional = lambda key, default=None: _Marker(key, default)
    vol.All = lambda *a, **k: a[0] if a else None
    vol.Range = lambda **k: None
    vol.Coerce = lambda t: t
    # The import_tokens schema passes extra=vol.REMOVE_EXTRA (it drops unknown
    # keys rather than refusing the post, which is what lets the renewal app
    # answer a form that has grown fields since the app was written).
    vol.REMOVE_EXTRA = "remove_extra"
    vol.PREVENT_EXTRA = "prevent_extra"
    sys.modules["voluptuous"] = vol


class _AutoMeta(type):
    """Class-attribute fallback: SensorDeviceClass.BATTERY -> 'battery'."""

    def __getattr__(cls, name):
        return name.lower()


def _install_const_stub():
    """Stub custom_components.xpeng.const.

    The real const.py subclasses HA's entity-description dataclasses with
    `kw_only=True`, which needs Python 3.10+ — the test runner here is 3.9. The
    config flow only needs the constant *values*, so this stub returns the real
    string for the keys the test asserts on and a sensible value for the rest.
    """
    real = {
        "CONF_CLIENT_ID": "client_id",
        "CONF_PASSWORD": "password",
        "CONF_SERVER": "server",
        "CONF_VIN": "vin",
        "CONF_AUTH_METHOD": "auth_method",
        "AUTH_METHOD_GOOGLE": "google",
        "AUTH_METHOD_EMAIL": "email",
        "CONF_XPENG_EMAIL": "xpeng_email",
        "CONF_XPENG_PASSWORD": "xpeng_password",
        "CONF_LOGIN_TOKENS": "login_tokens",
        "CONF_TENANT_DATA": "tenant_data",
        "CONF_CONFIGURED_DEVICE_ID": "configured_device_id",
        "CONF_COMMAND_MODE": "command_mode",
        "CONF_ALLOW_WAKE": "allow_wake",
        "CONF_ABRP_TOKEN": "abrp_token",
        "DEFAULT_SERVER": "iot-app-global.xiaopeng.com:8885",
        "DEFAULT_COMMAND_MODE": "safe_plus",
    }

    def _default(name: str):
        if name.startswith("CONF_") or name in ("DOMAIN",):
            return name.lower()
        if name.startswith("AUTH_METHOD_"):
            return name.split("AUTH_METHOD_")[-1].lower()
        if name.startswith("DEFAULT_"):
            return 60
        return name.lower()

    mod = types.ModuleType("custom_components.xpeng.const")

    def _getattr(name):
        return real.get(name, _default(name))

    mod.__getattr__ = _getattr  # module-level fallback for `from .const import X`
    sys.modules["custom_components.xpeng.const"] = mod
    return mod


class _FakeConfigEntries:
    def __init__(self, entry=None):
        self._entry = entry
        self.updated = None
        self.reloaded = False

    def async_get_entry(self, entry_id):
        return self._entry

    def async_update_entry(self, entry, *, data=None, options=None, **kw):
        if data:
            entry.data.update(data)
        if options is not None:
            entry.options = dict(options)
        self.updated = data
        self.updated_options = options

    async def async_reload(self, entry_id):
        self.reloaded = True


class _FakeHass:
    def __init__(self, entry=None):
        self.config_entries = _FakeConfigEntries(entry)

    async def async_add_executor_job(self, fn, *args):
        return fn(*args)


def _load_pkg():
    pkg = types.ModuleType("custom_components")
    pkg.__path__ = []
    xpkg = types.ModuleType("custom_components.xpeng")
    xpkg.__path__ = [str(PKG_DIR)]
    sys.modules["custom_components"] = pkg
    sys.modules["custom_components.xpeng"] = xpkg

    def load(name, filename):
        spec = importlib.util.spec_from_file_location(
            f"custom_components.xpeng.{name}", PKG_DIR / filename
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"custom_components.xpeng.{name}"] = mod
        spec.loader.exec_module(mod)
        return mod

    return load


# ── the test ──────────────────────────────────────────────────────────────

def main() -> int:
    _install_ha_stubs()
    _install_voluptuous_stub()
    _install_const_stub()
    load = _load_pkg()

    const = sys.modules["custom_components.xpeng.const"]

    # Fake the two network-touching modules before config_flow's lazy imports.
    fake_api = types.ModuleType("custom_components.xpeng.xpeng_api")

    #: Every config dict handed to fetch_tenant, so the test can prove the flow
    #: signs as a device (the tenant is sealed against that id).
    tenant_calls = []

    def _fake_fetch_tenant(cfg=None):
        tenant_calls.append(cfg)
        return {"teeId": "tee", "_key_pool": {}, "version": 2}

    fake_api.fetch_tenant = _fake_fetch_tenant
    fake_api.mint_device_id = lambda: "0123456789abcdef0123456789abcdef"
    fake_api.create_mqtt_account = lambda cfg, tenant, tokens: {
        "clientId": "xmart:app:999:aaaa", "password": "pw", "serverSsl": "iot-app-global.xiaopeng.com:8885",
    }
    fake_api.login_get_state = lambda cfg, tenant, email: {"xid": "xid-1"}
    fake_api.login_send_code = lambda cfg, tenant, email, xid="": {"body_json": {"code": 200}}
    fake_api.login_validate_code = lambda cfg, tenant, email, code, xid="": {"xid": "xid-2"}
    fake_api.login_password = lambda cfg, tenant, email, password, xid="", **kw: {
        "uid": 999, "accessToken": "acc", "refreshToken": "ref",
    }
    fake_api.DEFAULT_APP_CONFIG = {}
    # config_flow asks the REST layer where the certificates live (0.4.17 made
    # a stored cert_dir untrusted). Without this the harness dies on import, so
    # none of its checks ran - which is how the command-permission step went
    # unnoticed here.
    fake_api.resolve_cert_dir = lambda path: path or "/config/custom_components/xpeng/certs"

    fake_gl = types.ModuleType("custom_components.xpeng.google_login")
    fake_gl.extract_code = lambda text: "THECODE" if "code=" in text or len(text) > 16 else ""
    fake_gl.build_auth_url = lambda: ("https://accounts.google.com/o/oauth2/v2/auth?client_id=…", "VERIFIER")
    fake_gl.exchange_code = lambda code, verifier: "ID_TOKEN"
    fake_gl.google_auth_login = lambda api, cfg, tenant, id_token: {
        "uid": 999, "accessToken": "acc", "refreshToken": "ref", "expiresIn": 7200, "refreshExpiresIn": 7776000,
    }
    fake_gl.decode_id_token_claims = lambda t: {"email": "driver@example.com", "aud": "…"}

    class _Err(Exception):
        pass
    fake_gl.GoogleLoginError = _Err

    sys.modules["custom_components.xpeng.xpeng_api"] = fake_api
    sys.modules["custom_components.xpeng.google_login"] = fake_gl

    cf = load("config_flow", "config_flow.py")

    failures = []

    def check(name, cond, detail=""):
        print(f"  [{'ok' if cond else 'FAIL'}] {name}{' — ' + detail if detail and not cond else ''}")
        if not cond:
            failures.append(name)

    def _form_fields(result):
        """{field_name: default} for a shown form, from the voluptuous stub."""
        schema = result.get("data_schema") or {}
        fields = {}
        for key in schema:
            name = getattr(key, "key", key)
            fields[name] = getattr(key, "default", None)
        return fields

    async def run():
        flow = cf.XPengConfigFlow()
        flow.hass = _FakeHass()

        # 1. entry point shows the chooser
        r = await flow.async_step_user()
        check("step_user shows form", r.get("type") == "form" and r.get("step_id") == "user")

        # 2. choose Google. The first screen asks ONLY the method: the VIN is a
        #    question for the routes that need one, and this route asks for it on
        #    its own form (see FirstScreenTests).
        r = await flow.async_step_user({const.CONF_AUTH_METHOD: const.AUTH_METHOD_GOOGLE})
        check("google step shows form with url placeholder",
              r.get("type") == "form" and r.get("step_id") == "google"
              and "accounts.google.com" in r["description_placeholders"]["url"])
        google_fields = _form_fields(r)
        check("google form asks the command-permission question",
              {"command_mode", "allow_wake"} <= set(google_fields))
        check("and it is optional, defaulting to Safe Plus",
              google_fields.get("command_mode") == "safe_plus"
              and google_fields.get("allow_wake") is True)
        check("and the VIN, which this route cannot get from anywhere else",
              "vin" in google_fields)

        # 2b. A sign-in with no VIN cannot name a vehicle, so it is refused on the
        #     form - before the OAuth exchange and the MQTT mint, not after.
        r = await flow.async_step_google({"redirect_url": "x:/cb?code=THECODE"})
        check("a google sign-in with no VIN is refused before any network work",
              r.get("type") == "form" and r.get("step_id") == "google"
              and r.get("errors", {}).get(const.CONF_VIN) == "required")

        # The renewal app's route, which is the one that must not break: it
        # POSTs to import_tokens with only the two paths and the VIN and
        # requires create_entry back. The question therefore has to be optional
        # fields on that form, not a step.
        flow2 = cf.XPengConfigFlow()
        flow2.hass = _FakeHass()
        r = await flow2.async_step_user({
            const.CONF_VIN: "L1NNSGHA1SB000002",
            const.CONF_AUTH_METHOD: const.AUTH_METHOD_IMPORT,
        })
        check("the import route reaches import_tokens",
              r.get("type") == "form" and r.get("step_id") == "import_tokens")
        import_fields = _form_fields(r)
        check("the app's form carries the question too",
              {"command_mode", "allow_wake"} <= set(import_fields))
        check("with a default, so an app that omits it still creates the entry",
              import_fields.get("command_mode") == "safe_plus"
              and import_fields.get("allow_wake") is True)

        # 2c. A pair that is already on the host is offered wherever it lives, not
        #     only in the renewal app's pair_dir. Reported 2026-10-13: an install
        #     whose pair sat in the component directory - where `--save` writes it -
        #     got the import form with both path boxes blank and no sign that a
        #     search had happened, so a pair that was on disk read as a missing one.
        real_dirs = cf._PAIR_SEARCH_DIRS
        with tempfile.TemporaryDirectory() as pair_dir, \
                tempfile.TemporaryDirectory() as wrong_dir:
            for name in ("xpeng_tokens.json", "xpeng_tenant.json"):
                (Path(pair_dir) / name).write_text("{}", encoding="utf-8")
            cf._PAIR_SEARCH_DIRS = (str(wrong_dir), pair_dir)

            flow6 = cf.XPengConfigFlow()
            flow6.hass = _FakeHass()
            r = await flow6.async_step_user()
            check("a pair in any searched directory skips the chooser",
                  r.get("type") == "form" and r.get("step_id") == "import_tokens")
            fields = _form_fields(r)
            check("and its paths are the defaults, not blank",
                  fields.get("tokens_path") == os.path.join(pair_dir, "xpeng_tokens.json")
                  and fields.get("tenant_path") == os.path.join(pair_dir, "xpeng_tenant.json"))
            check("and the form says where it found it",
                  pair_dir in r["description_placeholders"]["pair_search"])

            # Half a pair is not a pair: the tenant carries the deviceId the
            # tokens are sealed against, so one file alone can only be refused.
            os.remove(os.path.join(pair_dir, "xpeng_tenant.json"))
            flow7 = cf.XPengConfigFlow()
            flow7.hass = _FakeHass()
            r = await flow7.async_step_user()
            check("half a pair does not count as one on the host",
                  r.get("type") == "form" and r.get("step_id") == "user")
            r = await flow7.async_step_import_tokens()
            check("and the form says the search found nothing, and where it looked",
                  _form_fields(r).get("tokens_path") == ""
                  and "No token + tenant pair was found" in r["description_placeholders"]["pair_search"]
                  and pair_dir in r["description_placeholders"]["pair_search"])

        # The entry's own recorded paths win over the directories: a legacy
        # file-based entry whose pair sits somewhere unusual must be offered its
        # own files, not whichever pair happens to be in a usual directory.
        with tempfile.TemporaryDirectory() as legacy_dir:
            legacy = {
                name: os.path.join(legacy_dir, name)
                for name in ("xpeng_tokens.json", "xpeng_tenant.json")
            }
            for path in legacy.values():
                Path(path).write_text("{}", encoding="utf-8")
            entry_legacy = ConfigEntry(
                data={const.CONF_AUTH_METHOD: const.AUTH_METHOD_IMPORT},
                options={
                    const.CONF_TOKEN_FILE: legacy["xpeng_tokens.json"],
                    const.CONF_TENANT_FILE: legacy["xpeng_tenant.json"],
                },
            )
            found = cf._host_pair_paths(entry_legacy)
            check("a legacy entry's own pair paths are used first",
                  found == (legacy["xpeng_tokens.json"], legacy["xpeng_tenant.json"]))
            check("and are ignored when the files are gone",
                  cf._host_pair_paths(
                      ConfigEntry(data={}, options={
                          const.CONF_TOKEN_FILE: os.path.join(legacy_dir, "gone.json"),
                          const.CONF_TENANT_FILE: os.path.join(legacy_dir, "gone2.json"),
                      })
                  ) is None)

        cf._PAIR_SEARCH_DIRS = real_dirs

        # 3. a bad paste is rejected
        r = await flow.async_step_google({"redirect_url": "no code here"})
        check("bad paste shows invalid_code", r.get("errors", {}).get("redirect_url") == "invalid_code")

        # 4. a good paste creates the entry. The command-permission question
        #    travels as optional fields ON this form, never as a step of its own:
        #    the renewal app POSTs here and requires `create_entry` back, so a
        #    new mandatory step would strand a fresh install (the 0.4.16 bug).
        r = await flow.async_step_google({
            "redirect_url": f"com.googleusercontent.apps.x:/oauth2redirect?code=THECODE",
            const.CONF_VIN: "L1NNSGHA1SB000001",
            const.CONF_COMMAND_MODE: "safe",
            const.CONF_ALLOW_WAKE: True,
        })
        check("good paste creates entry", r.get("type") == "create_entry")
        options = r.get("options", {})
        check("mode recorded", options.get("command_mode") == "safe")
        check("securing kept", options.get("securing") == "full")
        check("access refused", options.get("access") == "disabled")
        check("charging refused", options.get("charging") == "disabled")
        check("allow_wake recorded", options.get("allow_wake") is True)
        check("an unanswered question means Safe Plus",
              cf._command_policy_from({}).mode == "safe_plus")
        check("and Safe Plus leaves unlocking off",
              cf._command_policy_from({}).allows("access") is False)
        data = r.get("data", {})
        check("auth_method recorded as google", data.get(const.CONF_AUTH_METHOD) == const.AUTH_METHOD_GOOGLE)
        check("no password stored", data.get(const.CONF_XPENG_PASSWORD) == "")
        check("email taken from the id token", data.get(const.CONF_XPENG_EMAIL) == "driver@example.com")
        check("mqtt client id stored", data.get(const.CONF_CLIENT_ID) == "xmart:app:999:aaaa")
        check("vin stored", data.get(const.CONF_VIN) == "L1NNSGHA1SB000001")
        check("tenant data stored", bool(data.get(const.CONF_TENANT_DATA)))

        # A tenant's key pool is sealed against the device id that fetched it
        # and the request is signed as that device, so the config dict handed to
        # fetch_tenant has to carry this flow's id — and the entry has to store
        # the same one. The REST layer reads it from the "ConfiguredDeviceId"
        # key (see xpeng_api._get_device_id); the entry stores it under
        # CONF_CONFIGURED_DEVICE_ID. The .88 copy predates the field and passes
        # no config at all; assert the id only when a config dict is passed, so
        # this file still runs against both copies.
        tenant_cfg = tenant_calls[0] if tenant_calls else None
        api_device_id = str((tenant_cfg or {}).get("ConfiguredDeviceId") or "")
        check("tenant fetch is signed as a device id",
              bool(tenant_calls) and (tenant_cfg is None or len(api_device_id) == 32))
        if tenant_cfg is None:
            print("  [i] fetch_tenant got no config — pre-device-id copy, "
                  "device-id checks skipped")
        else:
            check("entry stores the id it signed with",
                  r.get("options", {}).get(const.CONF_CONFIGURED_DEVICE_ID)
                  == api_device_id)

        # 5. reauth on a google entry goes to the google step
        entry = ConfigEntry(data={const.CONF_AUTH_METHOD: const.AUTH_METHOD_GOOGLE, const.CONF_VIN: "L1NNSGHA1SB000001"})
        flow2 = cf.XPengConfigFlow()
        flow2.hass = _FakeHass(entry)
        flow2.context["entry_id"] = entry.entry_id
        r = await flow2.async_step_reauth()
        check("google reauth shows google form", r.get("type") == "form" and r.get("step_id") == "google")
        r = await flow2.async_step_google({"redirect_url": f"x:/cb?code=THECODE"})
        check("google reauth updates, not creates", r.get("type") == "abort" and r.get("reason") == "reauth_successful")
        check("google reauth wrote fresh tokens", flow2.hass.config_entries.updated is not None)
        check("google reauth reloaded the entry", flow2.hass.config_entries.reloaded)

        # 5b. A reauth must APPLY the answers the form was given. Reported by a
        #     user: choosing Safe Plus during a reauth was accepted and then
        #     discarded, so the entry stayed on Full access.
        read_only = dict(
            cf._command_policy_from({const.CONF_COMMAND_MODE: "read_only"}).to_options()
        )
        entry_ro = ConfigEntry(
            data={
                const.CONF_AUTH_METHOD: const.AUTH_METHOD_GOOGLE,
                const.CONF_VIN: "L1NNSGHA1SB000003",
            },
            options=read_only,
        )
        flow4 = cf.XPengConfigFlow()
        flow4.hass = _FakeHass(entry_ro)
        flow4.context["entry_id"] = entry_ro.entry_id
        r = await flow4.async_step_reauth()
        check("reauth form pre-fills the entry's own mode",
              _form_fields(r).get("command_mode") == "read_only")
        await flow4.async_step_google({
            "redirect_url": "x:/cb?code=THECODE",
            const.CONF_COMMAND_MODE: "safe",
        })
        check("reauth applies the mode it was given",
              entry_ro.options.get("command_mode") == "safe"
              and entry_ro.options.get("access") == "disabled"
              and entry_ro.options.get("queries") == "full")

        # ...and one that says nothing about them must leave them alone
        flow5 = cf.XPengConfigFlow()
        flow5.hass = _FakeHass(entry_ro)
        flow5.context["entry_id"] = entry_ro.entry_id
        await flow5.async_step_reauth()
        before = dict(entry_ro.options)
        await flow5.async_step_google({"redirect_url": "x:/cb?code=THECODE"})
        check("a partial reauth leaves permissions untouched",
              entry_ro.options == before)

        # 6. an email entry still reauths via the email path
        flow3 = cf.XPengConfigFlow()
        entry2 = ConfigEntry(data={
            const.CONF_AUTH_METHOD: const.AUTH_METHOD_EMAIL,
            const.CONF_XPENG_EMAIL: "a@b.c", const.CONF_XPENG_PASSWORD: "pw", const.CONF_VIN: "VIN",
        })
        flow3.hass = _FakeHass(entry2)
        flow3.context["entry_id"] = entry2.entry_id
        r = await flow3.async_step_reauth()
        check("email reauth stays on email path", r.get("type") == "form" and r.get("step_id") == "reauth_confirm")

        # 7. The optional ABRP token lives on the options screen and nowhere
        #    else: it identifies one ABRP user and one vehicle, so the
        #    integration cannot mint it, and blank means "send no telemetry".
        entry_opts = ConfigEntry(data={}, options={})
        flow_opts = cf.XPengOptionsFlow()
        flow_opts.hass = _FakeHass(entry_opts)
        flow_opts.config_entry = entry_opts
        r = await flow_opts.async_step_init()
        fields = _form_fields(r)
        check("the options form offers an ABRP token",
              "abrp_token" in fields and fields["abrp_token"] == "")
        bad = await flow_opts.async_step_init({
            const.CONF_COMMAND_MODE: "full",
            const.CONF_CERT_DIR: "",
            const.CONF_ABRP_TOKEN: "not a token",
        })
        check("a token that cannot be one is refused",
              bad.get("type") == "form"
              and bad.get("errors", {}).get("abrp_token") == "invalid_abrp_token")
        good_token = "11111111-2222-3333-4444-555555555555"
        r = await flow_opts.async_step_init({
            const.CONF_COMMAND_MODE: "full",
            const.CONF_CERT_DIR: "",
            const.CONF_ABRP_TOKEN: f"  {good_token}\n",
        })
        check("a pasted token is stored trimmed",
              r.get("type") == "create_entry"
              and r.get("data", {}).get("abrp_token") == good_token)
        entry_opts.options = {"abrp_token": good_token}
        r = await flow_opts.async_step_init()
        check("and pre-fills on the next visit",
              _form_fields(r).get("abrp_token") == good_token)
        r = await flow_opts.async_step_init({
            const.CONF_COMMAND_MODE: "full",
            const.CONF_CERT_DIR: "",
            const.CONF_ABRP_TOKEN: "   ",
        })
        check("an empty token turns ABRP off",
              r.get("type") == "create_entry"
              and r.get("data", {}).get("abrp_token") == "")

    asyncio.run(run())

    print()
    if failures:
        print(f"{len(failures)} FAILED")
        return 1
    print("all checks passed")
    return 0


class FirstScreenTests(unittest.TestCase):
    """The setup screen must not ask for what it already knows, or for what the
    method it is offering has no use for.

    Two live reports, both from a screenshot:

    * 2026-10-11 - the first screen was a required VIN box, flagged red the moment
      it was submitted, above a sign-in method dropdown whose other two options
      cannot complete in Home Assistant, while the renewal app had already put a
      token pair, VIN included, in pair_dir. Skipping the step when the pair is on
      disk fixed that half.
    * the same screen still opened with a VIN box under a blurb recommending
      Google, on a build whose dropdown default is "Import tokens". The box read
      as a question with an answer: the import route reads the VIN out of the
      pair, and the sign-in routes are the only things that need it. So the first
      screen now asks the method alone and each route asks for a VIN on its own
      form - which is what these pin.
    """

    CF = (PKG_DIR / "config_flow.py").read_text(encoding="utf-8")
    RENEW = (_component.ROOT / "xpeng_renew" / "renew_server.py").read_text(
        encoding="utf-8"
    )

    def _step_body(self, name: str) -> str:
        """The source of one step, up to the next `async def`."""
        start = self.CF.index(f"    async def {name}(")
        nxt = self.CF.find("\n    async def ", start + 10)
        return self.CF[start: nxt if nxt != -1 else len(self.CF)]

    def test_the_questions_are_skipped_when_a_pair_is_already_here(self):
        self.assertIn("def _host_pair_paths(", self.CF)
        marker = "if user_input is None and _host_pair_paths() is not None:"
        self.assertIn(marker, self.CF)
        start = self.CF.index(marker)
        self.assertIn("async_step_import_tokens()", self.CF[start:start + 200])

    def test_the_first_screen_asks_only_the_sign_in_method(self):
        """A VIN box above a dropdown whose default method does not need one
        reads as required - which is how it was reported."""
        body = self._step_body("async_step_user")
        self.assertIn("CONF_AUTH_METHOD", body)
        self.assertNotIn("CONF_VIN", body)

    def test_the_renewal_apps_post_to_that_screen_is_tolerated(self):
        """It answers whichever step the flow opened on, VIN included.

        The app sends ``{"vin": ..., "auth_method": "import"}`` to `user`, which
        no longer has a VIN field. PREVENT_EXTRA would refuse the key and leave a
        fresh, app-driven install half-created - the 0.4.16 failure. So the step
        drops unknown keys instead, and this pins both halves of that contract.
        """
        body = self._step_body("async_step_user")
        self.assertIn("extra=vol.REMOVE_EXTRA", body)
        self.assertIn('{"vin": vin, "auth_method": "import"}', self.RENEW)

    def test_the_import_form_leaves_the_vin_optional(self):
        """A pasted or imported pair carries its own VIN; asking for it there
        would be a question the flow can already answer."""
        body = self._step_body("async_step_import_tokens")
        self.assertIn("vol.Optional(CONF_VIN, default=self._vin): str", body)

    def test_a_vin_is_still_demanded_where_it_is_actually_needed(self):
        """Signing in has no pair to read one from, so those routes ask."""
        self.assertIn('def _vin_fields(current: str = "") -> dict[Any, Any]:', self.CF)
        self.assertIn("vol.Required(CONF_VIN): str", self.CF)
        for step in ("async_step_google", "async_step_email_password"):
            body = self._step_body(step)
            self.assertIn("_vin_fields(self._vin)", body)
            self.assertIn('errors[CONF_VIN] = "required"', body)
            self.assertIn("_take_vin(user_input)", body)

    def test_a_known_vin_is_never_re_asked_as_required(self):
        """A reauth carries its entry's VIN, so its form pre-fills and accepts it."""
        self.assertIn("vol.Optional(CONF_VIN, default=current): str", self.CF)

    def test_the_dropdown_says_which_methods_cannot_complete(self):
        self.assertIn("usually blocked by the slider captcha", self.CF)

    def test_the_import_form_offers_the_pair_that_is_already_there(self):
        """Fallback for anyone who follows the older instructions anyway."""
        self.assertIn("default=default_tokens_path", self.CF)
        self.assertIn("default=default_tenant_path", self.CF)


class PairDiscoveryTests(unittest.TestCase):
    """A pair already on the host must be found wherever this project put it.

    Two writers, two directories: the renewal app publishes to its `pair_dir`,
    while `tools/onboard_email_login.py --save` writes beside the component it
    used. The flow only looked in the first, so on 2026-10-13 an install whose
    download had been removed and re-added - which empties the component
    directory HACS manages - was handed a form asking for a pair, with blank
    path boxes and nothing saying a search had even happened. Source pins,
    because the failure was a search that never ran.
    """

    CF = (PKG_DIR / "config_flow.py").read_text(encoding="utf-8")
    TOOL = (_component.ROOT / "tools" / "onboard_email_login.py").read_text(
        encoding="utf-8"
    )
    STRINGS = (PKG_DIR / "strings.json").read_text(encoding="utf-8")
    EN = (PKG_DIR / "translations" / "en.json").read_text(encoding="utf-8")

    def test_the_search_covers_both_directories_this_project_writes_to(self):
        start = self.CF.index("_PAIR_SEARCH_DIRS: Final = (")
        block = self.CF[start:self.CF.index("\n)", start)]
        self.assertIn("_DEFAULT_PAIR_DIR", block)
        self.assertIn("os.path.dirname(os.path.abspath(__file__))", block)

    def test_a_legacy_entry_is_asked_for_its_own_paths_first(self):
        self.assertIn("def _pair_from_entry_options(", self.CF)
        start = self.CF.index("def _pair_from_entry_options(")
        body = self.CF[start:start + 900]
        self.assertIn("CONF_TOKEN_FILE", body)
        self.assertIn("CONF_TENANT_FILE", body)
        # ...and the search falls back to the directories only after that
        search = self.CF.index("def _host_pair_paths(")
        self.assertLess(search, self.CF.index("for directory in _PAIR_SEARCH_DIRS:"))

    def test_the_form_says_where_it_looked(self):
        """Blank boxes with no explanation are what made this look like a bug."""
        self.assertIn("def _pair_search_note(", self.CF)
        self.assertIn('description_placeholders={\n                "pair_search"', self.CF)
        for name, text in (("strings.json", self.STRINGS), ("en.json", self.EN)):
            with self.subTest(name):
                self.assertIn("{pair_search}", text)

    def test_the_tool_does_not_default_credentials_into_the_hacs_copy(self):
        self.assertIn('_LIVE_COMPONENT: Final = Path("/config/custom_components/xpeng")', self.TOOL)
        self.assertIn('_DURABLE_PAIR_DIR: Final = Path("/config/xpeng-secrets")', self.TOOL)
        self.assertIn("def _save_dir(", self.TOOL)
        self.assertIn('"--out-dir"', self.TOOL)
        # the old unconditional write into the component is what has to be gone
        self.assertNotIn('(COMP / "xpeng_tokens.json").write_text', self.TOOL)
        self.assertNotIn("os.chmod(COMP /", self.TOOL)

    def test_the_vendored_copy_carries_the_same_change(self):
        """The renewal app runs the copy, so a stale one would write into its image."""
        vendor = _component.ROOT / "xpeng_renew" / "vendor" / "tools" / "onboard_email_login.py"
        if not vendor.is_file():
            self.skipTest("renewal app not in this checkout")
        self.assertIn("def _save_dir(", vendor.read_text(encoding="utf-8"))

    def test_the_docs_name_the_durable_directory(self):
        readme = (_component.ROOT / "tools" / "README.md").read_text(encoding="utf-8")
        self.assertIn("/config/xpeng-secrets", readme)
        self.assertIn("HACS", readme)



class CommandPermissionStepTests(unittest.TestCase):
    """The permission question must not become a new mandatory flow step.

    The renewal app creates the entry by POSTing to ``import_tokens`` and
    requires ``type: create_entry`` back. A step of its own would answer with a
    form instead and leave a fresh, app-driven install half-finished - which is
    the 0.4.16 failure this project already paid for once.
    """

    CF = (PKG_DIR / "config_flow.py").read_text(encoding="utf-8")
    RENEW = _component.ROOT / "xpeng_renew" / "renew_server.py"

    def test_the_question_is_optional_fields_not_a_step(self):
        self.assertNotIn("async_step_command_mode", self.CF)
        self.assertIn("def _command_policy_fields(", self.CF)
        self.assertIn("def _command_policy_from(", self.CF)

    def test_the_answers_are_applied_on_a_reauth_too(self):
        """The bug this fixes: reauth accepted the answer and discarded it."""
        self.assertIn("_submitted_option_writes(form)", self.CF)
        self.assertIn("def _submitted_option_writes(", self.CF)
        # create keeps the new-install defaults; update only writes what was sent
        self.assertIn("def _setup_option_writes(", self.CF)
        start = self.CF.index("def _submitted_option_writes(")
        body = self.CF[start:start + 700]
        self.assertIn("if CONF_COMMAND_MODE in user_input", body)

    def test_every_route_that_creates_an_entry_asks_it(self):
        # import_tokens, email_code and google - one call each, through the flow
        # method so a reauth can pre-fill the entry's own answers.
        self.assertEqual(self.CF.count("**self._policy_fields(),"), 3)
        self.assertEqual(self.CF.count("**self._capacity_field(),"), 3)

    def test_a_reauth_prefills_from_the_entry_not_the_new_install_default(self):
        """Otherwise submitting a reauth form untouched would narrow the install."""
        self.assertIn("def _policy_fields(self)", self.CF)
        self.assertIn("def _entry(self)", self.CF)
        start = self.CF.index("def _policy_fields(self)")
        body = self.CF[start:start + 300]
        self.assertIn("CommandPolicy.from_options", body)
        self.assertIn("_entry()", body)

    def test_an_unanswered_question_means_the_new_entry_default(self):
        """A new entry gets Safe Plus; an entry with no policy keeps Full access."""
        start = self.CF.index("def _command_policy_from(")
        body = self.CF[start:start + 400]
        self.assertIn("DEFAULT_COMMAND_MODE", body)
        self.assertIn("CommandPolicy.from_preset(", body)
        const = (PKG_DIR / "const.py").read_text()
        defaults = const[const.index("# Defaults"):const.index("# MQTT topics")]
        self.assertIn('DEFAULT_COMMAND_MODE: Final = "safe_plus"', defaults)

    def test_the_app_can_answer_it(self):
        """The app that creates the entry must be able to set the mode too."""
        if not self.RENEW.is_file():
            self.skipTest("renewal app not in this checkout")
        source = self.RENEW.read_text(encoding="utf-8")
        self.assertIn('"command_mode": COMMAND_MODE', source)
        config = (_component.ROOT / "xpeng_renew" / "config.yaml").read_text()
        self.assertIn("command_mode: list(read_only|safe|safe_plus|charging|full)", config)
        self.assertIn("command_mode: safe_plus", config)

    def test_the_repairs_card_asks_the_same_questions(self):
        """The documented recovery path is a form too, and it must apply them."""
        repairs = (PKG_DIR / "repairs.py").read_text(encoding="utf-8")
        self.assertIn("_command_policy_fields(", repairs)
        self.assertIn("_capacity_fields(", repairs)
        # pre-filled from the entry, and able to write the answer back
        self.assertIn("CommandPolicy.from_options(entry.options)", repairs)
        self.assertIn("_submitted_option_writes(user_input)", repairs)

    def test_the_options_flow_offers_a_matrix_for_a_custom_mode(self):
        self.assertIn("async def async_step_groups(", self.CF)
        self.assertIn("for group in ALL_GROUPS:", self.CF)
        self.assertIn("_permission_options()", self.CF)


class NotificationIntervalOptionTests(unittest.TestCase):
    """The Message Centre poll interval is the only lever on notification delay.

    Notifications are REST-only - XPENG does not push them - so a notification cannot
    arrive sooner than the interval, which used to be a bare constant.
    """

    CONST = (PKG_DIR / "const.py").read_text(encoding="utf-8")
    INIT = (PKG_DIR / "__init__.py").read_text(encoding="utf-8")
    CF = (PKG_DIR / "config_flow.py").read_text(encoding="utf-8")

    def test_the_option_exists_with_a_floor(self):
        self.assertIn('CONF_NOTIFICATION_POLL_INTERVAL: Final = "notification_poll_interval"', self.CONST)
        self.assertIn("DEFAULT_NOTIFICATION_POLL_INTERVAL: Final = 300", self.CONST)
        self.assertIn("MIN_NOTIFICATION_POLL_INTERVAL: Final = 60", self.CONST)

    def test_the_timer_uses_the_option_not_the_constant(self):
        self.assertIn("timedelta(seconds=notification_poll_interval)", self.INIT)
        self.assertNotIn("timedelta(seconds=NOTIFICATION_POLL_INTERVAL)", self.INIT)
        self.assertIn("MIN_NOTIFICATION_POLL_INTERVAL,", self.INIT)

    def test_the_form_exposes_it(self):
        self.assertIn("CONF_NOTIFICATION_POLL_INTERVAL,", self.CF)
        self.assertIn("min=60, max=3600", self.CF)


class ImportStepToleranceTests(unittest.TestCase):
    """A client's wrong assumption must not leave a fresh install half-done."""

    def test_the_import_step_drops_unknown_keys_instead_of_refusing_them(self):
        """Measured on a fresh install: 0.4.16 + renewal app 0.1.14.

        Since 0.4.13 the flow opens on `import_tokens` when a pair is already on disk,
        and the app posted `{"vin": ..., "auth_method": "import"}` to whatever step came
        back. That step's schema refused it -

            400 {"errors":{"base":["not a valid option at 'auth_method'"]}}

        - so the pair was written, the entry was not created, and the install had
        credentials on disk and no entities. `REMOVE_EXTRA` accepts and drops unknown
        keys, which is the tolerant half of the fix: the app now asks which step it
        landed on, and an install that updates the integration before the app still
        finishes.
        """
        source = pathlib.Path(
            _component.module_path("config_flow.py")
        ).read_text(encoding="utf-8")
        start = source.index('step_id="import_tokens"')
        block = source[start:source.index("errors=errors", start)]
        self.assertIn("extra=vol.REMOVE_EXTRA", block)
        # the measured failure is recorded where the tolerance is, so nobody removes it
        # as noise
        self.assertIn("not a valid option at 'auth_method'", block)


if __name__ == "__main__":
    # Run BOTH halves. main() walks the flow end to end; the TestCase classes
    # pin the shapes. There used to be a second `__main__` gate earlier in this
    # file, which exited before the classes were defined - so running the file,
    # which is the way its own docstring documents, skipped every class test.
    _rc = main()
    _suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    _result = unittest.TextTestRunner(verbosity=1).run(_suite)
    raise SystemExit(_rc or (0 if _result.wasSuccessful() else 1))
