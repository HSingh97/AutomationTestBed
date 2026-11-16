"""Sanity-only NTP sync helpers (Management → System → General).

GUI NTP list maps to UCI ``ntpd.@server[*].host`` (not ``system.ntp``).
Index 0 is typically the local GPS ref (127.127.28.0) and is not shown/edited.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass

from pages.locators import CommonLocators, ManagementLocators, UITimeouts
from utils.management_flows import apply_triple
from utils.net_utils import normalize_ip
from utils.sanity_ssh import ensure_sanity_ssh_open, sanity_ssh_run
from utils.sanity_system_config import open_sanity_system_general
from utils.ui_helpers import attach_dialog_handler, luci_base_url

# Local/GPS NTP refs — do not replace these in the GUI list.
_LOCAL_NTP_HOST_RE = re.compile(r"^127\.127\.|^127\.0\.0\.1$|^::$|^::1$")

NTP_HOST_INPUT = "input[name^='ntpd.@server'][name$='.host']"
NTP_ADD_EVAL = """
(server) => {
    const el = document.querySelector('#ntp_addr');
    if (el) {
        el.scrollIntoView({ block: 'center' });
        el.value = server;
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
    }
    if (window.KWN_SYSTEM && KWN_SYSTEM.submit_add_ntp) {
        KWN_SYSTEM.submit_add_ntp();
        return 'added';
    }
    return el ? 'filled' : 'missing';
}
"""


@dataclass(frozen=True)
class SanityNtpSnap:
    servers: list[str]  # all ntpd.@server[*].host in index order
    enabled: str
    epoch_s: int
    date_str: str


def _admin_fallback(gui_page, fragment: str = "/system/system") -> str:
    base = luci_base_url(gui_page.url or "") or ""
    return f"{base}/admin{fragment}"


def resolve_lab_ntp_server(*, values: dict, source_v6: str = "") -> str:
    """Optional sync-only NTP target (hostname or IP). Prefer explicit, else PC IPv6."""
    for key in ("ntp_sync_server", "ntp_lab_server"):
        explicit = str(values.get(key) or "").strip()
        if explicit:
            return normalize_ip(explicit) or explicit
    bind = normalize_ip(source_v6 or "")
    if bind:
        return bind
    return ""


def resolve_ntp_test_servers(*, values: dict, slot_count: int = 4) -> list[str]:
    """
    Hostnames (preferred) written into the 4 GUI NTP slots.

    Not lab IPv6 — sheet allows any reachable NTP name; hostnames read clearly
    on the terminal.
    """
    raw = values.get("ntp_test_servers") or []
    servers = [str(s).strip() for s in raw if str(s).strip()]
    if not servers:
        servers = [
            "time.cloudflare.com",
            "ntp.ubuntu.com",
            "time.nist.gov",
            "pool.ntp.org",
        ]
    # Repeat / trim to match slot count.
    out: list[str] = []
    for i in range(max(1, int(slot_count))):
        out.append(servers[i % len(servers)])
    return out


def resolve_ntp_sync_target(*, values: dict, test_servers: list[str], source_v6: str = "") -> str:
    """
    Backend ntpdate target.

    Prefer lab PC (reachable on this testbed without public DNS). GUI slots use
    hostnames from ``ntp_test_servers`` — sync target is separate.
    """
    explicit = str(values.get("ntp_sync_server") or "").strip()
    if explicit:
        return normalize_ip(explicit) or explicit
    lab = resolve_lab_ntp_server(values=values, source_v6=source_v6)
    if lab:
        return lab
    if test_servers:
        return test_servers[0]
    return ""


def is_local_ntp_host(host: str) -> bool:
    return bool(_LOCAL_NTP_HOST_RE.match((host or "").strip()))


async def _ssh_multiline(ssh, command: str, *, timeout_s: int = 30) -> str:
    """Multi-line SSH output — do not use clean_ssh_output (it keeps only last scalar)."""
    await ensure_sanity_ssh_open(ssh)
    response = await ssh.send_command(command, timeout_ops=timeout_s)
    text = str(response.result or "").replace("\r", "")
    keep: list[str] = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            continue
        # Scrapli often appends a bare prompt line (root@HOST:~#).
        if s.startswith("root@"):
            continue
        keep.append(ln.rstrip())
    return "\n".join(keep).strip()


async def read_ntp_snap(ssh) -> SanityNtpSnap:
    raw = await _ssh_multiline(
        ssh,
        "uci -q show ntpd; echo ---; date +%s; date",
        timeout_s=30,
    )
    servers: list[str] = []
    enabled = ""
    for line in (raw or "").splitlines():
        m = re.match(r"ntpd\.@server\[(\d+)\]\.host='([^']*)'", line.strip())
        if m:
            idx = int(m.group(1))
            while len(servers) <= idx:
                servers.append("")
            servers[idx] = m.group(2)
            continue
        # Alternate UCI dump form without quotes
        m = re.match(r"ntpd\.@server\[(\d+)\]\.host=(.*)$", line.strip())
        if m and "host='" not in line:
            idx = int(m.group(1))
            while len(servers) <= idx:
                servers.append("")
            servers[idx] = m.group(2).strip().strip("'\"")
            continue
        m = re.match(r"ntpd\.general\.enabled='?([^'\s]+)'?", line.strip())
        if m:
            enabled = m.group(1)
    epoch_s = 0
    date_str = ""
    parts = (raw or "").split("---", 1)
    if len(parts) == 2:
        tail = [ln.strip() for ln in parts[1].strip().splitlines() if ln.strip()]
        if tail:
            try:
                epoch_s = int(re.search(r"\d{9,}", tail[0]).group(0))  # type: ignore[union-attr]
            except Exception:
                epoch_s = 0
        if len(tail) > 1:
            date_str = " ".join(tail[1:]).strip()
    return SanityNtpSnap(
        servers=servers,
        enabled=enabled,
        epoch_s=epoch_s,
        date_str=date_str,
    )


def first_public_ntp_index(servers: list[str]) -> int:
    for idx, host in enumerate(servers):
        if host and not is_local_ntp_host(host):
            return idx
    for idx, host in enumerate(servers):
        if not is_local_ntp_host(host):
            return idx
    return 1 if servers else 0


def public_ntp_indices(servers: list[str], *, max_slots: int = 4) -> list[int]:
    """UCI indices for the up-to-4 GUI NTP rows (skips GPS/local 127.127.x)."""
    idxs: list[int] = []
    for idx, host in enumerate(servers):
        if is_local_ntp_host(host or ""):
            continue
        idxs.append(idx)
        if len(idxs) >= max_slots:
            break
    return idxs


def pick_ntp_replace_index(servers: list[str], lab_server: str) -> int:
    """Prefer a public slot that is not already the lab NTP address."""
    lab_forms = _ipv6_forms(lab_server)
    for idx, host in enumerate(servers):
        if not host or is_local_ntp_host(host):
            continue
        if _ipv6_forms(host) & lab_forms:
            continue
        return idx
    return first_public_ntp_index(servers)


async def open_sanity_ntp_general(
    gui_page,
    *,
    host: str = "",
    device_creds: dict | None = None,
) -> None:
    await open_sanity_system_general(gui_page, host=host, device_creds=device_creds)
    # NTP table / add box should be present on General.
    add_box = gui_page.locator(ManagementLocators.NTP_ADD_INPUT_XPATH).or_(
        gui_page.locator("#ntp_addr")
    ).first
    try:
        await add_box.wait_for(state="attached", timeout=UITimeouts.ELEMENT_WAIT_MS)
    except Exception:
        pass


async def _save_system_general(gui_page) -> None:
    attach_dialog_handler(gui_page)
    await apply_triple(
        gui_page,
        ManagementLocators.SAVE_BUTTON,
        CommonLocators.APPLY_ICON,
        CommonLocators.CONFIRM_APPLY,
        settle_seconds=12,
    )
    await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)


async def gui_set_ntp_server(
    gui_page,
    server: str,
    *,
    host: str = "",
    device_creds: dict | None = None,
    replace_index: int | None = None,
) -> str:
    """
    Set NTP server via GUI (edit existing row, or Add when a slot is free).

    Returns mode used: ``edit`` or ``add``.
    """
    await open_sanity_ntp_general(gui_page, host=host, device_creds=device_creds)
    attach_dialog_handler(gui_page)
    server = (server or "").strip()
    if not server:
        raise ValueError("NTP server address required")

    if replace_index is not None:
        name = f"ntpd.@server[{replace_index}].host"
        field = gui_page.locator(f"input[name='{name}']").first
        try:
            await field.wait_for(state="visible", timeout=5000)
            await field.fill("")
            await field.fill(server)
            await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)
            await _save_system_general(gui_page)
            return "edit"
        except Exception:
            pass

    # Prefer editing first visible public NTP host input.
    inputs = gui_page.locator(NTP_HOST_INPUT)
    count = await inputs.count()
    for i in range(count):
        field = inputs.nth(i)
        try:
            if not await field.is_visible(timeout=1500):
                continue
            current = (await field.input_value()).strip()
            if is_local_ntp_host(current):
                continue
            await field.fill("")
            await field.fill(server)
            await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)
            await _save_system_general(gui_page)
            return "edit"
        except Exception:
            continue

    # Slot free — Add path (same as GUI_64 / KWN_SYSTEM.submit_add_ntp).
    add_visible = False
    add_box = gui_page.locator("#ntp_addr").first
    try:
        add_visible = await add_box.is_visible(timeout=2000)
    except Exception:
        add_visible = False

    if not add_visible:
        # Max entries — delete last Delete button then add.
        delete_btn = gui_page.locator(ManagementLocators.NTP_DELETE_BTN_XPATH).or_(
            gui_page.locator(ManagementLocators.NTP_DELETE_BTNS)
        ).last
        if await delete_btn.is_visible(timeout=3000):
            await delete_btn.click(force=True)
            await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)
            await open_sanity_ntp_general(gui_page, host=host, device_creds=device_creds)

    result = await gui_page.evaluate(NTP_ADD_EVAL, server)
    await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)
    try:
        await gui_page.wait_for_function(
            f"() => document.body.innerText.includes({server!r})",
            timeout=UITimeouts.ELEMENT_WAIT_MS,
        )
    except Exception:
        pass
    await _save_system_general(gui_page)
    if result == "missing":
        raise RuntimeError("[sanity] NTP add input #ntp_addr not found on System General")
    return "add"


async def gui_set_ntp_servers_bulk(
    gui_page,
    updates: dict[int, str],
    *,
    host: str = "",
    device_creds: dict | None = None,
) -> str:
    """
    Edit multiple ``ntpd.@server[i].host`` fields on System General, then Save once.

    ``updates`` maps UCI server index → hostname/IP.
    """
    if not updates:
        return "noop"
    await open_sanity_ntp_general(gui_page, host=host, device_creds=device_creds)
    attach_dialog_handler(gui_page)
    filled = 0
    missing: list[int] = []
    for idx in sorted(updates):
        server = (updates[idx] or "").strip()
        if not server:
            continue
        name = f"ntpd.@server[{idx}].host"
        field = gui_page.locator(f"input[name='{name}']").first
        try:
            await field.wait_for(state="visible", timeout=5000)
            await field.fill("")
            await field.fill(server)
            filled += 1
        except Exception:
            missing.append(idx)
    if not filled:
        raise RuntimeError(
            f"[sanity] no NTP host inputs found for indices {sorted(updates)}"
        )
    await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)
    await _save_system_general(gui_page)
    if missing:
        return f"edit-{filled}/{len(updates)}-miss{missing}"
    return f"edit-{filled}"


async def gui_restore_ntp_server(
    gui_page,
    original_host: str,
    *,
    index: int,
    host: str = "",
    device_creds: dict | None = None,
) -> None:
    """Restore a single ntpd.@server[index].host via GUI edit + Save."""
    if not original_host:
        return
    await gui_set_ntp_server(
        gui_page,
        original_host,
        host=host,
        device_creds=device_creds,
        replace_index=index,
    )


async def gui_restore_ntp_servers_bulk(
    gui_page,
    originals: dict[int, str],
    *,
    host: str = "",
    device_creds: dict | None = None,
) -> str:
    """Restore multiple NTP host slots via one GUI Save."""
    return await gui_set_ntp_servers_bulk(
        gui_page,
        originals,
        host=host,
        device_creds=device_creds,
    )


async def skew_device_clock(ssh, *, days_back: int = 10) -> int:
    """Push device clock backward so NTP sync is observable. Returns epoch before skew."""
    await ensure_sanity_ssh_open(ssh)
    before = await sanity_ssh_run(ssh, "date +%s", timeout_s=15)
    try:
        before_epoch = int(re.search(r"\d{9,}", before).group(0))  # type: ignore[union-attr]
    except Exception:
        before_epoch = 0
    seconds = max(1, int(days_back)) * 86400
    target = max(0, before_epoch - seconds)
    # Busybox date -s accepts @epoch on many builds; fall back to formatted string.
    await sanity_ssh_run(
        ssh,
        f"date -s @{target} 2>/dev/null || date -s \"$(date -d @{target} '+%Y-%m-%d %H:%M:%S' 2>/dev/null)\" 2>/dev/null || true",
        timeout_s=20,
    )
    return before_epoch


async def force_ntp_sync(ssh, server: str, *, wait_s: float = 8.0) -> str:
    """
    One-shot sync against configured server.

    Uses ``ntpdate -b -u`` first (reliable step), then restarts ``ntpd``.
    """
    await ensure_sanity_ssh_open(ssh)
    server = (server or "").strip()
    # Prefer direct step against the lab server we just configured.
    out = await sanity_ssh_run(
        ssh,
        f"ntpdate -b -u '{server}' 2>&1; /etc/init.d/ntpdate start 2>&1; "
        f"/etc/init.d/ntpd restart >/dev/null 2>&1; sleep 1; date +%s; date",
        timeout_s=60,
    )
    await asyncio.sleep(max(0.0, wait_s))
    return out


def epoch_delta_ok(device_epoch: int, *, tolerance_s: int) -> tuple[bool, int]:
    lab = int(time.time())
    delta = abs(int(device_epoch) - lab)
    return delta <= int(tolerance_s), delta


def _ipv6_forms(addr: str) -> set[str]:
    """Match compressed/expanded IPv6 and raw string forms."""
    import ipaddress

    forms: set[str] = set()
    raw = (addr or "").strip().lower()
    if not raw:
        return forms
    forms.add(raw)
    norm = normalize_ip(addr)
    if norm:
        forms.add(norm.lower())
    try:
        ip = ipaddress.ip_address(addr.strip("[]"))
        forms.add(str(ip).lower())
        forms.add(ip.compressed.lower())
        forms.add(ip.exploded.lower())
    except Exception:
        pass
    return {f for f in forms if f}


async def verify_ntp_server_uci(ssh, server: str) -> bool:
    snap = await read_ntp_snap(ssh)
    targets = _ipv6_forms(server)
    for host in snap.servers:
        host_forms = _ipv6_forms(host)
        if targets & host_forms:
            return True
        h = (host or "").strip().lower()
        for t in targets:
            if t and t in h:
                return True
    return False


async def verify_ntp_slots_uci(
    ssh,
    server: str,
    indices: list[int],
) -> tuple[bool, list[int], list[str]]:
    """
    Verify each UCI index holds ``server``.

    Returns (all_ok, failed_indices, current_hosts_for_indices).
    """
    expected = {idx: server for idx in indices}
    return await verify_ntp_slots_map(ssh, expected)


async def verify_ntp_slots_map(
    ssh,
    expected: dict[int, str],
) -> tuple[bool, list[int], list[str]]:
    """Verify each UCI index matches its expected hostname/IP."""
    snap = await read_ntp_snap(ssh)
    failed: list[int] = []
    current: list[str] = []
    indices = sorted(expected)
    for idx in indices:
        want = (expected.get(idx) or "").strip()
        host = snap.servers[idx] if idx < len(snap.servers) else ""
        current.append(host or "")
        got = (host or "").strip()
        if got.lower() == want.lower():
            continue
        if _ipv6_forms(want) and (_ipv6_forms(got) & _ipv6_forms(want)):
            continue
        if want and want.lower() in got.lower():
            continue
        failed.append(idx)
    return (not failed), failed, current


async def ssh_set_ntp_slots(
    ssh,
    updates: dict[int, str],
) -> None:
    """Commit multiple ntpd.@server[i].host values via UCI + ntpd restart."""
    if not updates:
        return
    parts: list[str] = []
    for idx, host in sorted(updates.items()):
        h = (host or "").replace("'", "")
        parts.append(f"uci set ntpd.@server[{idx}].host='{h}'")
        parts.append(f"uci set ntpd.@server[{idx}].status='1'")
    parts.append("uci commit ntpd")
    parts.append("/etc/init.d/ntpd restart >/dev/null 2>&1")
    await sanity_ssh_run(ssh, "; ".join(parts), timeout_s=60)


async def read_device_epoch(ssh) -> tuple[int, str]:
    raw = await _ssh_multiline(ssh, "date +%s; date", timeout_s=20)
    lines = [ln.strip() for ln in (raw or "").splitlines() if ln.strip()]
    epoch = 0
    if lines:
        try:
            epoch = int(re.search(r"\d{9,}", lines[0]).group(0))  # type: ignore[union-attr]
        except Exception:
            epoch = 0
    date_str = " ".join(lines[1:]) if len(lines) > 1 else ""
    return epoch, date_str
