"""
DHCPv4 & DHCPv6 suite isolation.

- Skips shared mgmt-VLAN / testbed bootstrap so Functional/Sanity topology is not rewired.
- Own BTS root_ssh: lab IPv4 (192.168.2.10) + mgmt IPv6; no root recovery / SSID wifi-reload.
- Re-opens BTS SSH before every case (BTS reboot/PDU/factory drop the session fixture).
- Does not touch CPE 2.4 GHz mgmt DHCP (lan24) used by Radio24 / API.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from scrapli.driver.generic import AsyncGenericDriver

from utils.net_utils import is_ipv6_literal, normalize_ip
from utils.parsers import clean_ssh_output


def pytest_collection_modifyitems(config, items):
    if any(item.get_closest_marker("DHCPv4_DHCPv6") for item in items):
        config.option.skip_testbed_bootstrap = True


@pytest.fixture(scope="session")
async def testbed_ready(profile_bundle):
    print(
        "\n[DHCPv4_DHCPv6] Skipping testbed bootstrap — "
        "suite owns BTS DHCP server / CPE data client restore"
    )
    return profile_bundle


async def _open_bts_scrapli(host: str, username: str, password: str) -> AsyncGenericDriver:
    """Open scrapli SSH; treat session as good if a short command returns without error."""
    host = normalize_ip(host)
    conn = AsyncGenericDriver(
        host=host,
        auth_username=username,
        auth_password=password,
        auth_strict_key=False,
        transport="asyncssh",
        transport_options={"asyncssh": {"known_hosts": None}},
        timeout_socket=12,
        timeout_transport=12,
        timeout_ops=20,
    )
    await asyncio.wait_for(conn.open(), timeout=20)
    # BusyBox MOTD can swallow long echoes — use a short marker + clean_ssh_output.
    resp = await conn.send_command("echo DHS1")
    raw = clean_ssh_output(str(getattr(resp, "result", "") or ""))
    combined = str(getattr(resp, "result", "") or "") + "\n" + raw
    if "DHS1" not in combined and not raw:
        await conn.close()
        raise RuntimeError(f"SSH open but empty probe on {host}: {combined[-80:]!r}")
    return conn


class DhcpSuiteBtsSsh:
    """
    Mutable BTS SSH handle for the full suite.

    Cases that reboot / power-cycle / factory-reset BTS close the session
    scrapli object. The next send_command() (or autouse ensure_open) reopens it
    so later cases are not 0s ScrapliConnectionNotOpened failures.
    """

    def __init__(self, conn: AsyncGenericDriver, *, reopen_cb) -> None:
        self._conn = conn
        self._reopen_cb = reopen_cb

    @property
    def host(self) -> str:
        return str(getattr(self._conn, "host", "") or getattr(self._conn, "_host", "") or "")

    async def ensure_open(self, *, timeout_s: int = 180) -> None:
        try:
            await self._conn.send_command("echo DHS1", timeout_ops=8)
            # Channel is live if send_command returns (BusyBox MOTD may hide DHS1).
            return
        except Exception:
            pass
        await self._reopen(timeout_s=timeout_s)

    async def _reopen(self, *, timeout_s: int = 180) -> None:
        try:
            await self._conn.close()
        except Exception:
            pass
        deadline = time.monotonic() + max(20, timeout_s)
        last_exc: Exception | None = None
        while time.monotonic() < deadline:
            try:
                self._conn = await self._reopen_cb()
                print(
                    f"[DHCPv4_DHCPv6] BTS SSH reopened on "
                    f"{getattr(self._conn, 'host', '?')} (suite continue)"
                )
                return
            except Exception as exc:
                last_exc = exc
                await asyncio.sleep(5)
        raise ConnectionError(
            f"[DHCPv4_DHCPv6] Could not reopen BTS SSH within {timeout_s}s: {last_exc}"
        )

    async def send_command(self, *args, **kwargs):
        # Do not reopen here: callers wrap send_command in short wait_for()
        # and would cancel a 180s reconnect. _ssh/_ssh_raw retry after ensure_open().
        return await self._conn.send_command(*args, **kwargs)

    async def close(self):
        try:
            await self._conn.close()
        except Exception:
            pass

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _bts_ssh_candidates(bsu_ip, profile_bundle) -> list[str]:
    active = profile_bundle.active
    dut = active.get("dut") or {}
    mgmt = (active.get("testbed") or {}).get("mgmt_vlan") or {}
    candidates: list[str] = []
    for host in (
        "192.168.2.10",  # current lab static BTS
        bsu_ip,
        dut.get("local_ipv6"),
        mgmt.get("ipv6_bts"),
        dut.get("local_ip"),
        "10.0.0.1",  # factory fallback (optional)
    ):
        h = normalize_ip(str(host or ""))
        if h and h not in candidates:
            candidates.append(h)
    return candidates


@pytest.fixture(scope="session")
async def root_ssh(bsu_ip, device_creds, profile_bundle):
    """
    Lightweight BTS SSH for this suite only.

    Prefer lab data IPv4 192.168.2.10 (current bench), then profile IPv6,
    then legacy factory 10.0.0.1 last (often absent after DHCP lab runs).
    """
    password = str(device_creds.get("pass") or "")
    username = str(device_creds.get("user") or "root")
    candidates = _bts_ssh_candidates(bsu_ip, profile_bundle)

    async def _reopen_bts_ssh() -> AsyncGenericDriver:
        last_exc: Exception | None = None
        for host in candidates:
            try:
                kind = "IPv6" if is_ipv6_literal(host) else "IPv4"
                print(f"[DHCPv4_DHCPv6] BTS SSH try → {host} ({kind})")
                conn = await _open_bts_scrapli(host, username, password)
                print(f"[DHCPv4_DHCPv6] BTS SSH open on {host} (no link SSID change)")
                return conn
            except Exception as exc:
                last_exc = exc
                print(f"[DHCPv4_DHCPv6] BTS SSH failed on {host}: {exc}")
        raise ConnectionError(
            f"[DHCPv4_DHCPv6] Could not open BTS SSH via {candidates}: {last_exc}"
        )

    conn = await _reopen_bts_ssh()
    wrapper = DhcpSuiteBtsSsh(conn, reopen_cb=_reopen_bts_ssh)
    try:
        yield wrapper
    finally:
        try:
            await wrapper.close()
        except Exception:
            pass


@pytest.fixture(autouse=True)
async def _dhcp_suite_bts_ssh_before_each(request, root_ssh):
    """Re-open BTS SSH if a prior case dropped the transport (reboot / PDU / factory)."""
    if request.node.get_closest_marker("DHCPv4_DHCPv6") is None:
        return
    if isinstance(root_ssh, DhcpSuiteBtsSsh):
        await root_ssh.ensure_open()
