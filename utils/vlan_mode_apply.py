"""Apply BTS/CPE VLAN modes synchronously for link-debug campaigns."""

from __future__ import annotations

import asyncio
from typing import Any

from scrapli.driver.generic import AsyncGenericDriver

from utils.net_utils import format_ssh_host, normalize_ip
from utils.vlan_control import ensure_bts_qinq_ssh, ensure_bts_transparent_ssh, ensure_cpe_untagged_ssh
from utils.vlan_uci import build_cpe_untagged_commands


async def _open_bts_ssh(host: str, user: str, password: str) -> AsyncGenericDriver:
    conn = AsyncGenericDriver(
        host=format_ssh_host(host),
        auth_username=user,
        auth_password=password,
        auth_strict_key=False,
        transport="asyncssh",
    )
    await conn.open()
    return conn


async def _apply_cpe_vlan_via_remote_exec(
    bts_ssh: AsyncGenericDriver,
    *,
    su_count: int,
    profile_tb: dict[str, Any],
) -> list[dict[str, Any]]:
    """Push transparent/untagged VLAN UCI to each SU via BTS remote_exec."""
    cmds = build_cpe_untagged_commands(profile_tb)
    if not cmds:
        return []
    inner = " && ".join(cmds)
    escaped = inner.replace("'", "'\"'\"'")
    results: list[dict[str, Any]] = []
    for su_index in range(1, su_count + 1):
        remote = f"/usr/sbin/remote_exec.sh {su_index} '{escaped}'"
        try:
            out = await bts_ssh.send_command(remote, timeout_ops=120)
            text = str(out.result or "").strip()
            ok = "error" not in text.lower()
            results.append({"su_index": su_index, "ok": ok, "output_tail": text[-200:]})
            print(f"[vlan] CPE SU{su_index} vlan apply via remote_exec ok={ok}")
        except Exception as exc:
            results.append({"su_index": su_index, "ok": False, "error": str(exc)})
            print(f"[vlan] CPE SU{su_index} vlan apply failed: {exc}")
    return results


async def apply_vlan_mode_async(
    *,
    profile_active: dict[str, Any],
    bts_vlan_mode: str,
    su_count: int = 4,
) -> dict[str, Any]:
    """
    Apply VLAN on BTS (+ CPE untagged on all SUs).

    ``bts_vlan_mode``: ``qinq`` or ``transparent``.
    CPE data path stays transparent/untagged in both modes (QinQ is BTS-side).
    """
    dut = profile_active.get("dut") or {}
    tb = profile_active.get("testbed") or {}
    user = str(dut.get("username") or "root")
    password = str(dut.get("password") or "")
    bts_host = str(dut.get("ssh_host") or dut.get("local_ip") or "10.0.0.1")
    mode = str(bts_vlan_mode).strip().lower()

    bts_ssh = await _open_bts_ssh(bts_host, user, password)
    try:
        if mode == "qinq":
            bts_ok = await ensure_bts_qinq_ssh(bts_ssh, tb)
        elif mode == "transparent":
            bts_ok = await ensure_bts_transparent_ssh(bts_ssh, tb)
        else:
            raise ValueError(f"Unsupported BTS VLAN mode: {bts_vlan_mode}")

        cpe_results = await _apply_cpe_vlan_via_remote_exec(
            bts_ssh, su_count=su_count, profile_tb=tb
        )
        return {
            "bts_vlan_mode": mode,
            "bts_ok": bts_ok,
            "cpe_results": cpe_results,
            "bts_host": normalize_ip(bts_host.split("/")[0]) if ":" in bts_host else bts_host,
        }
    finally:
        await bts_ssh.close()


def apply_vlan_mode(
    *,
    profile_active: dict[str, Any],
    bts_vlan_mode: str,
    su_count: int = 4,
) -> dict[str, Any]:
    return asyncio.run(
        apply_vlan_mode_async(
            profile_active=profile_active,
            bts_vlan_mode=bts_vlan_mode,
            su_count=su_count,
        )
    )
