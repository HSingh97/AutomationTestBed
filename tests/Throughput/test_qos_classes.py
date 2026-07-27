"""Unit tests for ath1qos LuCI SFC/PIR mapping (no TRex hardware)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "traffic" / "scripts"
QOS_CONF = ROOT / "config" / "qos" / "ath1qos.conf"
QOS_JSON = ROOT / "config" / "qos" / "ath1qos_profile1.json"


def _load_qos_classes():
    path = SCRIPTS / "qos_classes.py"
    spec = importlib.util.spec_from_file_location("qos_classes", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


qos_classes = _load_qos_classes()


def test_saved_ath1qos_files_exist():
    assert QOS_CONF.is_file()
    assert QOS_JSON.is_file()
    text = QOS_CONF.read_text()
    assert "Control&Signaling" in text
    assert "Voice_DelayCriticalApplication5G" in text
    assert "Bronze4G" in text


def test_gui_sfc_order_and_stream_dscps():
    """LuCI SFC List: Control, Voice, OAM, ARVR, Gold, Bronze, ALL, (none)."""
    payload = json.loads(QOS_JSON.read_text())
    assert payload["trex_stream_dscps"] == [46, 34, 28, 40, 32, 22, 0, 18]
    classes = qos_classes.select_classes()
    assert [c["dscp"] for c in classes] == payload["trex_stream_dscps"]
    assert [c["name"] for c in classes] == [
        "control",
        "voice",
        "oam",
        "arvr",
        "gold",
        "bronze",
        "best_effort",
        "unassigned",
    ]
    assert [c["priority"] for c in classes] == list(range(1, 9))
    assert [c["mir"] for c in classes] == [100, 100, 5, 10, 45, 33, 100, 2]


def test_gui_pir_binding():
    classes = {c["name"]: c for c in qos_classes.QOS_TRAFFIC_CLASSES}
    assert classes["control"]["pir_name"] == "Control&Signaling"
    assert classes["voice"]["pir_name"] == "Voice_DelayCriticalApplication5G"
    assert classes["oam"]["pir_name"] == "OAM"
    assert classes["arvr"]["pir_name"] == "ARVR5G_5GKeyEnterprise"
    assert classes["gold"]["pir_name"] == "Gold4G_FWA5G_eMBB5G"
    assert classes["bronze"]["pir_name"] == "Bronze4G"
    assert classes["best_effort"]["pir_name"] == "ALL"
    assert classes["unassigned"]["pir_name"] is None
    assert classes["unassigned"].get("pir_missing") is True


def test_eight_unique_profile_dscps():
    classes = qos_classes.select_classes()
    dscps = [c["dscp"] for c in classes]
    assert len(dscps) == 8
    assert len(set(dscps)) == 8
    assert set(dscps).issubset(set(qos_classes.all_profile_dscps()))


def test_classify_dscp_to_pir():
    assert qos_classes.classify_dscp(46)["name"] == "Control&Signaling"
    assert qos_classes.classify_dscp(34)["name"] == "Voice_DelayCriticalApplication5G"
    assert qos_classes.classify_dscp(28)["name"] == "OAM"
    assert qos_classes.classify_dscp(40)["name"] == "ARVR5G_5GKeyEnterprise"
    assert qos_classes.classify_dscp(32)["name"] == "Gold4G_FWA5G_eMBB5G"
    assert qos_classes.classify_dscp(22)["name"] == "Bronze4G"
    assert qos_classes.classify_dscp(0)["name"] == "ALL"
    assert qos_classes.classify_dscp(18)["name"] == "Voice_DelayCriticalApplication5G"


def test_mir_share_weights():
    selected = qos_classes.select_classes(equal_share=False)
    # MIR sum = 100+100+5+10+45+33+100+2 = 395
    control = next(c for c in selected if c["name"] == "control")
    oam = next(c for c in selected if c["name"] == "oam")
    assert abs(control["bw_share"] - (100 / 395)) < 1e-9
    assert abs(oam["bw_share"] - (5 / 395)) < 1e-9


def test_equal_share_override():
    selected = qos_classes.select_classes(
        names=["control", "voice", "bronze", "best_effort"],
        equal_share=True,
    )
    assert all(abs(c["bw_share"] - 0.25) < 1e-9 for c in selected)


def test_aliases_resolve_to_profile_names():
    assert qos_classes.get_class_by_name("CTL")["name"] == "control"
    assert qos_classes.get_class_by_name("background")["name"] == "bronze"
    assert qos_classes.get_class_by_name("Control&Signaling")["dscp"] == 46
    assert qos_classes.get_class_by_name("voice_alt")["name"] == "unassigned"


def test_dscp_to_tos_roundtrip():
    for dscp in qos_classes.all_profile_dscps():
        assert qos_classes.tos_to_dscp(qos_classes.dscp_to_tos(dscp)) == dscp


def test_unknown_class_raises():
    with pytest.raises(KeyError):
        qos_classes.get_class_by_name("not_a_class")
