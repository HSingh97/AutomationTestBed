"""Sanity-only Network config (IP Configuration, mgmt VLAN)."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass

from pages.locators import NetworkLocators, UITimeouts
from utils.management_flows import apply_triple
from utils.net_utils import format_luci_url, is_ipv6_literal, normalize_ip
from utils.network_flows import _goto_admin_path, open_network_submenu
from utils.sanity_commands import SanityCommands
from utils.sanity_gui import resolve_luci_stok, sanity_login_if_needed
from utils.sanity_ssh import close_sanity_ssh, ensure_sanity_ssh_open, open_sanity_ssh, sanity_ssh_run
from utils.ui_helpers import attach_dialog_handler, fill_luci_input, read_luci_input_value


@dataclass(frozen=True)
class SanityNetworkSnapshot:
    proto: str
    ipaddr: str
    netmask: str
    gateway: str
    ip6proto: str
    ip6addr: str
    ip6gw: str
    mgmtvlan: str
    vlan_mode: str


def derive_sanity_alt_ipv4(ipv4_raw: str, *, role: str) -> str:
    raw = normalize_ip(str(ipv4_raw or "").split("/")[0])
    try:
        ip = ipaddress.IPv4Address(raw)
    except Exception:
        return "192.168.2.210" if role.upper() == "BTS" else "192.168.2.211"
    step = 10 if role.upper() == "BTS" else 11
    alt = int(ip) + step
    if alt > int(ipaddress.IPv4Address("255.255.255.254")):
        alt = int(ip) - step
    if alt <= 0:
        alt = int(ip)
    return str(ipaddress.IPv4Address(alt))


def derive_sanity_alt_ipv6(ipv6_cidr_raw: str, *, role: str) -> str:
    raw = str(ipv6_cidr_raw or "").strip()
    if not raw:
        raise ValueError("IPv6 address is empty")
    if "/" in raw:
        addr_part, prefix_part = raw.split("/", 1)
        prefix_len = int(prefix_part.strip())
    else:
        addr_part = raw
        prefix_len = 120
    addr_part = normalize_ip(addr_part.split("/")[0])
    try:
        iface = ipaddress.IPv6Interface(f"{addr_part}/{prefix_len}")
    except Exception:
        iface = ipaddress.IPv6Interface(f"{addr_part}/120")
    step = 10 if role.upper() == "BTS" else 11
    alt_int = int(iface.ip) + step
    network = iface.network
    if ipaddress.IPv6Address(alt_int) not in network:
        alt_int = int(iface.ip) - step
    alt_ip = ipaddress.IPv6Address(alt_int)
    if alt_ip == iface.ip:
        alt_ip = iface.ip + 1
    return f"{alt_ip.compressed}/{iface.network.prefixlen}"


def derive_sanity_alt_mgmt_vlan(current: str) -> int:
    try:
        base = int(str(current or "1").strip())
    except Exception:
        base = 1
    for candidate in (101, 102, 103, 201, 301):
        if candidate != base:
            return candidate
    return 201 if base != 201 else 202


async def open_sanity_ip_config(
    gui_page,
    *,
    host: str = "",
    device_creds: dict | None = None,
) -> None:
    """Network → IP Configuration."""
    proto = gui_page.locator(
        f"{NetworkLocators.IPv4_PROTO}, "
        "select[name*='.proto'], "
        "select[id*='proto']"
    ).first
    ipv6_addr = gui_page.locator(NetworkLocators.IPv6_ADDRESS).first

    async def _form_visible() -> bool:
        for loc in (proto, ipv6_addr, gui_page.locator(NetworkLocators.IPv4_ADDRESS).first):
            try:
                if await loc.is_visible(timeout=3000):
                    return True
            except Exception:
                continue
        return False

    async def _goto_ip() -> None:
        if not host:
            return
        url = f"{format_luci_url(host).rstrip('/')}/admin/network/ip"
        await gui_page.goto(url, timeout=60000, wait_until="domcontentloaded")
        if device_creds:
            await sanity_login_if_needed(gui_page, host, device_creds, wait_ms=5000)
            stok = await resolve_luci_stok(gui_page)
            if stok:
                head = format_luci_url(host).rstrip("/")
                url = f"{head}/{stok}/admin/network/ip"
                await gui_page.goto(url, timeout=60000, wait_until="domcontentloaded")
                await sanity_login_if_needed(gui_page, host, device_creds, wait_ms=3000)
            elif "network/ip" not in (gui_page.url or "").lower():
                await gui_page.goto(url, timeout=60000, wait_until="domcontentloaded")
                await sanity_login_if_needed(gui_page, host, device_creds, wait_ms=3000)
        await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)

    if host:
        await _goto_ip()
        if await _form_visible():
            return
        print("[sanity] IP config form missing — retry after re-auth", flush=True)
        if device_creds:
            await sanity_login_if_needed(gui_page, host, device_creds, wait_ms=5000)
        await _goto_ip()
        if await _form_visible():
            return
    try:
        await open_network_submenu(gui_page, "/network/ip")
    except Exception:
        if host:
            await _goto_ip()
        elif await _goto_admin_path(gui_page, "/network/ip"):
            pass
        else:
            raise
    await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)
    if not await _form_visible():
        await proto.wait_for(state="visible", timeout=30000)


async def read_sanity_network_backend(ssh) -> SanityNetworkSnapshot:
    await ensure_sanity_ssh_open(ssh)

    async def _get(cmd: str) -> str:
        return (await sanity_ssh_run(ssh, cmd, timeout_s=30)).strip()

    return SanityNetworkSnapshot(
        proto=await _get(SanityCommands.GET_NET_PROTO),
        ipaddr=await _get(SanityCommands.GET_NET_IP),
        netmask=await _get(SanityCommands.GET_NET_MASK),
        gateway=await _get(SanityCommands.GET_NET_GW),
        ip6proto=await _get("uci -q get network.lan.ip6proto"),
        ip6addr=await _get(SanityCommands.GET_NET_IP6),
        ip6gw=await _get(SanityCommands.GET_NET_GW6),
        mgmtvlan=await _get(SanityCommands.GET_VLAN_MGMT),
        vlan_mode=await _get(SanityCommands.GET_VLAN_MODE),
    )


async def _select_ipv4_proto(gui_page, *, want: str) -> str:
    element = gui_page.locator(NetworkLocators.IPv4_PROTO).first
    await element.wait_for(state="visible", timeout=30000)
    options = await element.evaluate(
        "el => Array.from(el.options).map(o => ({text: (o.text||'').trim(), value: (o.value||'').trim()}))"
    )
    for opt in options:
        text_l = opt["text"].lower()
        value_l = opt["value"].lower()
        if want == "dhcp" and (value_l == "dhcp" or ("dynamic" in text_l and "ipv4" in text_l)):
            await element.select_option(value=opt["value"])
            return opt["text"] or opt["value"]
        if want == "static" and (value_l == "static" or ("static" in text_l and "ipv4" in text_l)):
            await element.select_option(value=opt["value"])
            return opt["text"] or opt["value"]
    raise RuntimeError(f"[sanity] {want} IPv4 option not found: {options}")


async def _select_ipv6_proto(gui_page, *, want: str) -> str:
    element = gui_page.locator("select[name*='network.lan.ip6proto']").first
    if not await element.count():
        return want
    await element.wait_for(state="visible", timeout=20000)
    options = await element.evaluate(
        "el => Array.from(el.options).map(o => ({text: (o.text||'').trim(), value: (o.value||'').trim()}))"
    )
    for opt in options:
        text_l = opt["text"].lower()
        value_l = opt["value"].lower()
        if want == "dhcp" and ("dhcp" in value_l or "dynamic" in text_l):
            await element.select_option(value=opt["value"])
            return opt["text"] or opt["value"]
        if want == "static" and (value_l == "static" or "static" in text_l):
            await element.select_option(value=opt["value"])
            return opt["text"] or opt["value"]
    for opt in ("static", "dhcp"):
        try:
            await element.select_option(value=opt)
            return opt
        except Exception:
            continue
    raise RuntimeError(f"[sanity] {want} IPv6 option not found: {options}")


async def _apply_ip_config_gui(gui_page, *, settle_seconds: int = 25) -> None:
    attach_dialog_handler(gui_page)
    await apply_triple(
        gui_page,
        NetworkLocators.SAVE_BUTTON,
        NetworkLocators.APPLY_ICON,
        NetworkLocators.CONFIRM_APPLY,
        settle_seconds=settle_seconds,
    )


async def apply_sanity_static_ipv4_gui(
    gui_page,
    *,
    ipaddr: str,
    netmask: str = "",
    gateway: str = "",
    host: str = "",
    device_creds: dict | None = None,
    settle_seconds: int = 25,
) -> None:
    await open_sanity_ip_config(gui_page, host=host, device_creds=device_creds)
    await _select_ipv4_proto(gui_page, want="static")
    if ipaddr:
        await fill_luci_input(gui_page, NetworkLocators.IPv4_ADDRESS, ipaddr)
    if netmask:
        await fill_luci_input(gui_page, NetworkLocators.IPv4_NETMASK, netmask)
    if gateway:
        await fill_luci_input(gui_page, NetworkLocators.IPv4_GATEWAY, gateway)
    await _apply_ip_config_gui(gui_page, settle_seconds=settle_seconds)


async def apply_sanity_static_ipv6_gui(
    gui_page,
    *,
    ip6addr: str,
    ip6gw: str = "",
    host: str = "",
    device_creds: dict | None = None,
    settle_seconds: int = 25,
) -> None:
    await open_sanity_ip_config(gui_page, host=host, device_creds=device_creds)
    await _select_ipv6_proto(gui_page, want="static")
    if ip6addr:
        await fill_luci_input(gui_page, NetworkLocators.IPv6_ADDRESS, ip6addr)
    if ip6gw:
        await fill_luci_input(gui_page, NetworkLocators.IPv6_GATEWAY, ip6gw)
    await _apply_ip_config_gui(gui_page, settle_seconds=settle_seconds)


async def apply_sanity_static_ipv6_ssh(
    ssh,
    *,
    ip6addr: str,
    ip6gw: str = "",
    settle_seconds: int = 15,
) -> None:
    """Set static IPv6 via UCI + network reload (emergency restore / no-GUI path)."""
    import asyncio
    import shlex

    cidr = str(ip6addr or "").strip()
    if not cidr:
        raise ValueError("ip6addr required")
    if "/" not in cidr:
        cidr = f"{normalize_ip(cidr)}/120"
    gw = str(ip6gw or "").strip()
    parts = [
        "uci set network.lan.ip6proto='static'",
        f"uci set network.lan.ip6addr={shlex.quote(cidr)}",
    ]
    if gw:
        parts.append(f"uci set network.lan.ip6gw={shlex.quote(gw)}")
    else:
        parts.append("uci delete network.lan.ip6gw 2>/dev/null || true")
    parts.extend(
        [
            "uci commit network",
            "/etc/init.d/network reload >/dev/null 2>&1 || true",
            "echo IP6_APPLY_DONE",
        ]
    )
    batch = " ; ".join(parts)
    try:
        await sanity_ssh_run(ssh, batch, timeout_s=25)
    except Exception:
        # Reload often drops the SSH session — expected.
        pass
    if settle_seconds > 0:
        await asyncio.sleep(settle_seconds)


def sanity_proto_is_dynamic(proto: str) -> bool:
    p = str(proto or "").strip().lower()
    return p in ("dhcp", "dynamic", "auto")


def needs_static_dual_stack_apply(snap: SanityNetworkSnapshot) -> bool:
    return sanity_proto_is_dynamic(snap.proto) or sanity_proto_is_dynamic(snap.ip6proto)


async def resolve_static_dual_stack_values(
    ssh,
    snap: SanityNetworkSnapshot,
    *,
    role: str,
    fallback_ipv6: str = "",
) -> dict[str, str]:
    """Pin live br-lan addresses as static when converting from DHCP."""
    live_v4 = await read_live_ipv4(ssh)
    live_v6 = await read_live_ipv6(ssh)
    uci_v4 = normalize_ip(str(snap.ipaddr or "").split("/")[0])
    # Prefer UCI lab address when live still lists factory 10.0.0.1 first.
    if uci_v4 and live_v4 and live_v4.startswith("10.0.0.") and not uci_v4.startswith("10."):
        ipaddr = uci_v4
    else:
        ipaddr = live_v4 or uci_v4
    if not ipaddr:
        ipaddr = "192.168.2.210" if role.upper() == "BTS" else "192.168.2.211"
    netmask = str(snap.netmask or "255.255.255.0").strip() or "255.255.255.0"
    gateway = normalize_ip(str(snap.gateway or "").split("/")[0])
    ip6addr = live_v6 or str(snap.ip6addr or "").strip()
    if not ip6addr and fallback_ipv6:
        prefix = 120
        if "/" in fallback_ipv6:
            ip6addr = fallback_ipv6
        else:
            ip6addr = f"{normalize_ip(fallback_ipv6)}/{prefix}"
    ip6gw = str(snap.ip6gw or "").strip()
    return {
        "ipaddr": ipaddr,
        "netmask": netmask,
        "gateway": gateway,
        "ip6addr": ip6addr,
        "ip6gw": ip6gw,
    }


async def apply_sanity_static_dual_stack_gui(
    gui_page,
    *,
    ipaddr: str,
    netmask: str = "",
    gateway: str = "",
    ip6addr: str = "",
    ip6gw: str = "",
    host: str = "",
    device_creds: dict | None = None,
    settle_seconds: int = 25,
) -> None:
    """Network → IP: static IPv4 + static IPv6 together (dual stack)."""
    await open_sanity_ip_config(gui_page, host=host, device_creds=device_creds)
    await _select_ipv4_proto(gui_page, want="static")
    if ipaddr:
        await fill_luci_input(gui_page, NetworkLocators.IPv4_ADDRESS, ipaddr)
    if netmask:
        await fill_luci_input(gui_page, NetworkLocators.IPv4_NETMASK, netmask)
    if gateway:
        await fill_luci_input(gui_page, NetworkLocators.IPv4_GATEWAY, gateway)
    await _select_ipv6_proto(gui_page, want="static")
    if ip6addr:
        await fill_luci_input(gui_page, NetworkLocators.IPv6_ADDRESS, ip6addr)
    if ip6gw:
        await fill_luci_input(gui_page, NetworkLocators.IPv6_GATEWAY, ip6gw)
    await _apply_ip_config_gui(gui_page, settle_seconds=settle_seconds)


async def apply_sanity_ipv4_dhcp_gui(
    gui_page,
    *,
    host: str = "",
    device_creds: dict | None = None,
    settle_seconds: int = 25,
) -> None:
    await open_sanity_ip_config(gui_page, host=host, device_creds=device_creds)
    await _select_ipv4_proto(gui_page, want="dhcp")
    await _apply_ip_config_gui(gui_page, settle_seconds=settle_seconds)


async def apply_sanity_ipv6_dhcp_gui(
    gui_page,
    *,
    host: str = "",
    device_creds: dict | None = None,
    settle_seconds: int = 25,
) -> None:
    await open_sanity_ip_config(gui_page, host=host, device_creds=device_creds)
    await _select_ipv6_proto(gui_page, want="dhcp")
    await _apply_ip_config_gui(gui_page, settle_seconds=settle_seconds)


async def restore_sanity_network_snapshot_gui(
    gui_page,
    snap: SanityNetworkSnapshot,
    *,
    host: str = "",
    device_creds: dict | None = None,
    settle_seconds: int = 25,
) -> None:
    """Restore IP Configuration from a backend snapshot."""
    await open_sanity_ip_config(gui_page, host=host, device_creds=device_creds)
    proto = (snap.proto or "static").strip().lower()
    if proto == "dhcp":
        await _select_ipv4_proto(gui_page, want="dhcp")
    else:
        await _select_ipv4_proto(gui_page, want="static")
        if snap.ipaddr:
            await fill_luci_input(gui_page, NetworkLocators.IPv4_ADDRESS, snap.ipaddr.split("/")[0])
        if snap.netmask:
            await fill_luci_input(gui_page, NetworkLocators.IPv4_NETMASK, snap.netmask)
        if snap.gateway:
            await fill_luci_input(gui_page, NetworkLocators.IPv4_GATEWAY, snap.gateway.split("/")[0])

    ip6proto = (snap.ip6proto or "static").strip().lower()
    try:
        if ip6proto == "dhcp":
            await _select_ipv6_proto(gui_page, want="dhcp")
        else:
            await _select_ipv6_proto(gui_page, want="static")
            if snap.ip6addr:
                await fill_luci_input(gui_page, NetworkLocators.IPv6_ADDRESS, snap.ip6addr)
            if snap.ip6gw:
                await fill_luci_input(gui_page, NetworkLocators.IPv6_GATEWAY, snap.ip6gw)
    except Exception:
        pass

    await _apply_ip_config_gui(gui_page, settle_seconds=settle_seconds)


async def read_sanity_ipv4_gui(gui_page, *, host: str = "", device_creds: dict | None = None) -> str:
    await open_sanity_ip_config(gui_page, host=host, device_creds=device_creds)
    return (await read_luci_input_value(gui_page, NetworkLocators.IPv4_ADDRESS)).strip()


async def read_sanity_ipv6_gui(gui_page, *, host: str = "", device_creds: dict | None = None) -> str:
    await open_sanity_ip_config(gui_page, host=host, device_creds=device_creds)
    return (await read_luci_input_value(gui_page, NetworkLocators.IPv6_ADDRESS)).strip()


async def verify_sanity_ipv4_applied(
    gui_page,
    ssh,
    expected_ip: str,
    *,
    host: str = "",
    device_creds: dict | None = None,
) -> tuple[bool, bool, bool]:
    """Verify IPv4 change via UCI, live br-lan, and GUI field (mgmt session)."""
    await ensure_sanity_ssh_open(ssh)
    backend = await read_sanity_network_backend(ssh)
    backend_ok = uci_ipv4_matches(expected_ip, backend.ipaddr)
    live_ok = await br_lan_has_ipv4(ssh, expected_ip)
    try:
        gui_val = await read_sanity_ipv4_gui(gui_page, host=host, device_creds=device_creds)
        gui_ok = uci_ipv4_matches(expected_ip, gui_val)
    except Exception:
        gui_ok = backend_ok
    return backend_ok, live_ok, gui_ok


async def verify_sanity_ipv6_applied(
    gui_page,
    ssh,
    expected_ip6: str,
    *,
    host: str = "",
    device_creds: dict | None = None,
) -> tuple[bool, bool, bool]:
    await ensure_sanity_ssh_open(ssh)
    backend = await read_sanity_network_backend(ssh)
    backend_ok = uci_ipv6_matches(expected_ip6, backend.ip6addr)
    live_ok = uci_ipv6_matches(expected_ip6, await read_live_ipv6(ssh))
    if not live_ok:
        import asyncio

        for _ in range(4):
            await asyncio.sleep(3)
            if uci_ipv6_matches(expected_ip6, await read_live_ipv6(ssh)):
                live_ok = True
                break
    try:
        gui_val = await read_sanity_ipv6_gui(gui_page, host=host, device_creds=device_creds)
        gui_ok = uci_ipv6_matches(expected_ip6, gui_val)
    except Exception:
        gui_ok = backend_ok
    return backend_ok, live_ok, gui_ok


async def reopen_sanity_mgmt_ssh(
    ssh,
    mgmt_hosts: str | list[str],
    password: str,
    *,
    source_v6: str = "",
    label: str = "device",
    timeout_s: int = 120,
    poll_s: int = 10,
):
    from utils.sanity_ssh import wait_sanity_ssh

    if isinstance(mgmt_hosts, str):
        hosts = [normalize_ip(str(h).split("/")[0]) for h in [mgmt_hosts] if h]
    else:
        hosts = [normalize_ip(str(h).split("/")[0]) for h in mgmt_hosts if h]
    # De-dupe while preserving order.
    seen: set[str] = set()
    hosts = [h for h in hosts if h and not (h in seen or seen.add(h))]

    last_error = ""
    new_conn = None
    n = max(1, len(hosts))
    for idx, host in enumerate(hosts):
        # Primary host gets the full budget; fallbacks get a shorter window.
        host_timeout = int(timeout_s) if idx == 0 else min(120, max(45, int(timeout_s) // n))
        bind = source_v6 if is_ipv6_literal(host) else ""
        try:
            if idx > 0:
                print(f"[sanity] {label}: fallback SSH → {host}", flush=True)
            new_conn = await wait_sanity_ssh(
                host,
                password,
                source_v6=bind,
                label=label,
                timeout_s=host_timeout,
                poll_s=poll_s,
            )
            break
        except Exception as exc:
            last_error = str(exc)
    if new_conn is None:
        raise ConnectionError(f"[sanity] SSH failed for hosts {hosts}: {last_error}")

    # Session wrapper (SanityBtsSsh): adopt new transport without orphaning the fixture.
    replace = getattr(ssh, "replace_connection", None)
    if callable(replace):
        await replace(new_conn)
        return ssh

    await close_sanity_ssh(ssh)
    return new_conn


async def verify_sanity_ssh_at_host(
    host: str,
    password: str,
    *,
    source_v6: str = "",
    label: str = "device",
    timeout_s: int = 90,
) -> bool:
    host = normalize_ip(host.split("/")[0])
    try:
        conn = await open_sanity_ssh(
            host,
            password,
            source_v6=source_v6 if is_ipv6_literal(host) else "",
            label=label,
            timeout_s=min(timeout_s, 45),
        )
        await close_sanity_ssh(conn)
        return True
    except Exception:
        return False


async def read_live_ipv4(ssh) -> str:
    """Return preferred IPv4 on br-lan.

    CPE often keeps factory ``10.0.0.1`` alongside lab ``192.168.2.11`` after hop
    restore. Prefer the lab/UCI-style address so dual-stack pings hit the PC LAN.
    """
    raw = await sanity_ssh_run(
        ssh,
        "ip -4 -o addr show dev br-lan 2>/dev/null | awk '{print $4}'",
        timeout_s=20,
    )
    addrs: list[str] = []
    for line in (raw or "").splitlines():
        ip = normalize_ip(str(line).split("/")[0])
        if ip and ip not in addrs:
            addrs.append(ip)
    if not addrs:
        return ""
    for ip in addrs:
        if ip.startswith("192.168."):
            return ip
    for ip in addrs:
        if not ip.startswith("10.0.0."):
            return ip
    return addrs[0]


async def br_lan_has_ipv4(ssh, expected_ip: str, *, retries: int = 5, delay_s: float = 3.0) -> bool:
    import asyncio

    target = normalize_ip(expected_ip.split("/")[0])
    for attempt in range(retries):
        raw = await sanity_ssh_run(
            ssh,
            "ip -4 -o addr show dev br-lan 2>/dev/null | awk '{print $4}'",
            timeout_s=20,
        )
        for line in (raw or "").splitlines():
            if uci_ipv4_matches(target, line):
                return True
        if attempt + 1 < retries:
            await asyncio.sleep(delay_s)
    return False


async def read_live_ipv6(ssh) -> str:
    raw = await sanity_ssh_run(
        ssh,
        "ip -6 -o addr show dev br-lan scope global 2>/dev/null | awk '{print $4}' | head -1",
        timeout_s=20,
    )
    return raw.strip()


def uci_ipv4_matches(expected: str, actual: str) -> bool:
    return normalize_ip(expected.split("/")[0]) == normalize_ip(actual.split("/")[0])


def uci_ipv6_matches(expected: str, actual: str) -> bool:
    exp = str(expected or "").strip()
    act = str(actual or "").strip()
    if not exp or not act:
        return exp == act
    try:
        return ipaddress.IPv6Interface(exp) == ipaddress.IPv6Interface(act)
    except Exception:
        return normalize_ip(exp.split("/")[0]) == normalize_ip(act.split("/")[0])


async def open_sanity_vlan_radio_config(
    gui_page,
    *,
    host: str = "",
    device_creds: dict | None = None,
) -> None:
    """Network → VLAN → Radio tab."""
    vlan_url = ""
    if host:
        vlan_url = f"{format_luci_url(host).rstrip('/')}/admin/network/vlan"
        await gui_page.goto(vlan_url, timeout=60000, wait_until="domcontentloaded")
        if device_creds:
            await sanity_login_if_needed(gui_page, host, device_creds)
        await gui_page.goto(vlan_url, timeout=60000, wait_until="domcontentloaded")
        if device_creds:
            await sanity_login_if_needed(gui_page, host, device_creds)
    else:
        opened = False
        for fragment in ("/network/vlan", "/network/vlan24"):
            try:
                await open_network_submenu(gui_page, fragment)
                opened = True
                break
            except Exception:
                continue
        if not opened and not await _goto_admin_path(gui_page, "/network/vlan"):
            raise RuntimeError("[sanity] could not open Network → VLAN page")

    await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)
    mgmt_probe = gui_page.locator(NetworkLocators.VLAN_MGMT_INPUT).first
    for tab_sel in (
        "ul.cbi-tabmenu > li > a[href*='vlan24']",
        "ul.cbi-tabmenu > li > a:has-text('Radio')",
        "ul.cbi-tabmenu > li > a:not([href*='vlan24']):not([href*='24'])",
    ):
        if await mgmt_probe.is_visible(timeout=1500):
            break
        tab = gui_page.locator(tab_sel).first
        try:
            if await tab.count() and await tab.is_visible(timeout=2000):
                await tab.click()
                await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)
        except Exception:
            continue


async def _ensure_vlan_enabled_on_radio(gui_page) -> None:
    mode = gui_page.locator(NetworkLocators.VLAN_MODE_SELECT).first
    if await mode.count():
        try:
            await mode.wait_for(state="visible", timeout=10000)
            options = await mode.evaluate(
                "el => Array.from(el.options).map(o => ({text:(o.text||'').trim(), value:(o.value||'').trim()}))"
            )
            for opt in options:
                val = opt["value"].lower()
                text = opt["text"].lower()
                if val in ("transparent", "qinq", "tagged") or "transparent" in text:
                    await mode.select_option(value=opt["value"])
                    return
            if options:
                await mode.select_option(value=options[0]["value"])
            return
        except Exception:
            pass

    checkbox = gui_page.locator(NetworkLocators.VLAN_ENABLE_CHECKBOX).first
    if await checkbox.count():
        try:
            if not await checkbox.is_checked():
                await checkbox.check()
        except Exception:
            pass


async def _apply_vlan_config_gui(gui_page, *, settle_seconds: int = 15) -> None:
    attach_dialog_handler(gui_page)
    await apply_triple(
        gui_page,
        NetworkLocators.SAVE_BUTTON,
        NetworkLocators.APPLY_ICON,
        NetworkLocators.CONFIRM_APPLY,
        settle_seconds=settle_seconds,
    )


async def apply_sanity_mgmt_vlan_gui(
    gui_page,
    *,
    mgmtvlan: int,
    host: str = "",
    device_creds: dict | None = None,
    settle_seconds: int = 15,
) -> None:
    """Network → VLAN → Radio: enable VLAN, set Management VLAN ID, save."""
    await open_sanity_vlan_radio_config(gui_page, host=host, device_creds=device_creds)
    await _ensure_vlan_enabled_on_radio(gui_page)
    mgmt_loc = gui_page.locator(NetworkLocators.VLAN_MGMT_INPUT).first
    await mgmt_loc.wait_for(state="visible", timeout=30000)
    tag = await mgmt_loc.evaluate("el => el.tagName.toLowerCase()")
    if tag == "select":
        await mgmt_loc.select_option(value=str(int(mgmtvlan)))
    else:
        await mgmt_loc.fill(str(int(mgmtvlan)))
    await _apply_vlan_config_gui(gui_page, settle_seconds=settle_seconds)


def mgmt_vlan_matches(expected: int | str, actual: str) -> bool:
    try:
        return int(str(actual or "").strip()) == int(expected)
    except Exception:
        return str(expected).strip() == str(actual or "").strip()


async def verify_sanity_mgmt_vlan_backend(ssh, expected_vlan: int) -> bool:
    raw = (await sanity_ssh_run(ssh, SanityCommands.GET_VLAN_MGMT, timeout_s=30)).strip()
    return mgmt_vlan_matches(expected_vlan, raw)


async def read_mgmt_vlan_via_bts_hop(bts_ssh, cpe_v6: str) -> str:
    host = normalize_ip(cpe_v6.split("/")[0])
    cmd = (
        f"ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8 -6 {host} "
        f"\"uci -q get vlan.ath1.mgmtvlan\" 2>/dev/null || true"
    )
    return (await sanity_ssh_run(bts_ssh, cmd, timeout_s=30)).strip()


async def read_cpe_mgmt_vlan_with_fallback(
    bts_ssh,
    cpe_ssh,
    cpe_v6: str,
) -> str:
    try:
        await ensure_sanity_ssh_open(cpe_ssh)
        return (await sanity_ssh_run(cpe_ssh, SanityCommands.GET_VLAN_MGMT, timeout_s=20)).strip()
    except Exception:
        return await read_mgmt_vlan_via_bts_hop(bts_ssh, cpe_v6)


async def apply_sanity_mgmt_vlan_wifi_reload(
    ssh,
    mgmtvlan: int,
    *,
    settle_seconds: int = 10,
) -> None:
    """Set mgmtvlan with wifi reload only (keeps lab mgmt path up when possible)."""
    await ensure_sanity_ssh_open(ssh)
    for cmd in (
        f"uci set vlan.ath1.mgmtvlan='{int(mgmtvlan)}'",
        "uci commit vlan",
        "wifi reload >/dev/null 2>&1; echo RELOAD_DONE",
    ):
        try:
            await sanity_ssh_run(ssh, cmd, timeout_s=max(60, settle_seconds + 30))
        except Exception:
            pass
    if settle_seconds:
        import asyncio

        await asyncio.sleep(settle_seconds)


async def restore_sanity_mgmt_vlan_wifi_reload(
    ssh,
    snap: SanityNetworkSnapshot,
    *,
    settle_seconds: int = 10,
) -> None:
    mgmt = str(snap.mgmtvlan or "1").strip() or "1"
    mode = str(snap.vlan_mode or "transparent").strip() or "transparent"
    await ensure_sanity_ssh_open(ssh)
    for cmd in (
        f"uci set vlan.ath1.mode='{mode}'",
        f"uci set vlan.ath1.mgmtvlan='{mgmt}'",
        "uci commit vlan",
        "wifi reload >/dev/null 2>&1; echo RELOAD_DONE",
    ):
        try:
            await sanity_ssh_run(ssh, cmd, timeout_s=max(60, settle_seconds + 30))
        except Exception:
            pass
    if settle_seconds:
        import asyncio

        await asyncio.sleep(settle_seconds)


async def restore_mgmt_vlan_wifi_reload_via_bts_hop(
    bts_ssh,
    cpe_v6: str,
    snap: SanityNetworkSnapshot,
    *,
    settle_seconds: int = 10,
) -> None:
    host = normalize_ip(cpe_v6.split("/")[0])
    mgmt = str(snap.mgmtvlan or "1").strip() or "1"
    mode = str(snap.vlan_mode or "transparent").strip() or "transparent"
    cmd = (
        f"ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8 -6 {host} "
        f"\"uci set vlan.ath1.mode='{mode}'; uci set vlan.ath1.mgmtvlan='{mgmt}'; "
        f"uci commit vlan; wifi reload >/dev/null 2>&1; echo RELOAD_DONE\" 2>/dev/null || true"
    )
    await sanity_ssh_run(bts_ssh, cmd, timeout_s=max(60, settle_seconds + 30))
    if settle_seconds:
        import asyncio

        await asyncio.sleep(settle_seconds)


async def apply_sanity_mgmt_vlan_ucidyn(
    ssh,
    mgmtvlan: int,
    *,
    settle_seconds: int = 15,
) -> None:
    """Set mgmtvlan via ucidyn apply (FT_10-style; may drop SSH)."""
    await ensure_sanity_ssh_open(ssh)
    try:
        await sanity_ssh_run(ssh, f"ucidyn set vlan.ath1.mgmtvlan {int(mgmtvlan)}", timeout_s=120)
        await sanity_ssh_run(ssh, "ucidyn apply", timeout_s=120)
    except Exception:
        pass
    if settle_seconds:
        import asyncio

        await asyncio.sleep(settle_seconds)


async def apply_sanity_mgmt_vlan_backend(
    ssh,
    mgmtvlan: int,
    *,
    settle_seconds: int = 15,
) -> None:
    await apply_sanity_mgmt_vlan_ucidyn(ssh, mgmtvlan, settle_seconds=settle_seconds)


async def restore_sanity_mgmt_vlan_backend(
    ssh,
    snap: SanityNetworkSnapshot,
    *,
    settle_seconds: int = 15,
) -> None:
    await restore_sanity_mgmt_vlan_wifi_reload(ssh, snap, settle_seconds=settle_seconds)
