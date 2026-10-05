"""DFS lab helpers: channel/CAC/NOL via SSH and ``radartool -i wifi1 bangradar``."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from traffic.dut_radio_config import parse_running_htmode
from traffic.vlan_lab import _ssh_cmd, lab_hosts
from utils.parsers import parse_iwconfig_active_channel

ARTIFACT_DIR = Path("reports/artifacts/dfs_evidence")
WIFI_IFACE = "wifi1"
ATH_IFACE = "ath1"
RADAR_CMD = f"radartool -i {WIFI_IFACE} bangradar"
DFS_CHANNELS = {52, 56, 60, 64}
BANNED_UBR = set(range(100, 145))
SAFE_RESTORE_CH = 36


def _hosts(profile_active: dict[str, Any]) -> tuple[str, str, str]:
    dut = (profile_active or {}).get("dut") or {}
    hosts = lab_hosts(profile_active)
    host = str(
        dut.get("local_ip")
        or dut.get("ssh_host")
        or hosts.get("dut_ipv4")
        or hosts.get("dut_host")
        or ""
    ).split("/")[0]
    return host, str(hosts.get("dut_user") or dut.get("username") or "root"), str(
        hosts.get("dut_password") or dut.get("password") or ""
    )


def ssh(profile_active: dict[str, Any], remote: str, *, timeout_s: int = 45) -> str:
    host, user, password = _hosts(profile_active)
    return _ssh_cmd(host, password, remote, user=user, timeout_s=timeout_s)


def cpe_ssh(profile_active: dict[str, Any], remote: str, *, timeout_s: int = 30) -> str:
    dut = (profile_active or {}).get("dut") or {}
    hosts = lab_hosts(profile_active)
    remotes = list(dut.get("remote_ips") or [])
    host = str(remotes[0] if remotes else hosts.get("cpe_lan_host") or "").split("/")[0]
    if not host:
        return ""
    user = str(hosts.get("dut_user") or dut.get("username") or "root")
    password = str(hosts.get("dut_password") or dut.get("password") or "")
    return _ssh_cmd(host, password, remote, user=user, timeout_s=timeout_s)


def write_evidence(case_id: str, payload: dict[str, Any]) -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    path = ARTIFACT_DIR / f"{case_id}.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"[dfs] evidence -> {path}")


def channel_from_iwconfig(text: str) -> int | None:
    parsed = parse_iwconfig_active_channel(text)
    m = re.match(r"(\d+)", str(parsed or ""))
    if m:
        return int(m.group(1))
    m = re.search(r"Channel[:\s]+(\d+)", text, re.I)
    if m:
        return int(m.group(1))
    return None


def snapshot_radio(profile_active: dict[str, Any]) -> dict[str, Any]:
    raw = ssh(
        profile_active,
        "echo '---COUNTRY---'; "
        "uci get wireless.wifi1.country 2>/dev/null || uci get wireless.radio1.country 2>/dev/null || true; "
        "echo '---CFG_CH---'; "
        "uci get advwireless.ath1.channel 2>/dev/null || uci get wireless.wifi1.channel 2>/dev/null || true; "
        "echo '---HTMODE---'; "
        "uci get wireless.wifi1.htmode 2>/dev/null || true; "
        "echo '---IW---'; iwconfig ath1 2>/dev/null || true; "
        "echo '---MODE---'; cfg80211tool ath1 get_mode 2>/dev/null || true; "
        "echo '---DFSKEYS---'; "
        "(uci show 2>/dev/null | grep -iE 'dfs|doth|cac|nol|nop|radar' | head -n 80) || true; "
        "echo '---RADAR---'; radartool -h 2>/dev/null | head -n 40 || radartool 2>/dev/null | head -n 40 || true",
        timeout_s=40,
    )
    snap = {
        "raw": raw[-12000:],
        "country": _between(raw, "COUNTRY", "CFG_CH"),
        "cfg_channel": _between(raw, "CFG_CH", "HTMODE"),
        "htmode": _between(raw, "HTMODE", "IW"),
        "iwconfig": _between(raw, "IW", "MODE"),
        "get_mode": _between(raw, "MODE", "DFSKEYS"),
        "dfs_keys": _between(raw, "DFSKEYS", "RADAR"),
        "radartool": _between(raw, "RADAR", None),
    }
    snap["active_channel"] = channel_from_iwconfig(str(snap["iwconfig"]))
    snap["running_htmode"] = parse_running_htmode(str(snap["get_mode"]))
    snap["country_code"] = _first_token(str(snap["country"]))
    snap["cfg_channel_n"] = _int_or_none(snap["cfg_channel"])
    return snap


def dfs_event_dump(profile_active: dict[str, Any]) -> str:
    return ssh(
        profile_active,
        "echo '---LOGREAD---'; logread 2>/dev/null | grep -iE 'dfs|radar|cac|nol|nop|evac|terminated link|established link' | tail -n 120; "
        "echo '---WIFI_EV---'; tail -n 80 /tmp/kwn-wifi1-events.log 2>/dev/null || true; "
        "echo '---DMSG---'; dmesg 2>/dev/null | grep -iE 'dfs|radar|cac|nol' | tail -n 40 || true",
        timeout_s=40,
    )


def set_channel(profile_active: dict[str, Any], channel: int) -> str:
    return ssh(
        profile_active,
        f"ucidyn set advwireless.ath1.channel {int(channel)}; "
        f"ucidyn set wireless.wifi1.channel {int(channel)} 2>/dev/null || true; "
        "ucidyn apply; echo CHANNEL_RC:$?",
        timeout_s=90,
    )


def set_htmode(profile_active: dict[str, Any], htmode: str) -> str:
    return ssh(
        profile_active,
        f"ucidyn set wireless.wifi1.htmode {htmode}; ucidyn apply; echo HT_RC:$?",
        timeout_s=90,
    )


def bangradar(profile_active: dict[str, Any]) -> str:
    print(f"[dfs] {RADAR_CMD}")
    return ssh(profile_active, f"{RADAR_CMD}; echo BANG_RC:$?", timeout_s=30)


def restore_safe(profile_active: dict[str, Any], channel: int | None = None, htmode: str | None = None) -> None:
    ch = int(channel or SAFE_RESTORE_CH)
    try:
        if htmode:
            set_htmode(profile_active, htmode)
        set_channel(profile_active, ch)
    except Exception as exc:
        print(f"[dfs] restore failed: {exc}")


def wait_active_channel(
    profile_active: dict[str, Any],
    *,
    timeout_s: float = 90,
    not_equal: int | None = None,
    equal: int | None = None,
    poll_s: float = 2.0,
) -> tuple[int | None, float]:
    start = time.monotonic()
    last: int | None = None
    while time.monotonic() - start < timeout_s:
        snap = snapshot_radio(profile_active)
        last = snap.get("active_channel")
        if equal is not None and last == equal:
            return last, time.monotonic() - start
        if not_equal is not None and last is not None and last != not_equal:
            return last, time.monotonic() - start
        time.sleep(poll_s)
    return last, time.monotonic() - start


_CAC_LINE = re.compile(
    r"([A-Z][a-z]{2}\s+[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+\d{4})"
    r":\s+DFS CAC (started|end) on channel (\d+)",
    re.I,
)


def _log_stamp(raw: str) -> datetime:
    return datetime.strptime(re.sub(r"\s+", " ", raw.strip()), "%a %b %d %H:%M:%S %Y")


_LINK_LINE = re.compile(
    r"([A-Z][a-z]{2}\s+[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+\d{4})"
    r":\s+BTS (terminated link|established link)",
    re.I,
)


def parse_link_events(text: str) -> list[dict[str, Any]]:
    """``BTS terminated link`` / ``BTS established link`` sorted by DUT clock."""
    rows: list[dict[str, Any]] = []
    for match in _LINK_LINE.finditer(text or ""):
        kind = "terminated" if "terminat" in match.group(2).lower() else "established"
        stamp = _log_stamp(match.group(1))
        rows.append({"kind": kind, "at": stamp.isoformat(sep=" ")})
    rows.sort(key=lambda row: row["at"])
    return rows


def parse_cac_intervals(text: str) -> list[dict[str, Any]]:
    """Pair ``DFS CAC started`` / ``DFS CAC end`` by DUT clock, ignoring log order."""
    events: list[tuple[datetime, str, int]] = []
    for match in _CAC_LINE.finditer(text or ""):
        events.append((_log_stamp(match.group(1)), match.group(2).lower(), int(match.group(3))))
    events.sort(key=lambda item: item[0])
    pending: dict[int, datetime] = {}
    done: list[dict[str, Any]] = []
    for stamp, kind, channel in events:
        if kind == "started":
            pending[channel] = stamp
            continue
        start = pending.get(channel)
        if start is None or stamp < start:
            continue
        done.append(
            {
                "channel": channel,
                "start": start.isoformat(sep=" "),
                "end": stamp.isoformat(sep=" "),
                "duration_s": (stamp - start).total_seconds(),
            }
        )
        pending.pop(channel, None)
    return done


def wait_fresh_cac(
    profile_active: dict[str, Any],
    *,
    known: set[tuple[Any, ...]],
    timeout_s: float = 110,
) -> tuple[dict[str, Any] | None, str]:
    """Poll until a CAC start/end pair appears that was not already in ``known``."""
    logs = ""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        logs = dfs_event_dump(profile_active)
        for interval in parse_cac_intervals(logs):
            key = (interval["channel"], interval["start"], interval["end"])
            if key not in known:
                return interval, logs
        time.sleep(3)
    return None, logs


def wait_cac_markers(profile_active: dict[str, Any], *, timeout_s: float = 90) -> tuple[str, float]:
    start = time.monotonic()
    blob = ""
    while time.monotonic() - start < timeout_s:
        blob = dfs_event_dump(profile_active)
        if re.search(r"\bcac\b|channel availability", blob, re.I):
            return blob, time.monotonic() - start
        time.sleep(3)
    return blob, time.monotonic() - start


def nol_blob(profile_active: dict[str, Any]) -> str:
    return ssh(
        profile_active,
        "echo '---NOL---'; "
        "(radartool -i wifi1 getnol 2>/dev/null || radartool -i wifi1 nol 2>/dev/null || true); "
        "(uci show 2>/dev/null | grep -iE 'nol|nop' | head -n 40); "
        "(logread 2>/dev/null | grep -iE 'nol|non-occup|nop' | tail -n 30); "
        "(cat /sys/class/net/wifi1/dfs_nol 2>/dev/null || true); "
        "(ls /sys/kernel/debug/ieee80211/*/dfs* 2>/dev/null | head); "
        "echo '---END---'",
        timeout_s=30,
    )


def _between(raw: str, start: str, end: str | None) -> str:
    marker = f"---{start}---"
    if marker not in raw:
        return ""
    body = raw.split(marker, 1)[1]
    if end:
        end_m = f"---{end}---"
        if end_m in body:
            body = body.split(end_m, 1)[0]
    return body.strip()


def _first_token(text: str) -> str:
    for line in str(text or "").splitlines():
        tok = line.strip().strip("'\"")
        if tok and "not found" not in tok.lower():
            return tok
    return ""


def _int_or_none(text: Any) -> int | None:
    m = re.search(r"\d+", str(text or ""))
    return int(m.group(0)) if m else None
