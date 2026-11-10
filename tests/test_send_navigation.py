"""Regression test: send_to_vehicle must use the app's xpMsgPoi envelope.

The official iOS app posts:
  {"vin": ..., "msgType": "xpMsgPoi", "uid": "<uid>",
   "xpMsgPoi": {"label": ..., "coordinateType": 2,
                "latitude": ..., "longitude": ..., "name": ...}}

The cloud rejects a flat payload or numeric msgType with 11001101/11001601.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "xpeng_api", ROOT / "custom_components" / "xpeng" / "xpeng_api.py"
)
assert SPEC and SPEC.loader
api = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(api)


def test_payload_matches_app() -> None:
    captured: dict[str, str] = {}

    def fake_get_authenticated_data(config, tenant, tokens, path, method, body):
        captured.update(path=path, method=method, body=body)
        return {"messageId": 1569754822236864512}

    api.get_authenticated_data = fake_get_authenticated_data  # type: ignore[assignment]
    result = api.send_to_vehicle(
        None,
        {},
        {"uid": "10458997"},
        "L1NNSGHA3SB800783",
        "Taree",
        -31.9111159,
        152.453931,
        "Taree NSW 2430, Australia",
    )
    assert result == {"messageId": 1569754822236864512}
    assert captured["path"] == "/biz/v5/app/sendToVehicle"
    assert captured["method"] == "POST"

    body = json.loads(captured["body"])
    assert body == {
        "vin": "L1NNSGHA3SB800783",
        "msgType": "xpMsgPoi",
        "uid": "10458997",
        "xpMsgPoi": {
            "label": "Taree NSW 2430, Australia",
            "coordinateType": 2,
            "latitude": -31.9111159,
            "longitude": 152.453931,
            "name": "Taree",
        },
    }


def test_label_defaults_to_name() -> None:
    captured: dict[str, str] = {}

    def fake_get_authenticated_data(config, tenant, tokens, path, method, body):
        captured.update(body=body)
        return {"messageId": 1}

    api.get_authenticated_data = fake_get_authenticated_data  # type: ignore[assignment]
    api.send_to_vehicle(
        None, {}, {"uid": "1"}, "VIN", "Home", -33.0, 151.0, None
    )
    body = json.loads(captured["body"])
    assert body["xpMsgPoi"]["label"] == "Home"


def test_execute_xcombo_payload() -> None:
    captured: dict[str, str] = {}

    def fake_get_authenticated_data(config, tenant, tokens, path, method, body):
        captured.update(path=path, method=method, body=body)
        return {"code": 200}

    api.get_authenticated_data = fake_get_authenticated_data  # type: ignore[assignment]
    api.execute_xcombo_scene(
        None, {}, {"uid": "10458997"}, "scene-123", "L1NNSGHA3SB800783"
    )
    assert captured["path"] == "/personal-tailor/v3/scene/lifecycle"
    assert captured["method"] == "POST"
    body = json.loads(captured["body"])
    assert body["sceneId"] == "scene-123"
    assert body["vin"] == "L1NNSGHA3SB800783"
    assert body["uid"] == "10458997"
    assert body["operatorType"] == 1
    assert body["source"] == 1
    assert isinstance(body["timestamp"], float) and body["timestamp"] > 0


if __name__ == "__main__":
    test_payload_matches_app()
    test_label_defaults_to_name()
    test_execute_xcombo_payload()
    print("PASS send navigation xpMsgPoi payload")
    print("PASS remote X-Combo lifecycle payload")
