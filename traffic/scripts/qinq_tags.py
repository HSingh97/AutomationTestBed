"""QinQ / VLAN tagging helpers for TRex stream builders (deployed with master_script)."""

from __future__ import annotations

# Senao UBR firmware uses 0x0081 for S/C-VLAN ethertype (not IEEE 0x8100).
DEFAULT_QINQ_ETHERTYPE = 0x0081


def format_vlan_label(vlan_id, svlan_id, cvlan_id) -> str:
    if svlan_id is not None and cvlan_id is not None:
        return f"DL QinQ S={svlan_id}/C={cvlan_id} (etype 0x0081), UL untagged"
    if vlan_id is not None:
        return str(vlan_id)
    return "Untagged"


def header_sizes(vlan_id, svlan_id, cvlan_id, *, ipv6: bool = False) -> tuple[int, int]:
    """Return (downlink_l2_l3_l4_bytes, uplink_l2_l3_l4_bytes) for padding.

    Base untagged: Eth(14) + IPv4(20)/IPv6(40) + UDP(8) → 42 / 62.
    QinQ: DL double-tag (+8), UL untagged (CPE side).
    """
    base = 62 if ipv6 else 42
    if svlan_id is not None and cvlan_id is not None:
        return base + 8, base
    if vlan_id is not None:
        tagged = base + 4
        return tagged, tagged
    return base, base


def apply_downlink_tags(
    l2_header,
    vlan_id,
    svlan_id,
    cvlan_id,
    *,
    dot1q_cls,
    ethertype: int = DEFAULT_QINQ_ETHERTYPE,
):
    if svlan_id is not None and cvlan_id is not None:
        if hasattr(l2_header, "type"):
            l2_header.type = int(ethertype)
        return (
            l2_header
            / dot1q_cls(vlan=int(svlan_id), type=int(ethertype))
            / dot1q_cls(vlan=int(cvlan_id))
        )
    if vlan_id is not None:
        if hasattr(l2_header, "type"):
            l2_header.type = 0x8100
        return l2_header / dot1q_cls(vlan=int(vlan_id))
    return l2_header


def apply_uplink_tags(
    l2_header,
    vlan_id,
    svlan_id,
    cvlan_id,
    *,
    dot1q_cls,
    ethertype: int = DEFAULT_QINQ_ETHERTYPE,
):
    # Asymmetric: CPE/UL stays untagged for QinQ lab path.
    if svlan_id is not None and cvlan_id is not None:
        return l2_header
    if vlan_id is not None:
        if hasattr(l2_header, "type"):
            l2_header.type = 0x8100
        return l2_header / dot1q_cls(vlan=int(vlan_id))
    return l2_header
