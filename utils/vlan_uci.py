"""UBR VLAN UCI helpers: BTS QinQ (double-tag) vs CPE untagged."""

from __future__ import annotations

from typing import Any

# Firmware expects numeric vlan.ath1.mode:
#   0=transparent, 1=access, 2=trunk, 3=qinq
VLAN_MODE_NUMERIC = {
    "transparent": "0",
    "untagged": "0",
    "0": "0",
    "access": "1",
    "1": "1",
    "trunk": "2",
    "2": "2",
    "qinq": "3",
    "q-in-q": "3",
    "qin-q": "3",
    "3": "3",
}


def resolve_vlan_mode_uci(mode: str | int | None, *, default: str = "0") -> str:
    """Map name/alias → UCI numeric mode string (``0``/``1``/``2``/``3``)."""
    if mode is None:
        return default
    raw = str(mode).strip().lower().replace(" ", "").replace("_", "-")
    if "qinq" in raw or raw in {"q-in-q", "qin-q"}:
        return "3"
    return VLAN_MODE_NUMERIC.get(raw, default)


def _vlan_uci(profile_tb: dict[str, Any]) -> dict[str, Any]:
    return profile_tb.get("vlan_uci", {}) or {}


def _qinq(profile_tb: dict[str, Any]) -> dict[str, Any]:
    return profile_tb.get("qinq", {}) or {}


def _mgmt(profile_tb: dict[str, Any]) -> dict[str, Any]:
    return profile_tb.get("mgmt_vlan", {}) or {}


def _iface_keys(profile_tb: dict[str, Any], role: str) -> dict[str, str]:
    uci = _vlan_uci(profile_tb)
    role_cfg = uci.get(role, {}) or {}
    radio = str(role_cfg.get("radio", uci.get("bts_radio", "ath1") if role == "bts" else "ath1"))
    prefix = f"vlan.{radio}"
    return {
        "mode": str(role_cfg.get("mode_key", f"{prefix}.mode")),
        "svlan": str(role_cfg.get("svlan_key", f"{prefix}.svlan")),
        "cvlan": str(role_cfg.get("cvlan_key", f"{prefix}.cvlan")),
        "mgmtvlan": str(role_cfg.get("mgmtvlan_key", f"{prefix}.mgmtvlan")),
    }


def build_uci_set(key: str, value: str | int) -> str:
    # Numeric mode and VLAN IDs: uci set vlan.ath1.mode=0 (no quotes needed).
    return f"uci set {key}={value}"


def build_uci_delete(key: str) -> str:
    return f"uci delete {key} 2>/dev/null || true"


def build_bts_qinq_commands(profile_tb: dict[str, Any]) -> list[str]:
    """BTS: QinQ double-tag (S-VLAN outer, C-VLAN inner) + mgmt VLAN on ath1."""
    keys = _iface_keys(profile_tb, "bts")
    qinq = _qinq(profile_tb)
    mgmt = _mgmt(profile_tb)
    svlan = int(qinq.get("svlan", 100))
    cvlan = int(qinq.get("cvlan", 101))
    mgmt_val = int(mgmt.get("uci_value", cvlan))
    mode_val = resolve_vlan_mode_uci(
        _vlan_uci(profile_tb).get("bts", {}).get("mode_value", "3"),
        default="3",
    )
    if mode_val != "3":
        mode_val = "3"

    cmds = [
        build_uci_set(keys["mode"], mode_val),
        build_uci_set(keys["svlan"], svlan),
        build_uci_set(keys["cvlan"], cvlan),
        build_uci_set(keys["mgmtvlan"], mgmt_val),
        "uci commit vlan",
        "/etc/init.d/network reload 2>/dev/null || true",
    ]
    extra = list(_vlan_uci(profile_tb).get("bts", {}).get("extra_commands") or [])
    return cmds + extra


def build_bts_transparent_mgmt_commands(profile_tb: dict[str, Any]) -> list[str]:
    """BTS: transparent mode + mgmt VLAN only (single mgmt tag)."""
    keys = _iface_keys(profile_tb, "bts")
    mgmt = _mgmt(profile_tb)
    mgmt_val = int(mgmt.get("uci_value", _qinq(profile_tb).get("cvlan", 101)))
    mode_val = resolve_vlan_mode_uci(
        _vlan_uci(profile_tb).get("bts", {}).get("mode_value", "0"),
        default="0",
    )
    if mode_val != "0":
        # This helper is transparent-only; ignore profile leftovers like qinq=3.
        mode_val = "0"
    cmds = [
        build_uci_set(keys["mode"], mode_val),
        build_uci_delete(keys["svlan"]),
        build_uci_delete(keys["cvlan"]),
        build_uci_set(keys["mgmtvlan"], mgmt_val),
        "uci commit vlan",
        "/etc/init.d/network reload 2>/dev/null || true",
    ]
    extra = list(_vlan_uci(profile_tb).get("bts", {}).get("extra_commands") or [])
    return cmds + extra


def build_bts_trunk_commands(profile_tb: dict[str, Any]) -> list[str]:
    """BTS: trunk mode (vlan.ath1.mode=2)."""
    keys = _iface_keys(profile_tb, "bts")
    mgmt = _mgmt(profile_tb)
    mgmt_val = int(mgmt.get("uci_value", _qinq(profile_tb).get("cvlan", 101)))
    cmds = [
        build_uci_set(keys["mode"], "2"),
        build_uci_set(keys["mgmtvlan"], mgmt_val),
        "uci commit vlan",
        "/etc/init.d/network reload 2>/dev/null || true",
    ]
    extra = list(_vlan_uci(profile_tb).get("bts", {}).get("extra_commands") or [])
    return cmds + extra


def build_cpe_untagged_commands(profile_tb: dict[str, Any]) -> list[str]:
    """CPE: transparent / untagged — no double tagging on RF."""
    keys = _iface_keys(profile_tb, "cpe")
    mode_val = resolve_vlan_mode_uci(
        _vlan_uci(profile_tb).get("cpe", {}).get("mode_value", "0"),
        default="0",
    )
    if mode_val != "0":
        mode_val = "0"

    cmds = [
        build_uci_set(keys["mode"], mode_val),
        build_uci_delete(keys["svlan"]),
        build_uci_delete(keys["cvlan"]),
    ]
    mgmt = _mgmt(profile_tb)
    if mgmt.get("set_on_cpe", False):
        mgmt_val = int(mgmt.get("uci_value", _qinq(profile_tb).get("cvlan", 101)))
        cmds.insert(-2, build_uci_set(keys["mgmtvlan"], mgmt_val))
    cmds.extend(
        [
            "uci commit vlan",
            "/etc/init.d/network reload 2>/dev/null || true",
        ]
    )
    extra = list(_vlan_uci(profile_tb).get("cpe", {}).get("extra_commands") or [])
    return cmds + extra


def build_cpe_mgmtvlan_only_commands(profile_tb: dict[str, Any]) -> list[str]:
    """CPE factory / quick-start: transparent + mgmt VLAN ID only (no double-tag)."""
    keys = _iface_keys(profile_tb, "cpe")
    mgmt = _mgmt(profile_tb)
    mgmt_val = int(mgmt.get("uci_value", _qinq(profile_tb).get("cvlan", 101)))
    return [
        build_uci_set(keys["mode"], "0"),
        build_uci_delete(keys["svlan"]),
        build_uci_delete(keys["cvlan"]),
        build_uci_set(keys["mgmtvlan"], mgmt_val),
        "uci commit vlan",
        "/etc/init.d/network reload 2>/dev/null || true",
    ]


def _mgmt_from_profile(profile: dict[str, Any]) -> dict[str, Any]:
    tb = profile.get("testbed", {}) or {}
    return dict(tb.get("mgmt_vlan", {}) or profile.get("mgmt_vlan", {}) or {})


def build_bts_network_ipv6_commands(profile: dict[str, Any]) -> list[str]:
    """Set management IPv6 on BTS (network.lan) after factory reset."""
    mgmt = _mgmt_from_profile(profile)
    prefix = int(mgmt.get("prefix_len", 120))
    v6 = str(mgmt.get("ipv6_bts", "")).strip()
    if not v6:
        return []
    cidr = v6 if "/" in v6 else f"{v6}/{prefix}"
    gw6 = str(mgmt.get("ipv6_gateway", "") or profile.get("ip_tests", {}).get("ipv6_gateway", "")).strip()
    cmds = [
        f"ucidyn set network.lan.ip6proto static",
        f"ucidyn set network.lan.ip6addr '{cidr}'",
    ]
    if gw6:
        cmds.append(f"ucidyn set network.lan.ip6gw '{gw6}'")
    cmds.append("ucidyn apply")
    return cmds


def build_cpe_network_ipv6_commands(profile: dict[str, Any]) -> list[str]:
    """Set LAN IPv6 on CPE (network.lan) via secondary PC hop."""
    mgmt = _mgmt_from_profile(profile)
    ip_cfg = profile.get("ip_tests", {}) or {}
    prefix = int(ip_cfg.get("ipv6_prefix_len", mgmt.get("prefix_len", 120)))
    v6 = str(
        ip_cfg.get("ipv6_address_cpe")
        or mgmt.get("ipv6_cpe")
        or (profile.get("dut", {}) or {}).get("remote_ipv6s", [""])[0]
        or ""
    ).strip()
    if not v6:
        return []
    cidr = v6 if "/" in v6 else f"{v6}/{prefix}"
    gw6 = str(
        ip_cfg.get("ipv6_gateway_cpe")
        or ip_cfg.get("ipv6_gateway")
        or mgmt.get("ipv6_gateway", "")
    ).strip()
    cmds = [
        f"ucidyn set network.lan.ip6proto static",
        f"ucidyn set network.lan.ip6addr '{cidr}'",
    ]
    if gw6:
        cmds.append(f"ucidyn set network.lan.ip6gw '{gw6}'")
    cmds.append("ucidyn apply")
    return cmds


def build_nms_syslog_commands(nms_cfg: dict[str, Any]) -> list[str]:
    """Remote syslog / NMS target (IPv6)."""
    host = str(nms_cfg.get("syslog_ipv6", "") or nms_cfg.get("host", "")).strip()
    port = str(nms_cfg.get("syslog_port", "514")).strip()
    if not host:
        return []
    cmds = [f"uci set system.@system[0].log_ip='{host}'"]
    if port:
        cmds.append(f"uci set system.@system[0].log_port='{port}'")
    cmds.extend(["uci commit system", "/etc/init.d/system reload 2>/dev/null || true"])
    return cmds


def qinq_tags_from_profile(profile_tb: dict[str, Any]) -> tuple[int | None, int | None]:
    """Return (svlan, cvlan) from profile testbed.qinq — sole source for TRex tagging."""
    qinq = _qinq(profile_tb)
    svlan = qinq.get("svlan")
    cvlan = qinq.get("cvlan")
    if svlan is None or cvlan is None:
        return None, None
    return int(svlan), int(cvlan)


def build_verify_commands(profile_tb: dict[str, Any], role: str) -> list[str]:
    keys = _iface_keys(profile_tb, role)
    return [
        f"uci get {keys['mode']} 2>/dev/null",
        f"uci get {keys['svlan']} 2>/dev/null",
        f"uci get {keys['cvlan']} 2>/dev/null",
        f"uci get {keys['mgmtvlan']} 2>/dev/null",
    ]


def expected_bts_qinq_text(profile_tb: dict[str, Any]) -> dict[str, str]:
    qinq = _qinq(profile_tb)
    mgmt = _mgmt(profile_tb)
    return {
        "mode": "3",  # firmware numeric QinQ
        "svlan": str(int(qinq.get("svlan", 100))),
        "cvlan": str(int(qinq.get("cvlan", 101))),
        "mgmtvlan": str(int(mgmt.get("uci_value", qinq.get("cvlan", 101)))),
    }


def expected_cpe_untagged_text(profile_tb: dict[str, Any]) -> dict[str, str]:
    return {"mode": "0"}  # transparent


def mgmt_access_vlan_plan(profile_tb: dict[str, Any]) -> dict[str, Any]:
    """
    Tagging for DUT management access on the operating lab-PC interface.

    When ``mgmt_vlan.uci_value`` / ``lab_pc_vlan_id`` is set, management traffic
    is VLAN-tagged — create ``<iface>.<vid>`` even if ``lab_pc_tagging`` is
    untagged for the transparent data path.
    """
    mgmt = _mgmt(profile_tb)
    vid = int(mgmt.get("lab_pc_vlan_id") or mgmt.get("uci_value") or 0)
    if vid <= 0:
        return {"mode": "untagged", "vlan_id": 0, "untagged": True}
    return {"mode": "single", "vlan_id": vid, "untagged": False}


def lab_pc_vlan_plan(profile_tb: dict[str, Any], *, side: str) -> dict[str, Any]:
    """
    Lab switch port tagging toward devices (data-path / capture).
    BTS side: QinQ stacked subinterfaces (svlan then cvlan).
    CPE side: untagged (native) — no VLAN subinterface.

    For management IPv6 access when mgmtvlan is configured, use
    :func:`mgmt_access_vlan_plan` instead.
    """
    qinq = _qinq(profile_tb)
    mgmt = _mgmt(profile_tb)
    svlan = int(qinq.get("svlan", 100))
    cvlan = int(qinq.get("cvlan", 101))
    pc_tag = profile_tb.get("lab_pc_tagging", {}) or {}
    bts_pc = pc_tag.get("bts", {}) or {}
    cpe_pc = pc_tag.get("cpe", {}) or {}

    if side == "bts":
        mode = str(bts_pc.get("mode", "qinq")).lower()
        if mode in ("untagged", "native") or bts_pc.get("untagged"):
            return {
                "mode": "untagged",
                "vlan_id": 0,
                "untagged": True,
            }
        if mode == "single":
            return {
                "mode": "single",
                "vlan_id": int(bts_pc.get("vlan_id", mgmt.get("lab_pc_vlan_id", cvlan))),
                "untagged": False,
            }
        return {
            "mode": mode,
            "svlan": int(bts_pc.get("svlan", svlan)),
            "cvlan": int(bts_pc.get("cvlan", cvlan)),
            "untagged": False,
        }
    return {
        "mode": str(cpe_pc.get("mode", "untagged")),
        "untagged": True,
        "vlan_id": int(cpe_pc.get("vlan_id", mgmt.get("lab_pc_vlan_id", 0))),
    }
