"""Functional suite — IPv6 SSH-only; never bootstrap VLAN/link on session start."""

from __future__ import annotations

import asyncio
import shlex
import subprocess

import pytest
from scrapli.driver.generic import AsyncGenericDriver

from utils.net_utils import is_ipv6_literal, normalize_ip


def _ipv6_mgmt_endpoints(profile_bundle, request) -> tuple[str, str, str]:
    """BTS mgmt IPv6, CPE mgmt IPv6, lab PC source-bind IPv6."""
    dut = profile_bundle.active.get("dut", {}) or {}
    mgmt = (profile_bundle.active.get("testbed", {}) or {}).get("mgmt_vlan", {}) or {}

    bts_v6 = normalize_ip(
        (request.config.getoption("--local-ipv6") or "").strip()
        or str(dut.get("local_ipv6") or mgmt.get("ipv6_bts") or "")
    )
    cpe_v6 = normalize_ip(
        (request.config.getoption("--remote-ipv6") or "").strip().split(",")[0]
        or str((dut.get("remote_ipv6s") or [mgmt.get("ipv6_cpe")])[0] or "")
    )
    source_v6 = normalize_ip(
        str(dut.get("bts_pc_ipv6") or mgmt.get("ipv6_bts_pc") or "")
    )
    return bts_v6, cpe_v6, source_v6


async def _open_ipv6_ssh(
    host: str,
    password: str,
    *,
    source_v6: str = "",
    label: str = "device",
) -> AsyncGenericDriver:
    host = normalize_ip(host)
    if not host or not is_ipv6_literal(host):
        raise ValueError(f"{label}: IPv6 management address required, got {host!r}")

    last_error = ""
    for wait_s in (0, 5, 10):
        if wait_s:
            await asyncio.sleep(wait_s)
        transport_options: dict = {}
        if source_v6 and is_ipv6_literal(source_v6):
            transport_options["local_addr"] = normalize_ip(source_v6)
        conn = AsyncGenericDriver(
            host=host,
            auth_username="root",
            auth_password=password,
            auth_strict_key=False,
            transport="asyncssh",
            transport_options=transport_options or None,
        )
        try:
            await asyncio.wait_for(conn.open(), timeout=20)
            return conn
        except Exception as exc:
            last_error = str(exc)
            try:
                await conn.close()
            except Exception:
                pass
    raise ConnectionError(f"{label} IPv6 SSH failed for {host}: {last_error}")


async def _open_factory_ssh(
    host: str,
    password: str,
    *,
    label: str = "device",
) -> AsyncGenericDriver:
    host = normalize_ip(host)
    last_error = ""
    for wait_s in (0, 5, 10):
        if wait_s:
            await asyncio.sleep(wait_s)
        conn = AsyncGenericDriver(
            host=host,
            auth_username="root",
            auth_password=password,
            auth_strict_key=False,
            transport="asyncssh",
        )
        try:
            await conn.open()
            return conn
        except Exception as exc:
            last_error = str(exc)
            try:
                await conn.close()
            except Exception:
                pass
    raise ConnectionError(f"{label} factory SSH failed for {host}: {last_error}")


class FunctionalBtsSsh:
    """Mutable BTS SSH handle — reopens after cases that drop the session (e.g. FT_02 RAM stress)."""

    def __init__(self, conn: AsyncGenericDriver, *, reopen_cb) -> None:
        self._conn = conn
        self._reopen_cb = reopen_cb

    async def ensure_open(self) -> None:
        try:
            await self._conn.send_command("true", timeout_ops=10)
            return
        except Exception:
            pass
        try:
            await self._conn.close()
        except Exception:
            pass
        self._conn = await self._reopen_cb()
        print("[functional] BTS SSH session reopened after disconnect")

    async def send_command(self, *args, **kwargs):
        await self.ensure_open()
        return await self._conn.send_command(*args, **kwargs)

    async def close(self):
        return await self._conn.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)


def pytest_collection_modifyitems(config, items):
    if any(item.get_closest_marker("Functional") for item in items):
        config.option.skip_testbed_bootstrap = True


def _run_local(command: str) -> tuple[int, str]:
    proc = subprocess.run(command, shell=True, capture_output=True, text=True)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


async def _functional_reset_pc_lan(profile: dict) -> None:
    """Functional cases require untagged PC LAN; clear stale iface.VLAN links before SSH."""
    tb = profile.get("testbed", {}) or {}
    dut = profile.get("dut", {}) or {}
    primary = tb.get("primary_pc", {}) or {}
    if not primary.get("local", True):
        return

    iface = str(primary.get("mgmt_interface") or "enp1s0").strip() or "enp1s0"
    fallback = str(primary.get("fallback_ipv4") or "192.168.2.200").split("/")[0].strip()
    source_v6 = normalize_ip(str(dut.get("bts_pc_ipv6") or (tb.get("mgmt_vlan", {}) or {}).get("ipv6_bts_pc") or ""))
    iface_q = shlex.quote(iface)
    prefix_q = shlex.quote(f"{iface}.")
    cmds = [
        (
            "for vlan_if in $(ip -o link show | awk -F': ' "
            f"'$2 ~ /^{prefix_q}/ {{print $2}}' | cut -d@ -f1 | sort -r); do "
            'ip link set "$vlan_if" down 2>/dev/null || true; '
            'ip link del "$vlan_if" 2>/dev/null || true; '
            "done"
        ),
        f"ip link set {iface_q} up",
        f"ip addr add {shlex.quote(fallback)}/24 dev {iface_q} 2>/dev/null || true",
        f"ip addr add 10.0.0.200/24 dev {iface_q} 2>/dev/null || true",
        f"ip route replace 192.168.2.0/24 dev {iface_q} src {shlex.quote(fallback)}",
        f"ip route replace 10.0.0.0/24 dev {iface_q} src 10.0.0.200",
        f"ip neigh flush dev {iface_q} >/dev/null 2>&1 || true",
    ]
    if source_v6:
        cmds.insert(4, f"ip -6 addr add {shlex.quote(source_v6)}/120 dev {iface_q} 2>/dev/null || true")
    joined = " && ".join(cmds)
    rc, out = await asyncio.to_thread(_run_local, f"sudo -n sh -c {shlex.quote(joined)}")
    if rc != 0:
        print(f"[functional] PC LAN untagged cleanup failed on {iface}: {out[:220]}")
    else:
        print(f"[functional] PC LAN untagged cleanup OK on {iface}")


@pytest.fixture(scope="session")
async def testbed_ready(request, profile_bundle, device_creds):
    """Never run bootstrap_testbed for Functional — preserves mgmt IPv6 routing."""
    del device_creds, request
    return profile_bundle


@pytest.fixture(scope="session")
def bsu_ip(request, profile_bundle):
    bts_v6, _, _ = _ipv6_mgmt_endpoints(profile_bundle, request)
    if not bts_v6 or not is_ipv6_literal(bts_v6):
        pytest.fail("[functional] BTS mgmt IPv6 required (--local-ipv6 or profile dut.local_ipv6)")
    return bts_v6


@pytest.fixture(scope="session")
def cpe_ips(request, profile_bundle):
    _, cpe_v6, _ = _ipv6_mgmt_endpoints(profile_bundle, request)
    if not cpe_v6 or not is_ipv6_literal(cpe_v6):
        pytest.fail("[functional] CPE mgmt IPv6 required (--remote-ipv6 or profile dut.remote_ipv6s)")
    raw = (request.config.getoption("--remote-ipv6") or "").strip()
    if raw:
        return [normalize_ip(ip.strip()) for ip in raw.split(",") if ip.strip()]
    return [cpe_v6]


@pytest.fixture(autouse=True)
async def _functional_ensure_bts_ssh_before_each(request, root_ssh):
    """Re-open BTS SSH if a prior case dropped the transport."""
    if request.node.get_closest_marker("Functional") is None:
        return
    if isinstance(root_ssh, FunctionalBtsSsh):
        await root_ssh.ensure_open()


@pytest.fixture(scope="session")
async def root_ssh(request, bsu_ip, device_creds, profile_bundle):
    """
    SSH to BTS: mgmt IPv6 first; factory hop 10.0.0.1 when mgmt is down (post-reset lab).
    """
    from config.defaults import FUNCTIONAL_TEST_VALUES

    _, _, source_v6 = _ipv6_mgmt_endpoints(profile_bundle, request)
    password = device_creds["pass"]
    factory_hop = normalize_ip(
        str(FUNCTIONAL_TEST_VALUES.get("ft07_bts_hop_ipv4", "10.0.0.1"))
    )
    async def _reopen_bts_ssh() -> AsyncGenericDriver:
        try:
            return await _open_ipv6_ssh(
                bsu_ip,
                password,
                source_v6=source_v6,
                label="BTS",
            )
        except ConnectionError as exc:
            print(f"[functional] BTS mgmt IPv6 unavailable ({exc}); fallback → {factory_hop}")
            return await _open_factory_ssh(factory_hop, password, label="BTS")

    conn = None
    try:
        print(
            f"\n[functional] IPv6 SSH → BTS {bsu_ip}"
            + (f" (bind {source_v6})" if source_v6 else "")
            + " — no bootstrap / no link changes"
        )
        await _functional_reset_pc_lan(profile_bundle.active)
        conn = await _reopen_bts_ssh()
    except ConnectionError:
        if not request.config.getoption("--allow-functional"):
            raise
        conn = await _open_factory_ssh(factory_hop, password, label="BTS")
    wrapper = FunctionalBtsSsh(conn, reopen_cb=_reopen_bts_ssh)
    try:
        yield wrapper
    finally:
        try:
            await wrapper.close()
        except Exception:
            pass
