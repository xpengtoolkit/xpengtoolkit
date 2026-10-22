"""Tests for the message-centre notification path.

These lock in a contract that was verified against the live service, because the
failure mode here is nasty: a wrong request body returns a 400 whose message is
"Server unavailable. Please try again later", which reads like a transient
outage rather than "your request shape is wrong". That is exactly how the
original implementation sat broken and unnoticed.
"""
import importlib.util
import json
import pathlib
import sys
import types
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _component import component_dir, module_path  # noqa: E402

COMP = component_dir()


def _stub_cryptography() -> None:
    """Stand in for `cryptography`, which xpeng_api imports at module scope."""
    try:
        import cryptography  # noqa: F401
        return
    except ImportError:
        pass

    real_import = (
        __builtins__["__import__"] if isinstance(__builtins__, dict)
        else __builtins__.__import__
    )

    class _Any(types.ModuleType):
        def __getattr__(self, name):
            child = _Any(f"{self.__name__}.{name}")
            setattr(self, name, child)
            return child

    def fake_import(name, *args, **kwargs):
        if name == "cryptography" or name.startswith("cryptography."):
            return _Any(name)
        return real_import(name, *args, **kwargs)

    if isinstance(__builtins__, dict):
        __builtins__["__import__"] = fake_import
    else:
        __builtins__.__import__ = fake_import


_stub_cryptography()


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, module_path(filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


api = _load("xpeng_api_notif", "xpeng_api.py")


class TestRequestShape(unittest.TestCase):
    """The body must be the shape the service actually accepts."""

    def setUp(self):
        self.sent = []
        self._real = api.get_authenticated_data

        def _capture(config, tenant, tokens, path, method, body, **kw):
            self.sent.append({"path": path, "method": method, "body": body, "kw": kw})
            return []

        api.get_authenticated_data = _capture
        self.addCleanup(lambda: setattr(api, "get_authenticated_data", self._real))
        self.tokens = {"uid": "12345678"}

    def test_body_uses_param_size_uid_page(self):
        api.get_notifications(None, {"_key_pool": {}}, self.tokens)
        body = json.loads(self.sent[0]["body"])
        # The four keys the service expects. `pageNum`/`pageSize` (the original
        # body) are silently rejected with a 400 that looks like an outage.
        self.assertIn("param", body)
        self.assertIn("size", body)
        self.assertIn("uid", body)
        self.assertIn("page", body)
        self.assertNotIn("pageNum", body)
        self.assertNotIn("pageSize", body)
        self.assertEqual(body["uid"], "12345678")
        self.assertEqual(self.sent[0]["path"], "/v3/app/notification/page")

    def test_param_carries_a_non_empty_classification(self):
        api.get_notifications(None, {"_key_pool": {}}, self.tokens)
        body = json.loads(self.sent[0]["body"])
        code = body["param"].get("firstClassificationCode")
        # The key is REQUIRED even though its value is not validated: omitting
        # it yields {"code":2,"msg":"Required String parameter 'code' is not
        # present"}. An empty value is equally rejected.
        self.assertTrue(code, "param.firstClassificationCode must be non-empty")

    def test_vehicle_classification_is_the_default(self):
        # XPMOBILE-VEH is the "Vehicle Notification" list - where X-Combos,
        # sentry and charge messages land. XPMOBILE-SIL returns [] even when
        # the account has messages, which reads as "no notifications".
        api.get_notifications(None, {"_key_pool": {}}, self.tokens)
        body = json.loads(self.sent[0]["body"])
        self.assertEqual(body["param"]["firstClassificationCode"], "XPMOBILE-VEH")

    def test_classification_is_overridable(self):
        api.get_notifications(None, {"_key_pool": {}}, self.tokens,
                              classification="XPMOBILE-CHA")
        body = json.loads(self.sent[0]["body"])
        self.assertEqual(body["param"]["firstClassificationCode"], "XPMOBILE-CHA")

    def test_every_category_is_in_the_polled_set(self):
        """The poll iterates NOTIFICATION_CLASSIFICATIONS, so it must be complete.

        Only asking for the vehicle category was the original defect: a category
        that is never queried returns nothing at all, which is indistinguishable
        from a category that is empty, so charging/service/order/event messages
        would be silently dropped forever.
        """
        codes = api.NOTIFICATION_CLASSIFICATIONS
        self.assertGreater(len(codes), 1, "only one category would be polled")
        self.assertEqual(len(codes), len(set(codes)), "duplicate category codes")
        # XPMOBILE-VEH must be present - it is where X-Combo/sentry land.
        self.assertIn(api.CLASSIFICATION_VEHICLE, codes)

    def test_vehicle_classification_is_in_the_known_set(self):
        self.assertIn(api.CLASSIFICATION_VEHICLE, api.NOTIFICATION_CLASSIFICATIONS)


class TestParser(unittest.TestCase):
    """parse_notification_item flattens the real wire format."""

    # Shaped exactly like a live item.
    ITEM = {
        "id": "1558173270679261184",
        "messageId": "1558173270679261184",
        "read": False,
        "createTime": "2026-10-09 09:44:17.0000",
        "messageContent": json.dumps({
            "msgContent": {
                "title": "XCOMBO-Benachrichtigung",
                "description": "<p>Send HA message Benachrichtigung HA Test<br/></p>",
                "link": "https://example.invalid/x",
            },
            "category": "XPMOBILE-VEH",
            "msgType": "LINK",
        }),
    }

    def test_flattens_and_strips_html(self):
        out = api.parse_notification_item(self.ITEM)
        self.assertEqual(out["id"], "1558173270679261184")
        self.assertEqual(out["title"], "XCOMBO-Benachrichtigung")
        # HTML gone, <br/> turned into a newline
        self.assertNotIn("<p>", out["message"])
        self.assertNotIn("<br", out["message"])
        self.assertIn("HA Test", out["message"])
        self.assertEqual(out["msg_type"], "LINK")
        self.assertEqual(out["category"], "XPMOBILE-VEH")
        self.assertFalse(out["read"])
        self.assertEqual(out["source"], "rest")

    def test_parses_the_4_digit_fraction_timestamp(self):
        # "2026-10-09 09:44:17.0000": space separator AND a 4-digit fraction.
        # datetime.fromisoformat rejects the fraction, so a naive parse throws
        # and the timestamp would fall back to the raw string.
        out = api.parse_notification_item(self.ITEM)
        self.assertTrue(out["timestamp"].startswith("2026-10-09T09:44:17"),
                        f"unparsed timestamp: {out['timestamp']!r}")

    def test_message_id_falls_back_to_id(self):
        item = dict(self.ITEM)
        item.pop("messageId")
        self.assertEqual(api.parse_notification_item(item)["msg_id"],
                         "1558173270679261184")

    def test_survives_a_non_json_message_content(self):
        item = dict(self.ITEM, messageContent="<p>just html</p>")
        out = api.parse_notification_item(item)   # must not raise
        self.assertIn("just html", out.get("message", ""))

    def test_survives_missing_fields(self):
        out = api.parse_notification_item({})     # must not raise
        self.assertEqual(out["id"], "")




class TestNotificationIdNamespacing(unittest.TestCase):
    """Ids are per-category, so the dedupe key must be namespaced.

    Each category has its own id sequence, so the same numeric id can appear
    under Vehicle *and* Charging. Without the namespace the second is treated as
    already-seen and silently dropped - and the two are genuinely different
    messages.
    """

    def setUp(self):
        import importlib.util as _iu

        sys.path.insert(0, str(pathlib.Path(__file__).parent))
        import _component

        # coordinator.py imports Home Assistant; load just the staticmethod we
        # need by parsing the class instead of importing the module.
        src = (_component.component_dir() / "coordinator.py").read_text()
        import ast

        tree = ast.parse(src)
        self.fn = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_notification_id":
                mod = ast.Module(body=[node], type_ignores=[])
                ns: dict = {"Any": object}
                exec(compile(mod, "<id>", "exec"), ns)
                self.fn = staticmethod(ns["_notification_id"]).__func__
                break
        assert self.fn is not None, "_notification_id not found"

    def _id(self, item):
        return self.fn(item)

    def test_same_id_in_two_categories_does_not_collide(self):
        a = {"id": "123", "classification": "XPMOBILE-VEH"}
        b = {"id": "123", "classification": "XPMOBILE-CHA"}
        self.assertNotEqual(self._id(a), self._id(b),
                            "ids from different categories collided")

    def test_category_is_used_when_classification_absent(self):
        a = {"id": "9", "category": "XPMOBILE-VEH"}
        b = {"id": "9", "category": "XPMOBILE-ORD"}
        self.assertNotEqual(self._id(a), self._id(b))

    def test_falls_back_to_bare_id_when_no_category(self):
        self.assertEqual(self._id({"id": "5"}), "5")

    def test_empty_item_yields_empty_string(self):
        self.assertEqual(self._id({}), "")

    def test_stable_for_the_same_item(self):
        item = {"messageId": "77", "classification": "XPMOBILE-SER"}
        self.assertEqual(self._id(item), self._id(item))


if __name__ == "__main__":
    unittest.main(verbosity=2)
