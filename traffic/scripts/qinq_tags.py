"""QinQ / VLAN tagging helpers for TRex stream builders (deployed with master_script)."""

from __future__ import annotations


def format_vlan_label(vlan_id, svlan_id, cvlan_id) -> str:
    if svlan_id is not None and cvlan_id is not None:
        return f"DL QinQ S={svlan_id}/C={cvlan_id}, UL untagged"
    if vlan_id is not None:
        return str(vlan_id)
    return "Untagged"


def header_sizes(vlan_id, svlan_id, cvlan_id) -> tuple[int, int]:
    """Return (downlink_l2_l3_l4_bytes, uplink_l2_l3_l4_bytes) for padding."""
    if svlan_id is not None and cvlan_id is not None:
        return 42 + 8, 42
    if vlan_id is not None:
        tagged = 42 + 4
        return tagged, tagged
    return 42, 42


def apply_downlink_tags(l2_header, vlan_id, svlan_id, cvlan_id, *, dot1q_cls):
    if svlan_id is not None and cvlan_id is not None:
        return l2_header / dot1q_cls(vlan=svlan_id) / dot1q_cls(vlan=cvlan_id)
    if vlan_id is not None:
        return l2_header / dot1q_cls(vlan=vlan_id)
    return l2_header


def apply_uplink_tags(l2_header, vlan_id, svlan_id, cvlan_id, *, dot1q_cls):
    if svlan_id is not None and cvlan_id is not None:
        return l2_header
    if vlan_id is not None:
        return l2_header / dot1q_cls(vlan=vlan_id)
    return l2_header
