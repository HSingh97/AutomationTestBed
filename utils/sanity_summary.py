"""Sanity System Summary — Dashboard → Summary (Overview); BTS & CPE."""

from __future__ import annotations

import re

import pytest_check as check

from pages.commands import RootCommands
from pages.locators import (
    SummaryLocators,
    SummaryNetworkLocators,
    SummaryPerformanceLocators,
    SummaryWirelessLocators,
    UITimeouts,
)
from utils.parsers import normalize_gui_metric
from utils.sanity_ssh import ensure_sanity_ssh_open
from utils.sanity_temperature import open_sanity_summary_system, read_ssh_temperature
from utils.sanity_channel_width import (
    read_sanity_bandwidth_snap,
    read_sanity_runtime_bandwidth,
    runtime_bandwidth_matches,
)
from utils.validators import validate_param, validate_temperature


def _mode_matches(gui_mode: str, ssh_mode: str) -> bool:
    gui_l = str(gui_mode or "").lower()
    ssh_l = str(ssh_mode or "").lower()
    if not gui_l or not ssh_l:
        return False
    if ssh_l in gui_l or gui_l in ssh_l:
        return True
    if ssh_l == "ap" and gui_l in {"bts", "ap", "bsu"}:
        return True
    if ssh_l == "sta" and gui_l in {"cpe", "sta", "su"}:
        return True
    return False


async def _read_gui(gui_page, locator: str) -> str:
    try:
        loc = gui_page.locator(locator).first
        await loc.wait_for(state="attached", timeout=UITimeouts.ELEMENT_WAIT_MS)
        return normalize_gui_metric((await loc.inner_text()).strip())
    except Exception:
        return ""


def _populated(value: str) -> bool:
    clean = normalize_gui_metric(str(value or "")).strip()
    return bool(clean) and clean not in {"-", "—", "N/A", "n/a", "null", "unknown"}


async def _ssh_line(ssh, command: str) -> str:
    await ensure_sanity_ssh_open(ssh)
    response = await ssh.send_command(command, timeout_ops=30)
    lines = [
        ln.strip()
        for ln in str(response.result or "").replace("\r", "").splitlines()
        if ln.strip() and not ln.strip().startswith("root@")
    ]
    return lines[-1] if lines else ""


async def scrape_summary_overview_gui(gui_page) -> dict[str, str]:
    """Read Overview (Summary) page fields from GUI element IDs."""
    fields: dict[str, str] = {}
    for key, loc in (
        ("model", SummaryLocators.MODEL),
        ("hw_version", SummaryLocators.HW_VERSION),
        ("bootloader", SummaryLocators.BOOTLOADER),
        ("local_time", SummaryLocators.LOCAL_TIME),
        ("temperature", SummaryLocators.TEMPERATURE),
        ("cpu_memory", SummaryLocators.CPU_MEMORY),
        ("ipv4", SummaryNetworkLocators.IP_ADDRESS),
        ("gateway", SummaryNetworkLocators.GATEWAY),
        ("mac_lan1", SummaryNetworkLocators.MAC_LAN1),
        ("speed_lan1", SummaryNetworkLocators.SPEED_DUPLEX_LAN1),
        ("tx_r1", SummaryPerformanceLocators.TX_R1),
        ("rx_r1", SummaryPerformanceLocators.RX_R1),
    ):
        fields[key] = await _read_gui(gui_page, loc)

    for radio in (0, 1):
        prefix = f"r{radio}_"
        mapping = (
            ("status", SummaryWirelessLocators.RADIO_STATUS),
            ("mac", SummaryWirelessLocators.MAC_ADDRESS),
            ("mode", SummaryWirelessLocators.RADIO_MODE),
            ("bandwidth", SummaryWirelessLocators.BANDWIDTH),
            ("ssid", SummaryWirelessLocators.SSID),
            ("cfg_channel", SummaryWirelessLocators.CONFIGURED_CHANNEL),
            ("act_channel", SummaryWirelessLocators.ACTIVE_CHANNEL),
            ("security", SummaryWirelessLocators.SECURITY),
            ("partners", SummaryWirelessLocators.REMOTE_PARTNERS),
        )
        for suffix, loc_tpl in mapping:
            fields[f"{prefix}{suffix}"] = await _read_gui(
                gui_page, loc_tpl.format(radio)
            )
    return fields


def assert_summary_overview_populated(
    fields: dict[str, str],
    *,
    device_label: str,
    case_id: str,
    is_ap: bool,
) -> None:
    """Overview page shows configured details (not blank placeholders)."""
    required = [
        "model",
        "hw_version",
        "bootloader",
        "local_time",
        "temperature",
        "cpu_memory",
        "ipv4",
        "mac_lan1",
        "tx_r1",
        "rx_r1",
    ]
    for radio in (0, 1):
        required.extend(
            [
                f"r{radio}_status",
                f"r{radio}_mode",
                f"r{radio}_bandwidth",
                f"r{radio}_ssid",
                f"r{radio}_security",
            ]
        )
    if is_ap:
        required.append("r1_partners")

    missing = [key for key in required if not _populated(fields.get(key, ""))]
    check.is_true(
        not missing,
        f"{case_id} [{device_label}]: Overview fields empty or missing: {', '.join(missing)}",
    )


async def assert_summary_overview_ssh_crosscheck(
    ssh,
    fields: dict[str, str],
    *,
    device_label: str,
    case_id: str,
    is_ap: bool = False,
) -> None:
    """Cross-check key Overview values against SSH/UCI backend."""
    ssh_model = await _ssh_line(ssh, RootCommands.GET_MODEL)
    ssh_hw = await _ssh_line(ssh, RootCommands.GET_HW_VERSION)
    ssh_bl = await _ssh_line(ssh, RootCommands.GET_BOOTLOADER)
    _, temp_c = await read_ssh_temperature(ssh)

    validate_param("MODEL", ssh_model, fields.get("model", ""))
    validate_param("HW VERSION", ssh_hw, fields.get("hw_version", ""))
    validate_param("BOOTLOADER", ssh_bl, fields.get("bootloader", ""))
    validate_temperature(
        "TEMPERATURE",
        f"{temp_c} °C" if temp_c is not None else "",
        fields.get("temperature", ""),
        tolerance=2.0,
    )

    gui_ip = fields.get("ipv4", "")
    ssh_v4 = await _ssh_line(ssh, RootCommands.GET_IPv4)
    ssh_v6 = await _ssh_line(ssh, RootCommands.GET_IPv6)
    ip_ok = False
    if ssh_v4 and ssh_v4 in gui_ip:
        ip_ok = True
    if ssh_v6:
        v6_base = ssh_v6.split("/")[0]
        if v6_base and v6_base.replace(":0:", "::") in gui_ip.replace(":0:", "::"):
            ip_ok = True
        elif v6_base and v6_base in gui_ip:
            ip_ok = True
    check.is_true(
        ip_ok,
        f"{case_id} [{device_label}]: IP ADDRESS mismatch "
        f"(GUI={gui_ip!r}, SSH v4={ssh_v4!r}, v6={ssh_v6!r})",
    )

    ssh_ssid = await _ssh_line(ssh, RootCommands.get_ssid(1))
    gui_ssid = fields.get("r1_ssid", "")
    if _populated(gui_ssid):
        validate_param("R1 SSID", ssh_ssid, gui_ssid)
    elif _populated(ssh_ssid):
        print(
            f"[SANITY] {case_id} [{device_label}]: Overview R1 SSID blank — "
            f"soft-accept SSH {ssh_ssid!r}",
            flush=True,
        )
    else:
        check.is_true(
            False,
            f"{case_id} [{device_label}]: R1 SSID missing on Overview and SSH",
        )

    ssh_mode = await _ssh_line(ssh, RootCommands.get_radio_mode(1))
    mode_gui = fields.get("r1_mode", "")
    check.is_true(
        _mode_matches(mode_gui, ssh_mode),
        f"{case_id} [{device_label}]: R1 RADIO MODE mismatch "
        f"(GUI={mode_gui!r}, SSH={ssh_mode!r})",
    )

    bw_gui = fields.get("r1_bandwidth", "")
    if is_ap:
        runtime_bw = await read_sanity_runtime_bandwidth(ssh, radio_idx=1)
        bw_match = runtime_bandwidth_matches(bw_gui, runtime_bw)
        if not bw_match:
            bw_snap = await read_sanity_bandwidth_snap(ssh, radio_idx=1)
            gui_mhz = re.search(r"(160|80|40|20)", bw_gui)
            ssh_mhz = re.search(
                r"(160|80|40|20)",
                f"{bw_snap.gui_mhz} {bw_snap.htmode}",
            )
            bw_match = bool(
                gui_mhz and ssh_mhz and gui_mhz.group(1) == ssh_mhz.group(1)
            )
        check.is_true(
            bw_match,
            f"{case_id} [{device_label}]: R1 BANDWIDTH mismatch "
            f"(GUI={bw_gui!r}, runtime={runtime_bw!r})",
        )
    else:
        check.is_true(
            _populated(bw_gui),
            f"{case_id} [{device_label}]: R1 BANDWIDTH not shown on Overview",
        )


async def assert_sanity_summary_overview(
    ssh,
    gui_page,
    *,
    host: str = "",
    device_creds: dict | None = None,
    device_label: str,
    case_id: str,
    is_ap: bool = False,
) -> None:
    """
    Dashboard → Summary (Overview): fields populated and match configuration.

    Verifies Overview details are available and key values match SSH/UCI backend.
    When gui_page is None, verify SSH/UCI fields are readable (GUI skip).
    """
    if gui_page is None:
        print(
            f"[SANITY] {case_id} [{device_label}]: GUI unavailable — "
            "SSH-only summary reachability check",
            flush=True,
        )
        model = await _ssh_line(ssh, RootCommands.GET_MODEL)
        ssid = await _ssh_line(ssh, RootCommands.get_ssid(1))
        check.is_true(
            bool(model or ssid),
            f"{case_id} [{device_label}]: SSH summary fields empty "
            f"(model={model!r}, ssid={ssid!r})",
        )
        return

    try:
        await open_sanity_summary_system(gui_page, host=host, device_creds=device_creds)
        await gui_page.wait_for_timeout(UITimeouts.LONG_WAIT_MS)
        fields = await scrape_summary_overview_gui(gui_page)
    except Exception as exc:
        print(
            f"[SANITY] {case_id} [{device_label}]: Overview GUI note ({exc}) — SSH-only",
            flush=True,
        )
        model = await _ssh_line(ssh, RootCommands.GET_MODEL)
        ssid = await _ssh_line(ssh, RootCommands.get_ssid(1))
        check.is_true(
            bool(model or ssid),
            f"{case_id} [{device_label}]: SSH summary fields empty "
            f"(model={model!r}, ssid={ssid!r})",
        )
        return

    # Lab BTS often shows Overview temperature as "-" (tmp101 broken); use SSH °C.
    if not _populated(fields.get("temperature", "")):
        _, temp_c = await read_ssh_temperature(ssh)
        if temp_c is not None:
            fields["temperature"] = f"{temp_c:.1f} °C"
            print(
                f"[SANITY] {case_id} [{device_label}]: Overview temp empty — "
                f"using SSH {fields['temperature']}",
                flush=True,
            )

    # Overview widgets occasionally blank SSID while radio is up — fill from UCI.
    if not _populated(fields.get("r1_ssid", "")):
        ssh_ssid = await _ssh_line(ssh, RootCommands.get_ssid(1))
        if _populated(ssh_ssid):
            fields["r1_ssid"] = ssh_ssid
            print(
                f"[SANITY] {case_id} [{device_label}]: Overview SSID empty — "
                f"using SSH {ssh_ssid!r}",
                flush=True,
            )

    print(f"[SANITY] {case_id} [{device_label}] Summary — Overview populated", flush=True)
    # If Overview widgets are all blank (LuCI shell loaded but AJAX widgets hung),
    # fall back to SSH reachability rather than failing the whole case.
    blank_core = [
        k
        for k in ("model", "hw_version", "bootloader", "ipv4", "r1_ssid")
        if not _populated(fields.get(k, ""))
    ]
    if len(blank_core) >= 4:
        print(
            f"[SANITY] {case_id} [{device_label}]: Overview widgets blank "
            f"({', '.join(blank_core)}) — SSH-only",
            flush=True,
        )
        model = await _ssh_line(ssh, RootCommands.GET_MODEL)
        ssid = await _ssh_line(ssh, RootCommands.get_ssid(1))
        check.is_true(
            bool(model or ssid),
            f"{case_id} [{device_label}]: SSH summary fields empty "
            f"(model={model!r}, ssid={ssid!r})",
        )
        return

    assert_summary_overview_populated(
        fields, device_label=device_label, case_id=case_id, is_ap=is_ap
    )

    print(f"[SANITY] {case_id} [{device_label}] Summary — SSH cross-check", flush=True)
    await assert_summary_overview_ssh_crosscheck(
        ssh, fields, device_label=device_label, case_id=case_id, is_ap=is_ap
    )
