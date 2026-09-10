"""SSH/UCI VLAN mode helpers (no SNMP). BTS QinQ double-tag vs CPE untagged."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from utils.parsers import clean_ssh_output
from utils.vlan_uci import (
    build_bts_transparent_mgmt_commands,
    build_bts_qinq_commands,
    build_cpe_untagged_commands,
    build_verify_commands,
    expected_bts_qinq_text,
    expected_cpe_untagged_text,
)

MODE_ALIASES = {
    "transparent": ("transparent", "0", "untagged"),
    "access": ("access", "1"),
    "trunk": ("trunk", "2"),
    "qinq": ("qinq", "q-in-q", "q in q", "3"),
}


async def _ssh_run(ssh, command: str, *, timeout: int = 60) -> str:
    result = await ssh.send_command(command, timeout_ops=timeout)
    return clean_ssh_output(str(result.result or ""))


def _normalize_mode_name(mode: str) -> str:
    key = str(mode).lower().replace(" ", "").replace("-", "")
    if key in ("3",) or "qinq" in key:
        return "qinq"
    if key in ("2", "trunk"):
        return "trunk"
    if key in ("1", "access"):
        return "access"
    if key in ("transparent", "untagged", "0"):
        return "transparent"
    return key or str(mode).lower().strip()


def _parse_uci_get_output(text: str) -> dict[str, str]:
    """Parse lines like vlan.ath1.svlan='100' from uci get/show."""
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip().split(".")[-1].lower()
        val = val.strip().strip("'").strip('"')
        out[key] = val
    return out


async def read_vlan_uci_ssh(ssh, profile_tb: dict[str, Any], role: str) -> dict[str, str]:
    """
    Read VLAN/UCI values over SSH.

    Firmware may return either:
    - `vlan.ath1.mode='qinq'` (key=value form), or
    - just the value `qinq` (bare form) for `uci get ...`.

    We therefore extract the short key from each `uci get <path>` command and map
    the returned value accordingly.
    """
    out: dict[str, str] = {}
    for cmd in build_verify_commands(profile_tb, role):
        m = re.search(r"uci\s+get\s+([^\s]+)", cmd)
        uci_key = m.group(1) if m else ""
        short_key = uci_key.split(".")[-1].lower() if uci_key else ""

        raw = await _ssh_run(ssh, cmd)
        lines = str(raw or "").strip().splitlines()
        val = lines[-1].strip().strip("'").strip('"') if lines else ""

        # Prefer command-derived key mapping; fallback to key=value parsing if needed.
        if short_key and val:
            out[short_key] = val
        else:
            out.update(_parse_uci_get_output(raw))
    return out


async def apply_vlan_commands_ssh(ssh, commands: list[str]) -> bool:
    ok = True
    for cmd in commands:
        if not cmd:
            continue
        out = await _ssh_run(ssh, cmd)
        if "error" in out.lower() and "entry not found" not in out.lower():
            print(f"[vlan] WARN: {cmd} -> {out[:200]}")
            ok = False
    return ok


def _verify_bts_qinq(current: dict[str, str], profile_tb: dict[str, Any]) -> bool:
    exp = expected_bts_qinq_text(profile_tb)
    if _normalize_mode_name(current.get("mode", "")) != "qinq":
        if "qinq" not in str(current.get("mode", "")).lower():
            return False
    if current.get("svlan") and current["svlan"] != exp["svlan"]:
        return False
    if current.get("cvlan") and current["cvlan"] != exp["cvlan"]:
        return False
    if current.get("mgmtvlan") and current["mgmtvlan"] != exp["mgmtvlan"]:
        return False
    return True


def _verify_cpe_untagged(current: dict[str, str]) -> bool:
    mode = _normalize_mode_name(current.get("mode", "transparent"))
    if mode != "transparent":
        return False
    # Untagged CPE must not carry BTS double-tag IDs on data path
    for key in ("svlan", "cvlan"):
        val = (current.get(key) or "").strip()
        if val and val not in ("0", ""):
            return False
    return True


async def ensure_bts_qinq_ssh(ssh, profile_tb: dict[str, Any]) -> bool:
    current = await read_vlan_uci_ssh(ssh, profile_tb, "bts")
    if _verify_bts_qinq(current, profile_tb):
        exp = expected_bts_qinq_text(profile_tb)
        print(
            f"[vlan] BTS QinQ OK: svlan={current.get('svlan', exp['svlan'])} "
            f"cvlan={current.get('cvlan', exp['cvlan'])} mgmtvlan={current.get('mgmtvlan', exp['mgmtvlan'])}"
        )
        return True
    cmds = build_bts_qinq_commands(profile_tb)
    # Prefer UCI-only first — network reload drops SU for 1–3 minutes on this lab.
    soft = [c for c in cmds if "network reload" not in c]
    print(f"[vlan] BTS applying QinQ soft ({len(soft)} cmds, no network reload)")
    ok = await apply_vlan_commands_ssh(ssh, soft)
    await asyncio.sleep(5)
    after = await read_vlan_uci_ssh(ssh, profile_tb, "bts")
    if ok and _verify_bts_qinq(after, profile_tb):
        return True
    print("[vlan] BTS QinQ soft apply incomplete — full apply with network reload")
    ok = await apply_vlan_commands_ssh(ssh, cmds)
    await asyncio.sleep(20)
    after = await read_vlan_uci_ssh(ssh, profile_tb, "bts")
    return ok and _verify_bts_qinq(after, profile_tb)


async def ensure_bts_transparent_ssh(ssh, profile_tb: dict[str, Any]) -> bool:
    current = await read_vlan_uci_ssh(ssh, profile_tb, "bts")
    # Prefer full show — intermittent bare `uci get` parses can miss mgmtvlan and force reload.
    try:
        show = await _ssh_run(ssh, "uci show vlan.ath1 2>/dev/null")
        for line in show.splitlines():
            if "vlan.ath1." not in line or "=" not in line:
                continue
            key, _, val = line.partition("=")
            short = key.strip().split(".")[-1].lower()
            current[short] = val.strip().strip("'\"")
    except Exception:
        pass
    exp_mgmt = str(int((profile_tb.get("mgmt_vlan", {}) or {}).get("uci_value", 101)))
    mode = _normalize_mode_name(current.get("mode", "transparent"))
    mgmt = str(current.get("mgmtvlan", "")).strip()
    if mode == "transparent" and (mgmt == exp_mgmt or not mgmt):
        print(f"[vlan] BTS transparent OK (mgmtvlan={mgmt or exp_mgmt})")
        return True
    cmds = build_bts_transparent_mgmt_commands(profile_tb)
    soft = [c for c in cmds if "network reload" not in c]
    print(f"[vlan] BTS applying transparent soft ({len(soft)} cmds, no network reload)")
    ok = await apply_vlan_commands_ssh(ssh, soft)
    await asyncio.sleep(5)
    after = await read_vlan_uci_ssh(ssh, profile_tb, "bts")
    mode_after = _normalize_mode_name(after.get("mode", "transparent"))
    mgmt_after = str(after.get("mgmtvlan", "")).strip()
    if ok and mode_after == "transparent" and (mgmt_after == exp_mgmt or not mgmt_after):
        return True
    print(f"[vlan] BTS transparent soft incomplete — full apply with network reload")
    ok = await apply_vlan_commands_ssh(ssh, cmds)
    await asyncio.sleep(15)
    after = await read_vlan_uci_ssh(ssh, profile_tb, "bts")
    mode_after = _normalize_mode_name(after.get("mode", "transparent"))
    mgmt_after = str(after.get("mgmtvlan", "")).strip()
    return ok and mode_after == "transparent" and (mgmt_after == exp_mgmt or not mgmt_after)


async def ensure_cpe_untagged_ssh(ssh, profile_tb: dict[str, Any]) -> bool:
    current = await read_vlan_uci_ssh(ssh, profile_tb, "cpe")
    if _verify_cpe_untagged(current):
        print(f"[vlan] CPE untagged/transparent OK (mode={current.get('mode', 'transparent')})")
        return True
    cmds = build_cpe_untagged_commands(profile_tb)
    print(f"[vlan] CPE applying untagged/transparent ({len(cmds)} commands)")
    ok = await apply_vlan_commands_ssh(ssh, cmds)
    after = await read_vlan_uci_ssh(ssh, profile_tb, "cpe")
    return ok and _verify_cpe_untagged(after)


async def ensure_vlan_mode_ssh(
    ssh,
    role: str,
    expected: str,
    vlan_ssh_cfg: dict[str, Any],
    profile_tb: dict[str, Any] | None = None,
) -> bool:
    """
    Role-aware VLAN setup using vlan_uci profile (preferred) or legacy command lists.
    BTS + qinq -> double tag; CPE + transparent -> untagged.
    """
    tb = profile_tb or {}
    exp = _normalize_mode_name(expected)

    if role == "bts" and exp == "qinq" and tb.get("qinq"):
        return await ensure_bts_qinq_ssh(ssh, tb)
    if role == "bts" and exp == "transparent":
        return await ensure_bts_transparent_ssh(ssh, tb)

    if role == "cpe" and exp == "transparent":
        return await ensure_cpe_untagged_ssh(ssh, tb)

    # Legacy fallback: vlan_ssh.modes.<name>
    role_cfg = vlan_ssh_cfg.get(role, {}) or {}
    verify_cmds = list(role_cfg.get("verify_commands") or build_verify_commands(tb, role))
    mode_cmds = list((role_cfg.get("modes") or {}).get(exp) or [])
    if mode_cmds:
        return await apply_vlan_commands_ssh(ssh, mode_cmds)
    if verify_cmds:
        current = await _ssh_run(ssh, " && ".join(verify_cmds))
        if exp in current.lower():
            return True
    return True
