"""
QoS traffic-class definitions mapped to DUT ath1qos (LuCI QOS SFC / PIR tables).

Authoritative sources:
  config/qos/ath1qos.conf            — /etc/config/ath1qos export
  config/qos/ath1qos_profile1.json   — SFC↔PIR↔DSCP used by TRex
  LuCI Wireless → QOS → SFC List / PIR List

PIR list (UCI @pirlist order, confirmed via `uci show ath1qos`):
  [0] ALL                              dscp 0
  [1] Control&Signaling                dscp 46,20
  [2] Voice_DelayCriticalApplication5G dscp 34,18,42,44,16
  [3] OAM                              dscp 28,4
  [4] Gold4G_FWA5G_eMBB5G              dscp 32,8,30,6,10
  [5] ARVR5G_5GKeyEnterprise           dscp 40,14,12
  [6] Bronze4G                         dscp 22,24,26

LuCI SFC List (DL) — this is the active Profile1 priority map:
  Prio MIR%  PIR
  1    100   Control&Signaling
  2    100   Voice_DelayCriticalApplication5G
  3      5   OAM
  4     10   ARVR5G_5GKeyEnterprise
  5     45   Gold4G_FWA5G_eMBB5G
  6     33   Bronze4G
  7    100   ALL
  8      2   (none)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

def _resolve_ath1qos_conf() -> Path:
    """Locate saved ath1qos.conf from repo layout or beside this script."""
    here = Path(__file__).resolve()
    candidates = [
        here.parents[2] / "config" / "qos" / "ath1qos.conf" if len(here.parents) > 2 else None,
        here.parent / "ath1qos.conf",
        Path("/home/desktop-026/UBR Automation/AutomationTestBed/config/qos/ath1qos.conf"),
    ]
    for path in candidates:
        if path is not None and path.is_file():
            return path
    return here.parent / "ath1qos.conf"


ATH1QOS_CONF_PATH = _resolve_ath1qos_conf()
ACTIVE_QOS_PROFILE = "Profile1"

# PIR table as on the DUT (index == UCI @pirlist order).
ATH1QOS_PIR_TABLE: list[dict[str, Any]] = [
    {"pirindex": 0, "name": "ALL", "dscps": [0], "qostyp": 0, "status": 1},
    {"pirindex": 1, "name": "Control&Signaling", "dscps": [46, 20], "qostyp": 7, "status": 1},
    {
        "pirindex": 2,
        "name": "Voice_DelayCriticalApplication5G",
        "dscps": [34, 18, 42, 44, 16],
        "qostyp": 7,
        "status": 1,
    },
    {"pirindex": 3, "name": "OAM", "dscps": [28, 4], "qostyp": 7, "status": 1},
    {
        "pirindex": 4,
        "name": "Gold4G_FWA5G_eMBB5G",
        "dscps": [32, 8, 30, 6, 10],
        "qostyp": 7,
        "status": 1,
    },
    {
        "pirindex": 5,
        "name": "ARVR5G_5GKeyEnterprise",
        "dscps": [40, 14, 12],
        "qostyp": 7,
        "status": 1,
    },
    {"pirindex": 6, "name": "Bronze4G", "dscps": [22, 24, 26], "qostyp": 7, "status": 1},
]

# Active SFC List from LuCI (DL). UL uses the same priority / PIR / MIR.
ATH1QOS_PROFILE1_SFCS: list[dict[str, Any]] = [
    {"sfc": "SFC_1", "priority": 1, "pirindex": 1, "mir": 100, "pir_name": "Control&Signaling"},
    {"sfc": "SFC_2", "priority": 2, "pirindex": 2, "mir": 100, "pir_name": "Voice_DelayCriticalApplication5G"},
    {"sfc": "SFC_3", "priority": 3, "pirindex": 3, "mir": 5, "pir_name": "OAM"},
    {"sfc": "SFC_4", "priority": 4, "pirindex": 5, "mir": 10, "pir_name": "ARVR5G_5GKeyEnterprise"},
    {"sfc": "SFC_5", "priority": 5, "pirindex": 4, "mir": 45, "pir_name": "Gold4G_FWA5G_eMBB5G"},
    {"sfc": "SFC_6", "priority": 6, "pirindex": 6, "mir": 33, "pir_name": "Bronze4G"},
    {"sfc": "SFC_7", "priority": 7, "pirindex": 0, "mir": 100, "pir_name": "ALL"},
    {"sfc": "SFC_8", "priority": 8, "pirindex": None, "mir": 2, "pir_name": None},
]


def dscp_to_tos(dscp: int) -> int:
    """Convert 6-bit DSCP (0–63) to the 8-bit IPv4 TOS/Traffic Class field."""
    if not 0 <= int(dscp) <= 63:
        raise ValueError(f"DSCP must be 0–63, got {dscp}")
    return int(dscp) << 2


def tos_to_dscp(tos: int) -> int:
    """Extract DSCP from an IPv4 TOS / IPv6 Traffic Class byte."""
    return (int(tos) >> 2) & 0x3F


def pir_by_index(pirindex: int) -> dict[str, Any] | None:
    for pir in ATH1QOS_PIR_TABLE:
        if int(pir["pirindex"]) == int(pirindex):
            return pir
    return None


def classify_dscp(dscp: int) -> dict[str, Any] | None:
    """
    Return the PIR that claims this DSCP (match non-ALL PIRs first).

    ALL (pirindex 0, dscp 0) is only returned for DSCP 0.
    """
    value = int(dscp)
    for pir in ATH1QOS_PIR_TABLE:
        if int(pir["pirindex"]) == 0:
            continue
        if value in pir["dscps"]:
            return pir
    if value == 0:
        return ATH1QOS_PIR_TABLE[0]
    return None


def _slug(name: str) -> str:
    return (
        name.lower()
        .replace("&", "_")
        .replace(" ", "_")
        .replace("-", "_")
    )


# Eight TRex streams = LuCI SFC priorities 1..8.
# Primary stream DSCP = first tag of the bound PIR (profile tags only).
# SFC_8 has no PIR in the GUI ("-"); stream uses Voice DSCP 18 so we still
# emit eight distinct profile DSCPs under MIR 2%.
QOS_TRAFFIC_CLASSES: list[dict[str, Any]] = [
    {
        "name": "control",
        "label": "CTL",
        "dscp": 46,
        "dscp_set": [46, 20],
        "pir_name": "Control&Signaling",
        "pirindex": 1,
        "sfc": "SFC_1",
        "priority": 1,
        "mir": 100,
        "bw_share": 100,
        "sport": 5060,
        "dport": 5060,
        "l4": "udp",
        "description": "SFC prio1 Control&Signaling (DSCP 46, MIR 100%)",
        "case_tags": ["control", "signaling", "voice", "hierarchy", "dscp", "critical"],
    },
    {
        "name": "voice",
        "label": "VO",
        "dscp": 34,
        "dscp_set": [34, 18, 42, 44, 16],
        "pir_name": "Voice_DelayCriticalApplication5G",
        "pirindex": 2,
        "sfc": "SFC_2",
        "priority": 2,
        "mir": 100,
        "bw_share": 100,
        "sport": 5004,
        "dport": 5004,
        "l4": "udp",
        "description": "SFC prio2 Voice delay-critical (DSCP 34, MIR 100%)",
        "case_tags": ["voice", "realtime", "hierarchy", "congestion", "latency", "dscp"],
    },
    {
        "name": "oam",
        "label": "OAM",
        "dscp": 28,
        "dscp_set": [28, 4],
        "pir_name": "OAM",
        "pirindex": 3,
        "sfc": "SFC_3",
        "priority": 3,
        "mir": 5,
        "bw_share": 5,
        "sport": 161,
        "dport": 161,
        "l4": "udp",
        "description": "SFC prio3 OAM (DSCP 28, MIR 5%)",
        "case_tags": ["oam", "critical", "hierarchy", "rate_limit", "dscp"],
    },
    {
        "name": "arvr",
        "label": "AR",
        "dscp": 40,
        "dscp_set": [40, 14, 12],
        "pir_name": "ARVR5G_5GKeyEnterprise",
        "pirindex": 5,
        "sfc": "SFC_4",
        "priority": 4,
        "mir": 10,
        "bw_share": 10,
        "sport": 5000,
        "dport": 5000,
        "l4": "udp",
        "description": "SFC prio4 AR/VR key enterprise (DSCP 40, MIR 10%)",
        "case_tags": ["ar_vr", "video", "enterprise", "hierarchy", "rate_limit", "dscp"],
    },
    {
        "name": "gold",
        "label": "GLD",
        "dscp": 32,
        "dscp_set": [32, 8, 30, 6, 10],
        "pir_name": "Gold4G_FWA5G_eMBB5G",
        "pirindex": 4,
        "sfc": "SFC_5",
        "priority": 5,
        "mir": 45,
        "bw_share": 45,
        "sport": 8080,
        "dport": 8080,
        "l4": "udp",
        "description": "SFC prio5 Gold/FWA/eMBB (DSCP 32, MIR 45%)",
        "case_tags": ["data", "gold", "hierarchy", "dscp", "http"],
    },
    {
        "name": "bronze",
        "label": "BRZ",
        "dscp": 22,
        "dscp_set": [22, 24, 26],
        "pir_name": "Bronze4G",
        "pirindex": 6,
        "sfc": "SFC_6",
        "priority": 6,
        "mir": 33,
        "bw_share": 33,
        "sport": 6881,
        "dport": 6881,
        "l4": "udp",
        "description": "SFC prio6 Bronze bulk (DSCP 22, MIR 33%)",
        "case_tags": ["backup", "p2p", "bronze", "rate_limit", "cap", "bulk", "dscp"],
    },
    {
        "name": "best_effort",
        "label": "BE",
        "dscp": 0,
        "dscp_set": [0],
        "pir_name": "ALL",
        "pirindex": 0,
        "sfc": "SFC_7",
        "priority": 7,
        "mir": 100,
        "bw_share": 100,
        "sport": 1234,
        "dport": 1234,
        "l4": "udp",
        "description": "SFC prio7 ALL / best-effort (DSCP 0, MIR 100%)",
        "case_tags": ["data", "baseline", "hierarchy", "voice_and_data", "dscp"],
    },
    {
        "name": "unassigned",
        "label": "NA",
        "dscp": 18,
        "dscp_set": [34, 18, 42, 44, 16],
        "pir_name": None,
        "pirindex": None,
        "sfc": "SFC_8",
        "priority": 8,
        "mir": 2,
        "bw_share": 2,
        "sport": 5006,
        "dport": 5006,
        "l4": "udp",
        "description": (
            "SFC prio8 has no PIR in LuCI ('-'); stream uses Voice DSCP 18 "
            "(still a profile tag) at MIR 2%"
        ),
        "case_tags": ["dscp", "unassigned", "hierarchy", "voice"],
        "pir_missing": True,
    },
]

_NAME_ALIASES: dict[str, str] = {
    "network_control": "control",
    "nc": "control",
    "ctl": "control",
    "vo": "voice",
    "video": "arvr",
    "vi": "arvr",
    "gaming": "gold",
    "gm": "gold",
    "multimedia": "arvr",
    "mm": "arvr",
    "low_latency_data": "gold",
    "ld": "gold",
    "background": "bronze",
    "bk": "bronze",
    "brz": "bronze",
    "gld": "gold",
    "ar": "arvr",
    "voice_alt": "unassigned",
    "vo2": "unassigned",
    "na": "unassigned",
    "all": "best_effort",
    "be": "best_effort",
}


def get_class_by_name(name: str) -> dict[str, Any]:
    key = name.strip().lower()
    key = _NAME_ALIASES.get(key, key)
    key_slug = _slug(key)
    for cls in QOS_TRAFFIC_CLASSES:
        if cls["name"] == key or cls["label"].lower() == key:
            return cls
        pir_name = cls.get("pir_name")
        if pir_name and _slug(str(pir_name)) == key_slug:
            return cls
    valid = sorted({c["name"] for c in QOS_TRAFFIC_CLASSES} | set(_NAME_ALIASES))
    raise KeyError(f"Unknown QoS class '{name}'. Valid: {valid}")


def all_profile_dscps() -> list[int]:
    """Sorted unique DSCP tags present in the saved ath1qos PIR lists."""
    values: set[int] = set()
    for pir in ATH1QOS_PIR_TABLE:
        values.update(int(v) for v in pir["dscps"])
    return sorted(values)


def select_classes(
    names: list[str] | None = None,
    *,
    tags: list[str] | None = None,
    equal_share: bool = False,
    use_mir_share: bool = True,
) -> list[dict[str, Any]]:
    """
    Return a copy of selected classes.

    Bandwidth shares default to LuCI SFC MIR weights unless equal_share=True.
    """
    if names:
        selected = [dict(get_class_by_name(n)) for n in names]
    elif tags:
        tag_set = {t.strip().lower() for t in tags if t.strip()}
        selected = [
            dict(cls)
            for cls in QOS_TRAFFIC_CLASSES
            if tag_set.intersection({t.lower() for t in cls.get("case_tags", [])})
        ]
        if not selected:
            raise ValueError(f"No QoS classes matched tags={sorted(tag_set)}")
    else:
        selected = [dict(cls) for cls in QOS_TRAFFIC_CLASSES]

    if equal_share and selected:
        share = 1.0 / len(selected)
        for cls in selected:
            cls["bw_share"] = share
    else:
        if use_mir_share:
            for cls in selected:
                cls["bw_share"] = float(cls.get("mir", cls.get("bw_share", 1)))
        total = sum(float(cls["bw_share"]) for cls in selected) or 1.0
        for cls in selected:
            cls["bw_share"] = float(cls["bw_share"]) / total

    for index, cls in enumerate(selected):
        cls["tos"] = dscp_to_tos(int(cls["dscp"]))
        cls["pg_id_dl"] = 100 + index
        cls["pg_id_ul"] = 200 + index
        cls["profile"] = ACTIVE_QOS_PROFILE
    return selected


def format_class_table(classes: list[dict[str, Any]] | None = None) -> str:
    rows = classes if classes is not None else select_classes()
    lines = [
        f"Profile: {ACTIVE_QOS_PROFILE}  (LuCI SFC List / {ATH1QOS_CONF_PATH.name})",
        f"{'Name':<12} {'Lbl':<4} {'DSCP':>4} {'Prio':>4} {'MIR':>4} "
        f"{'PIR':<34} {'Share%':>7}  Description",
        "-" * 110,
    ]
    for cls in rows:
        share_pct = float(cls["bw_share"]) * 100.0
        pir = str(cls.get("pir_name") or "-")[:34]
        lines.append(
            f"{cls['name']:<12} {cls['label']:<4} {cls['dscp']:>4} "
            f"{cls.get('priority', '-'):>4} {cls.get('mir', '-'):>4} "
            f"{pir:<34} {share_pct:>6.1f}%  {cls['description']}"
        )
    return "\n".join(lines)
