"""Assertions for DFS plan cases ``TC_DFS_001`` … ``TC_DFS_028``."""

from __future__ import annotations

import os
import re
import time
from datetime import datetime, timedelta
from typing import Any

import pytest

from config.dfs_test_cases import case_by_id
from utils.dfs_lab import (
    DFS_CHANNELS,
    SAFE_RESTORE_CH,
    bangradar,
    cpe_ssh,
    dfs_event_dump,
    nol_blob,
    parse_cac_intervals,
    parse_link_events,
    restore_safe,
    set_channel,
    ssh,
    set_htmode,
    snapshot_radio,
    wait_active_channel,
    wait_cac_markers,
    write_evidence,
)

INDIA_COUNTRY = {"IN", "IND", "INDIA", "356"}


def execute_dfs_case(case_id: str, profile_active: dict[str, Any]) -> None:
    case = case_by_id(case_id)
    mode = case.get("mode")
    if mode == "skip_na":
        pytest.skip(case.get("note") or "Not available on this stand")
    if case.get("long") and not _allow_long():
        pytest.skip(f"{case_id} waits ~30 min — pass --allow-dfs-long")

    before = snapshot_radio(profile_active)
    orig_ch = before.get("cfg_channel_n") or before.get("active_channel") or SAFE_RESTORE_CH
    orig_ht = before.get("htmode") or before.get("running_htmode")
    print(f"[dfs][{case_id}] start cfg_ch={orig_ch} ht={orig_ht} active={before.get('active_channel')}")
    try:
        _dispatch(case, profile_active, before)
    finally:
        if case.get("mutates"):
            restore_safe(profile_active, orig_ch, str(orig_ht).split()[0] if orig_ht else None)


def _allow_long() -> bool:
    return os.environ.get("ALLOW_DFS_LONG", "").strip() in {"1", "true", "yes"} or bool(
        globals().get("_DFS_LONG_FLAG")
    )


def set_long_flag(enabled: bool) -> None:
    globals()["_DFS_LONG_FLAG"] = bool(enabled)


def _dispatch(case: dict[str, Any], profile: dict[str, Any], before: dict[str, Any]) -> None:
    mode = case["mode"]
    handlers = {
        "regdomain_india": _regdomain_india,
        "banned_channels": _banned_channels,
        "dfs_capability": _dfs_capability,
        "cac_duration": _cac_duration,
        "cac_expiry": _cac_expiry,
        "cac_before_use": _cac_before_use,
        "radar_then_cac": _radar_then_cac,
        "nop_expiry_cac": _nop_expiry_cac,
        "radar_inservice": _radar_inservice,
        "evac_time": _evac_time,
        "nol_add": _nol_add,
        "post_radar_select": _post_radar_select,
        "nop_strict": _nop_strict,
        "nol_remove": _nol_remove,
        "ht160_block": _ht160_block,
        "radar_bw_downgrade": _radar_bw_downgrade,
        "post_downgrade_block": _post_downgrade_block,
        "cpe_follow": _cpe_follow,
        "rrm_restore": _rrm_restore,
        "avoid_radar_channel": _avoid_radar_channel,
        "log_reason_codes": _log_reason_codes,
        "acs_converge": _acs_converge,
    }
    fn = handlers.get(mode)
    if fn is None:
        raise RuntimeError(f"{case['id']} unknown mode {mode!r}")
    fn(case, profile, before)


def _regdomain_india(case: dict[str, Any], profile: dict[str, Any], before: dict[str, Any]) -> None:
    code = str(before.get("country_code") or "").upper()
    write_evidence(case["id"], {"country": code, "keys": before.get("dfs_keys")})
    assert any(tok in code for tok in INDIA_COUNTRY) or code.endswith("IN"), (
        f"{case['id']}: expected India regulatory domain, got {code!r}"
    )


def _banned_channels(case: dict[str, Any], profile: dict[str, Any], before: dict[str, Any]) -> None:
    orig = before.get("cfg_channel_n") or SAFE_RESTORE_CH
    applied: dict[int, dict[str, Any]] = {}
    for ch in case.get("channels") or [100, 120, 144]:
        out = set_channel(profile, int(ch))
        time.sleep(4)
        snap = snapshot_radio(profile)
        active = snap.get("active_channel")
        cfg = snap.get("cfg_channel_n")
        applied[int(ch)] = {"set": out[-400:], "active": active, "cfg": cfg}
        print(f"[dfs][{case['id']}] try ch={ch} cfg={cfg} active={active}")
    restore_safe(profile, orig)
    write_evidence(case["id"], {"attempts": applied})
    leaks = [
        ch
        for ch, row in applied.items()
        if row.get("active") == ch or row.get("cfg") == ch
    ]
    assert not leaks, f"{case['id']}: banned UBR channels were accepted: {leaks}"


def _dfs_capability(case: dict[str, Any], profile: dict[str, Any], before: dict[str, Any]) -> None:
    blob = (
        str(before.get("dfs_keys") or "")
        + "\n"
        + str(before.get("radartool") or "")
        + "\n"
        + dfs_event_dump(profile)
    )
    write_evidence(case["id"], {"blob": blob[-6000:]})
    has_radar = bool(re.search(r"radartool|bangradar|radar", blob, re.I))
    has_dfs = bool(re.search(r"\bdfs\b|doth|cac|nol", blob, re.I))
    assert has_radar and has_dfs, (
        f"{case['id']}: missing DFS/radar capability markers (radar={has_radar} dfs={has_dfs})"
    )


_DFS_CHOICES = (52, 56, 60, 64)
_BLACKLIST_BAND = re.compile(
    r"([A-Z][a-z]{2}\s+[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+\d{4})"
    r":\s+DFS blacklisted frequency band\s+(\d+)\s+to\s+(\d+)\s*MHz",
    re.I,
)
_NOP_WINDOW = timedelta(minutes=30)
_CAC_STARTED = re.compile(
    r"([A-Z][a-z]{2}\s+[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+\d{4})"
    r":\s+DFS CAC started on channel (\d+)",
    re.I,
)


def _dut_now(profile: dict[str, Any]) -> datetime | None:
    raw = ssh(profile, "date '+%Y-%m-%d %H:%M:%S'")
    for line in raw.splitlines():
        try:
            return datetime.strptime(line.strip(), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return None


def _blacklist_bands(text: str, now: datetime | None = None) -> list[tuple[int, int]]:
    """Bands still inside the 30-minute non-occupancy window."""
    bands: list[tuple[int, int]] = []
    for stamp, lo, hi in _BLACKLIST_BAND.findall(text or ""):
        if now is not None:
            logged = datetime.strptime(re.sub(r"\s+", " ", stamp.strip()), "%a %b %d %H:%M:%S %Y")
            if now - logged > _NOP_WINDOW:
                continue
        bands.append((int(lo), int(hi)))
    return bands


def _channel_blacklisted(channel: int, bands: list[tuple[int, int]]) -> bool:
    center = 5000 + int(channel) * 5
    low, high = center - 10, center + 10
    return any(low < hi and high > lo for lo, hi in bands)


def _open_dfs_channels(preferred: int, bands: list[tuple[int, int]]) -> list[int]:
    ordered: list[int] = []
    for channel in (int(preferred), *_DFS_CHOICES):
        if channel not in ordered and not _channel_blacklisted(channel, bands):
            ordered.append(channel)
    return ordered


def _cac_start_keys(text: str) -> set[tuple[str, int]]:
    keys: set[tuple[str, int]] = set()
    for match in _CAC_STARTED.finditer(text or ""):
        stamp = re.sub(r"\s+", " ", match.group(1).strip())
        keys.add((stamp, int(match.group(2))))
    return keys


def _move_to_dfs(profile: dict[str, Any], channel: int) -> dict[str, Any]:
    """Land on a DFS channel. If the requested one is blacklisted, use the next open one."""
    bands = _blacklist_bands(dfs_event_dump(profile), _dut_now(profile))
    choices = _open_dfs_channels(channel, bands)
    if not choices:
        print(f"[dfs] every DFS channel is inside a blacklist {bands}; still trying {channel}")
        choices = [int(channel)]
    snap = snapshot_radio(profile)
    for choice in choices:
        print(f"[dfs] move to DFS channel {choice}")
        out = set_channel(profile, choice)
        time.sleep(4)
        snap = snapshot_radio(profile)
        active = snap.get("active_channel")
        if "blacklist" in out.lower() or not _is_dfs_channel(active):
            print(f"[dfs] channel {choice} not accepted (active={active}); trying next")
            continue
        return snap
    return snap


_CAC_CYCLE: dict[str, Any] | None = None


def _is_dfs_channel(channel: int | None) -> bool:
    return channel is not None and 52 <= int(channel) <= 64


def _wait_cac_cycle(
    profile: dict[str, Any],
    *,
    known_cac: set[tuple[Any, ...]],
    known_starts: set[tuple[str, int]],
    known_term: set[str],
    known_up: set[str],
) -> tuple[dict[str, Any] | None, str]:
    """Wait up to 150s, but give up after 20s if this channel never starts CAC."""
    started = time.monotonic()
    logs = ""
    saw_start = False
    while time.monotonic() - started < 150:
        logs = dfs_event_dump(profile)
        if _cac_start_keys(logs) - known_starts:
            saw_start = True
        cycle = _match_cac_cycle(logs, known_cac, known_term, known_up)
        if cycle:
            return cycle, logs
        if not saw_start and time.monotonic() - started > 20:
            return None, logs
        time.sleep(5)
    return None, logs


def shared_cac_cycle(profile: dict[str, Any], channel: int = 52) -> dict[str, Any]:
    """One channel change, then the link-down / CAC / link-up sequence.

    Later CAC-timing cases reuse this capture instead of waiting again.
    """
    global _CAC_CYCLE
    if _CAC_CYCLE is not None:
        print(
            f"[dfs] reuse CAC cycle ch {_CAC_CYCLE.get('cac_channel')} "
            f"{_CAC_CYCLE.get('duration_s')}s "
            f"(link {_CAC_CYCLE.get('terminated')} -> {_CAC_CYCLE.get('established')})"
        )
        reused = dict(_CAC_CYCLE)
        reused["reused"] = True
        return reused

    snap = snapshot_radio(profile)
    active = snap.get("active_channel")
    pre = dfs_event_dump(profile)
    bands = _blacklist_bands(pre, _dut_now(profile))
    choices = _open_dfs_channels(channel, bands)
    if not choices:
        cycle = {
            "ok": False,
            "error": f"DFS channels 52-64 are inside blacklisted bands {bands}",
            "reused": False,
        }
        _CAC_CYCLE = cycle
        return dict(cycle)
    ht = str(snap.get("running_htmode") or snap.get("htmode") or "")
    if ("80" in ht or "160" in ht) and any(_channel_blacklisted(ch, bands) for ch in _DFS_CHOICES):
        print(f"[dfs] {ht} overlaps a blacklist; set HT20 so an open 20 MHz DFS channel can run CAC")
        set_htmode(profile, "HT20")
        time.sleep(6)
    if _is_dfs_channel(active):
        print(f"[dfs] active={active}; move to channel 36 so the next DFS channel restarts CAC")
        set_channel(profile, 36)
        time.sleep(8)
    before = dfs_event_dump(profile)
    known_cac = {(row["channel"], row["start"], row["end"]) for row in parse_cac_intervals(before)}
    known_starts = _cac_start_keys(before)
    known_term = {row["at"] for row in parse_link_events(before) if row["kind"] == "terminated"}
    known_up = {row["at"] for row in parse_link_events(before) if row["kind"] == "established"}
    logs = ""
    cycle = None
    tried: list[int] = []
    for choice in choices:
        tried.append(choice)
        print(f"[dfs] set channel {choice}; waiting for link down, CAC start/end, link up")
        out = set_channel(profile, choice)
        if "blacklist" in out.lower():
            print(f"[dfs] channel {choice} rejected as blacklisted")
            continue
        cycle, logs = _wait_cac_cycle(
            profile,
            known_cac=known_cac,
            known_starts=known_starts,
            known_term=known_term,
            known_up=known_up,
        )
        if cycle:
            break
        print(f"[dfs] channel {choice} did not start a CAC; trying the next open DFS channel")
    if cycle is None:
        cycle = {
            "ok": False,
            "error": (
                f"no CAC cycle after trying channels {tried}; "
                "blacklisted bands "
                f"{bands or 'none'}"
            ),
            "logs": logs[-4000:],
            "reused": False,
        }
    else:
        cycle["logs"] = logs[-4000:]
        cycle["reused"] = False
        cycle["ok"] = True
        cycle["error"] = _cac_cycle_error(cycle)
        if cycle["error"]:
            cycle["ok"] = False
        print(
            f"[dfs] CAC cycle ch {cycle.get('cac_channel')} "
            f"down {cycle.get('terminated')} "
            f"cac {cycle.get('cac_start')} -> {cycle.get('cac_end')} ({cycle.get('duration_s')}s) "
            f"up {cycle.get('established')}"
        )
    _CAC_CYCLE = cycle
    return dict(cycle)


def _match_cac_cycle(
    logs: str,
    known_cac: set[tuple[Any, ...]],
    known_term: set[str],
    known_up: set[str],
) -> dict[str, Any] | None:
    intervals = [
        row
        for row in parse_cac_intervals(logs)
        if (row["channel"], row["start"], row["end"]) not in known_cac and _is_dfs_channel(row["channel"])
    ]
    # Link-down is often logged just before the channel command, so it may already
    # be in the pre-change snapshot. Keep any terminate within 2 minutes of CAC start.
    terms = [row["at"] for row in parse_link_events(logs) if row["kind"] == "terminated"]
    ups = [row["at"] for row in parse_link_events(logs) if row["kind"] == "established" and row["at"] not in known_up]
    if not intervals or not ups:
        return None
    for interval in reversed(intervals):
        start, end = interval["start"], interval["end"]
        term = ""
        for stamp in terms:
            if stamp <= start and _stamp_delta(start, stamp) <= 120:
                term = stamp
        up = min((stamp for stamp in ups if stamp >= end), default="")
        if term and up:
            return {
                "cac_channel": interval["channel"],
                "cac_start": start,
                "cac_end": end,
                "duration_s": interval["duration_s"],
                "terminated": term,
                "established": up,
            }
    return None


def _stamp_delta(later: str, earlier: str) -> float:
    from datetime import datetime

    return (datetime.fromisoformat(later) - datetime.fromisoformat(earlier)).total_seconds()


def _cac_cycle_error(cycle: dict[str, Any]) -> str:
    order = (cycle["terminated"], cycle["cac_start"], cycle["cac_end"], cycle["established"])
    if list(order) != sorted(order):
        return (
            "expected link terminated, then CAC started, then CAC end, then link established; "
            f"got down={cycle['terminated']} start={cycle['cac_start']} "
            f"end={cycle['cac_end']} up={cycle['established']}"
        )
    duration = float(cycle["duration_s"])
    if not 45 <= duration <= 75:
        return (
            f"DUT log CAC on channel {cycle['cac_channel']} was {duration:.1f}s "
            f"({cycle['cac_start']} -> {cycle['cac_end']}); expected about 60s"
        )
    return ""


def _use_shared_cac(case: dict[str, Any], profile: dict[str, Any], *, require_link_after_cac: bool) -> None:
    channel = int(case.get("channel") or 52)
    cycle = shared_cac_cycle(profile, channel)
    write_evidence(case["id"], cycle)
    assert cycle.get("ok"), f"{case['id']}: {cycle.get('error')}"
    if require_link_after_cac:
        assert cycle["cac_end"] <= cycle["established"], (
            f"{case['id']}: link established {cycle['established']} before CAC end {cycle['cac_end']}"
        )


def _cac_duration(case: dict[str, Any], profile: dict[str, Any], before: dict[str, Any]) -> None:
    _use_shared_cac(case, profile, require_link_after_cac=False)


def _cac_before_use(case: dict[str, Any], profile: dict[str, Any], before: dict[str, Any]) -> None:
    _use_shared_cac(case, profile, require_link_after_cac=True)


def _cac_expiry(case: dict[str, Any], profile: dict[str, Any], before: dict[str, Any]) -> None:
    ch = int(case.get("channel") or 52)
    set_channel(profile, ch)
    logs1, _ = wait_cac_markers(profile, timeout_s=85)
    down = ssh_ok(profile, "ifconfig ath1 down; echo DOWN:$?")
    time.sleep(65)
    up = ssh_ok(profile, "ifconfig ath1 up; echo UP:$?")
    logs2, waited = wait_cac_markers(profile, timeout_s=85)
    write_evidence(case["id"], {"down": down, "up": up, "first": logs1[-1500:], "second": logs2[-1500:], "waited": waited})
    assert re.search(r"\bcac\b", logs2, re.I), f"{case['id']}: no new CAC after 65s delay"


def ssh_ok(profile: dict[str, Any], remote: str) -> str:
    from utils.dfs_lab import ssh

    return ssh(profile, remote, timeout_s=30)


def _radar_then_cac(case: dict[str, Any], profile: dict[str, Any], before: dict[str, Any]) -> None:
    ch = int(case.get("channel") or 52)
    _move_to_dfs(profile, ch)
    wait_cac_markers(profile, timeout_s=80)
    bang = bangradar(profile)
    new_ch, dt = wait_active_channel(profile, not_equal=ch, timeout_s=90)
    logs, cac_dt = wait_cac_markers(profile, timeout_s=85)
    write_evidence(case["id"], {"bang": bang, "from": ch, "to": new_ch, "switch_s": dt, "cac_s": cac_dt, "logs": logs[-3000:]})
    assert new_ch and new_ch != ch, f"{case['id']}: no channel change after bangradar (still {new_ch})"
    if new_ch in {52, 56, 60, 64}:
        assert re.search(r"\bcac\b", logs, re.I), f"{case['id']}: new channel {new_ch} is DFS but no CAC"


def _nop_expiry_cac(case: dict[str, Any], profile: dict[str, Any], before: dict[str, Any]) -> None:
    ch = int(case.get("channel") or 52)
    _move_to_dfs(profile, ch)
    wait_cac_markers(profile, timeout_s=80)
    bangradar(profile)
    time.sleep(2)
    nol1 = nol_blob(profile)
    print(f"[dfs][{case['id']}] waiting 30 min for NOP expiry")
    time.sleep(30 * 60)
    set_channel(profile, ch)
    logs, waited = wait_cac_markers(profile, timeout_s=90)
    nol2 = nol_blob(profile)
    write_evidence(case["id"], {"nol_before": nol1[-2000:], "nol_after": nol2[-2000:], "logs": logs[-2000:], "waited": waited})
    assert re.search(r"\bcac\b", logs, re.I), f"{case['id']}: no CAC after NOP wait when returning to {ch}"


def _radar_inservice(case: dict[str, Any], profile: dict[str, Any], before: dict[str, Any]) -> None:
    ch = int(case.get("channel") or 52)
    _move_to_dfs(profile, ch)
    wait_cac_markers(profile, timeout_s=80)
    bang = bangradar(profile)
    logs = dfs_event_dump(profile)
    write_evidence(case["id"], {"bang": bang, "logs": logs[-4000:]})
    assert re.search(r"radar", logs + bang, re.I), f"{case['id']}: no radar detection marker while in service"


def _evac_time(case: dict[str, Any], profile: dict[str, Any], before: dict[str, Any]) -> None:
    ch = int(case.get("channel") or 52)
    _move_to_dfs(profile, ch)
    wait_cac_markers(profile, timeout_s=80)
    t0 = time.monotonic()
    bangradar(profile)
    new_ch, dt = wait_active_channel(profile, not_equal=ch, timeout_s=15, poll_s=0.2)
    write_evidence(case["id"], {"from": ch, "to": new_ch, "evac_s": dt, "wall_s": time.monotonic() - t0})
    assert new_ch and new_ch != ch, f"{case['id']}: channel {ch} not vacated after bangradar"
    assert dt <= 0.2, (
        f"{case['id']}: evacuation took {dt:.3f}s (plan requires ≤200 ms). "
        "Measured DUT-side from bangradar to new iwconfig channel."
    )


def _nol_add(case: dict[str, Any], profile: dict[str, Any], before: dict[str, Any]) -> None:
    ch = int(case.get("channel") or 52)
    _move_to_dfs(profile, ch)
    wait_cac_markers(profile, timeout_s=80)
    bangradar(profile)
    time.sleep(2)
    nol = nol_blob(profile)
    logs = dfs_event_dump(profile)
    write_evidence(case["id"], {"channel": ch, "nol": nol[-4000:], "logs": logs[-2000:]})
    assert str(ch) in nol + logs or re.search(r"nol|non-occup|nop", nol + logs, re.I), (
        f"{case['id']}: channel {ch} not visible in NOL after bangradar"
    )


def _post_radar_select(case: dict[str, Any], profile: dict[str, Any], before: dict[str, Any]) -> None:
    ch = int(case.get("channel") or 52)
    _move_to_dfs(profile, ch)
    wait_cac_markers(profile, timeout_s=80)
    bangradar(profile)
    new_ch, dt = wait_active_channel(profile, not_equal=ch, timeout_s=20)
    write_evidence(case["id"], {"from": ch, "to": new_ch, "dt": dt})
    assert new_ch and new_ch != ch, f"{case['id']}: ACS/DCS did not select a new channel after radar"


def _nop_strict(case: dict[str, Any], profile: dict[str, Any], before: dict[str, Any]) -> None:
    ch = int(case.get("channel") or 52)
    _move_to_dfs(profile, ch)
    wait_cac_markers(profile, timeout_s=80)
    bangradar(profile)
    time.sleep(3)
    set_channel(profile, ch)
    time.sleep(5)
    snap = snapshot_radio(profile)
    active = snap.get("active_channel")
    write_evidence(case["id"], {"forced": ch, "active": active, "snap": _slim(snap)})
    assert active != ch, f"{case['id']}: BTS accepted NOL channel {ch} during NOP (active={active})"


def _nol_remove(case: dict[str, Any], profile: dict[str, Any], before: dict[str, Any]) -> None:
    ch = int(case.get("channel") or 52)
    _move_to_dfs(profile, ch)
    wait_cac_markers(profile, timeout_s=80)
    bangradar(profile)
    nol1 = nol_blob(profile)
    time.sleep(30 * 60)
    nol2 = nol_blob(profile)
    write_evidence(case["id"], {"nol_before": nol1[-2500:], "nol_after": nol2[-2500:]})
    still = str(ch) in nol2 and re.search(r"nol|non-occup", nol2, re.I)
    assert not still, f"{case['id']}: channel {ch} still on NOL after 30 minutes"


def _ht160_block(case: dict[str, Any], profile: dict[str, Any], before: dict[str, Any]) -> None:
    set_htmode(profile, "HT160")
    time.sleep(8)
    snap = snapshot_radio(profile)
    running = str(snap.get("running_htmode") or "")
    write_evidence(case["id"], {"running": running, "htmode_uci": snap.get("htmode"), "snap": _slim(snap)})
    assert "160" in running or "160" in str(snap.get("htmode") or ""), (
        f"{case['id']}: HT160 not applied (running={running!r}, uci={snap.get('htmode')!r})"
    )
    set_channel(profile, 149)
    time.sleep(5)
    after = snapshot_radio(profile)
    write_evidence(case["id"] + "_ch149", {"after": _slim(after)})
    active = after.get("active_channel")
    assert active is None or 36 <= int(active) <= 64, (
        f"{case['id']}: 160 MHz accepted channel {active} outside 36–64"
    )


def _radar_bw_downgrade(case: dict[str, Any], profile: dict[str, Any], before: dict[str, Any]) -> None:
    set_htmode(profile, "HT160")
    set_channel(profile, 36)
    time.sleep(8)
    before_bw = snapshot_radio(profile)
    running = str(before_bw.get("running_htmode") or "")
    if "160" not in running:
        write_evidence(case["id"], {"before": _slim(before_bw)})
        raise AssertionError(
            f"{case['id']}: never reached HT160 (running={running!r}); cannot verify 160→80 downgrade"
        )
    bangradar(profile)
    time.sleep(5)
    after = snapshot_radio(profile)
    after_bw = str(after.get("running_htmode") or "")
    write_evidence(case["id"], {"before": running, "after": after_bw, "snap": _slim(after)})
    assert "80" in after_bw, f"{case['id']}: expected 160→80 downgrade, running={after_bw!r}"


def _post_downgrade_block(case: dict[str, Any], profile: dict[str, Any], before: dict[str, Any]) -> None:
    set_htmode(profile, "HT160")
    set_channel(profile, 36)
    time.sleep(8)
    bangradar(profile)
    time.sleep(6)
    after = snapshot_radio(profile)
    ch = after.get("active_channel")
    write_evidence(case["id"], {"channel": ch, "ht": after.get("running_htmode")})
    ok = ch is not None and (36 <= int(ch) <= 48 or 149 <= int(ch) <= 161)
    assert ok, f"{case['id']}: post-downgrade channel {ch} not in 36–48 or 149–161"


def _cpe_follow(case: dict[str, Any], profile: dict[str, Any], before: dict[str, Any]) -> None:
    ch = int(case.get("channel") or 52)
    _move_to_dfs(profile, ch)
    wait_cac_markers(profile, timeout_s=80)
    bangradar(profile)
    new_ch, _ = wait_active_channel(profile, not_equal=ch, timeout_s=25)
    cpe_iw = cpe_ssh(profile, "iwconfig ath1 2>/dev/null || iwconfig ath0 2>/dev/null || true")
    from utils.dfs_lab import channel_from_iwconfig

    cpe_ch = channel_from_iwconfig(cpe_iw)
    write_evidence(case["id"], {"bts": new_ch, "cpe": cpe_ch, "cpe_iw": (cpe_iw or "")[-1500:]})
    assert new_ch, f"{case['id']}: BTS did not leave channel {ch}"
    if not cpe_iw or "timeout" in (cpe_iw or "").lower() or "No route" in (cpe_iw or ""):
        pytest.skip(f"{case['id']}: CPE SSH not reachable; BTS moved {ch} -> {new_ch}")
    assert cpe_ch == new_ch, f"{case['id']}: CPE channel {cpe_ch} != BTS {new_ch}"


def _rrm_restore(case: dict[str, Any], profile: dict[str, Any], before: dict[str, Any]) -> None:
    set_htmode(profile, "HT160")
    set_channel(profile, 36)
    time.sleep(8)
    bangradar(profile)
    time.sleep(5)
    mid = snapshot_radio(profile)
    print(f"[dfs][{case['id']}] waiting 30 min for RRM restore")
    time.sleep(30 * 60)
    end = snapshot_radio(profile)
    write_evidence(case["id"], {"mid": _slim(mid), "end": _slim(end)})
    # Honest: plan comments say Airtel did not confirm restore. Assert only if 160 returns.
    end_ht = str(end.get("running_htmode") or "")
    assert "160" in end_ht, (
        f"{case['id']}: RRM did not restore HT160 after 30 min (running={end_ht!r}). "
        "Plan comment: restore not confirmed by Airtel."
    )


def _avoid_radar_channel(case: dict[str, Any], profile: dict[str, Any], before: dict[str, Any]) -> None:
    ch = int(case.get("channel") or 52)
    _move_to_dfs(profile, ch)
    wait_cac_markers(profile, timeout_s=80)
    bangradar(profile)
    new_ch, _ = wait_active_channel(profile, not_equal=ch, timeout_s=25)
    write_evidence(case["id"], {"hit": ch, "selected": new_ch})
    assert new_ch and new_ch != ch, f"{case['id']}: ACS still on radar-hit channel {ch}"


def _log_reason_codes(case: dict[str, Any], profile: dict[str, Any], before: dict[str, Any]) -> None:
    ch = int(case.get("channel") or 52)
    _move_to_dfs(profile, ch)
    wait_cac_markers(profile, timeout_s=80)
    bangradar(profile)
    time.sleep(3)
    logs = dfs_event_dump(profile)
    write_evidence(case["id"], {"logs": logs[-5000:]})
    assert re.search(r"radar", logs, re.I), f"{case['id']}: no RadarDetected-style log after bangradar"


def _acs_converge(case: dict[str, Any], profile: dict[str, Any], before: dict[str, Any]) -> None:
    ch = int(case.get("channel") or 52)
    _move_to_dfs(profile, ch)
    wait_cac_markers(profile, timeout_s=80)
    t0 = time.monotonic()
    bangradar(profile)
    new_ch, dt = wait_active_channel(profile, not_equal=ch, timeout_s=10, poll_s=0.25)
    write_evidence(case["id"], {"from": ch, "to": new_ch, "dt": dt, "wall": time.monotonic() - t0})
    assert new_ch and new_ch != ch, f"{case['id']}: ACS did not pick a new channel"
    assert dt < 3.0, f"{case['id']}: ACS convergence {dt:.2f}s exceeds 3s"


def _slim(snap: dict[str, Any]) -> dict[str, Any]:
    return {
        k: snap.get(k)
        for k in ("country_code", "cfg_channel_n", "active_channel", "htmode", "running_htmode")
    }
