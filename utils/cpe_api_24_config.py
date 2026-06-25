"""Configuration for CPE 2.4 GHz management API (169.254.254.1)."""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, replace
from pathlib import Path

from config.defaults import RADIO_24_SCAN_DEFAULTS

CPE_24_MGMT_API_HOST = "169.254.254.1"
DEFAULT_CPE_MGMT_SSID = str(RADIO_24_SCAN_DEFAULTS.get("CPE_MGMT_HIDDEN_SSID", "KWDEJPOQ")).strip()
DEFAULT_CPE_MGMT_PASSWORD = str(RADIO_24_SCAN_DEFAULTS.get("CPE_MGMT_HIDDEN_PASSWORD", ""))
DEFAULT_CPE_API_24_BASE = f"http://{CPE_24_MGMT_API_HOST}"
BTSCONNECT_PATH = "/api/v1/CPE/btsconnect"
BTSCONNECT_URL = f"{DEFAULT_CPE_API_24_BASE}{BTSCONNECT_PATH}"
DEFAULT_BTSCONNECT_TIMEOUT_S = 300.0
DEFAULT_BTSCONNECT_NEGATIVE_TIMEOUT_S = 120.0
DEFAULT_RESET_WAIT_S = 180.0
DEFAULT_FACTORY_RESET_COMMAND = "firstboot -y && reboot"
DEFAULT_CPE_SSH_HOST = CPE_24_MGMT_API_HOST
DEFAULT_FW_TFTPBOOT_DIR = "/tftpboot"
UPLOAD_SW_PATH = "/api/v1/cpe/upload-sw"
UPLOAD_SW_STATUS_PATH = "/api/v1/cpe/upload-sw-status"
UPGRADE_SW_PATH = "/api/v1/cpe/upgrade-sw"
UPGRADE_SW_STATUS_PATH = "/api/v1/cpe/upgrade-sw-status"
# Spec sheet label; device exposes upgrade-sw-status (not upgrade-sw-version).
UPGRADE_SW_VERSION_PATH = UPGRADE_SW_STATUS_PATH
LINK_THROUGHPUT_TEST_PATH = "/api/v1/cpe/link-throughput-test"
DEFAULT_LINK_THROUGHPUT_DURATION_S = 30
# Test runs for `duration` seconds; allow margin for HTTP response.
DEFAULT_LINK_THROUGHPUT_READ_TIMEOUT_S = 90.0
# API_17: CPE wireless STA for Radio 1 / BTS link (mode=sta; usually wifi-iface[1]).
CPE_RADIO1_STA_WIFI_IFACE_IDX = 1
API_17_WIFI_SETTLE_S = 5.0
API_17_LINK_DOWN_MAX_S = 60.0
API_17_LINK_UP_MAX_S = 300.0
API_17_LINK_UP_POLL_S = 5.0
# API_18: overlap second POST while first throughput test is running.
API_18_FIRST_TEST_DURATION_S = 30
API_18_OVERLAP_DELAY_S = 2.0
API_18_IN_PROGRESS_POLL_MAX_S = 20.0
API_18_IN_PROGRESS_POLL_DELAY_S = 1.0
UPGRADE_IN_PROGRESS_STATES = frozenset(
    {"INPROGRESS", "IN_PROGRESS", "UPLOADING", "IN PROGRESS", "UPGRADE_IN_PROGRESS"}
)
UPGRADE_SUCCESS_STATES = frozenset(
    {"SUCCESS", "UPGRADE_SUCCESS", "FW_UPGRADE_SUCCESS", "COMPLETE", "COMPLETED"}
)
DEFAULT_FW_UPLOAD_TIMEOUT_S = 900.0
# Single cap for upgrade / reboot / device-up / BTS-link waits (poll until done).
FW_UPGRADE_WAIT_MAX_S = 1200.0  # 20 min
FW_UPGRADE_VERIFY_MAX_S = FW_UPGRADE_WAIT_MAX_S
DEFAULT_UPGRADE_DELAY_S = "5"
POST_UPGRADE_POLL_DELAY_S = 5.0
POST_UPGRADE_LINK_POLL_DELAY_S = 15.0
# API_12: after reboot, grace before nmcli rejoin; retry rejoin interval.
API_12_MGMT_AP_GRACE_S = 20.0
API_12_REJOIN_INTERVAL_S = 20.0
API_12_PROGRESS_INTERVAL_S = 180.0
# API_15: poll upgrade-sw-status after wrong-model upload + upgrade-sw until 422.
INVALID_FW_STATUS_POLL_MAX_S = 60.0
INVALID_FW_STATUS_POLL_DELAY_S = 2.0

# 2.4G_RADIO_41–50 — generic CPE mgmt API validation (169.254.254.1 over 2.4 GHz Wi‑Fi).
SW_VERSION_PATH = "/api/v1/cpe/sw-version"
ALIGNMENT_DATA_PATH = "/api/v1/cpe/alignment-data"
UPLOAD_SW_STATUS_PATH = "/api/v1/cpe/upload-sw-status"
INVALID_API_PATH = "/api/v1/cpe/not-a-valid-endpoint"
RADIO24_API_LOAD_REQUESTS = 100
RADIO24_API_LOAD_WINDOW_S = 60.0
RADIO24_API_RESPONSE_MAX_MS = 500.0
# Case 46: 4 min boot settle, then nmcli join with profile SSID/password (no BTS-link wait).
RADIO24_API_REBOOT_BOOT_MIN_S = 240.0  # 4 min before first join attempt
RADIO24_API_REJOIN_INTERVAL_S = 15.0
RADIO24_API_REBOOT_WAIT_S = 600.0  # 10 min total (4 min boot + up to 6 min retries)
RADIO24_BTS_LINK_WAIT_S = 300.0
RADIO24_API_LOG_PATTERNS = [
    r"sw-version",
    r"sw_version",
    r"swversion",
    r"Software version",
    r"/api/v1",
    r"api/v1/cpe",
    r"cpe_api_body",
    r"senao-openapi",
    r"api\.fcgi",
    r"METHOD_NOT_ALLOWED",
    r"endpoint_not_found",
    r"ssid_not_found",
    r"openapi",
]


@dataclass(frozen=True)
class CpeApi24Config:
    base_url: str
    btsconnect_path: str
    wifi_interface: str | None
    wifi_connection_name: str
    wifi_settle_s: float
    cpe_mgmt_ssid: str
    cpe_mgmt_password: str
    cpe_mgmt_hidden: bool
    bts_ssid: str
    bts_password: str
    btsconnect_timeout_s: float
    btsconnect_negative_timeout_s: float
    reset_wait_s: float
    factory_reset_command: str
    factory_reset_path: str | None
    cpe_ssh_host: str
    cpe_ssh_fallback_host: str | None
    cpe_ssh_tunnel_host: str | None
    cpe_ssh_via_bts_tunnel: bool
    cpe_ssh_user: str
    cpe_ssh_password: str
    invalid_ssid: str
    invalid_password: str
    fetch_bts_via_ssh: bool
    fw_file_path: str | None
    fw_upload_timeout_s: float
    cpe_model: str | None


def infer_cpe_model_from_fw_path(fw_path: str | None) -> str | None:
    if not fw_path:
        return None
    match = re.search(r"(UBR\d{3})", fw_path, re.IGNORECASE)
    return match.group(1).upper() if match else None


def infer_fw_version_from_fw_path(fw_path: str | None) -> str | None:
    if not fw_path:
        return None
    match = re.search(r"(\d+\.\d+\.\d+(?:\.\d+)?)", Path(fw_path).name)
    return match.group(1) if match else None


def resolve_cpe_api_24_config(request, profile: dict | None = None) -> CpeApi24Config:
    profile = profile or {}
    cpe24 = profile.get("cpe_api_24") or {}
    recovery = profile.get("recovery") or {}
    dut = profile.get("dut") or {}
    base_url = (
        request.config.getoption("--cpe-24-api-base") or cpe24.get("base_url") or DEFAULT_CPE_API_24_BASE
    ).rstrip("/")
    factory_reset_command = (
        request.config.getoption("--cpe-24-factory-reset-command")
        or cpe24.get("factory_reset_command")
        or recovery.get("factory_reset_command")
        or DEFAULT_FACTORY_RESET_COMMAND
    ).strip()
    cpe_ssh_password = request.config.getoption("--password") or dut.get("password") or ""
    cpe_ssh_user = request.config.getoption("--username") or dut.get("username") or "root"
    remote_ipv6s = dut.get("remote_ipv6s") or []
    cli_fallback = getattr(request.config.option, "cpe_24_ssh_fallback_host", None)
    cpe_ssh_fallback = (
        (str(cli_fallback).strip() if cli_fallback else "")
        or (cpe24.get("cpe_ssh_fallback_host") or "").strip()
        or (remote_ipv6s[0] if remote_ipv6s else "")
    )
    default_mgmt_ssid = DEFAULT_CPE_MGMT_SSID
    default_mgmt_password = DEFAULT_CPE_MGMT_PASSWORD
    cpe_mgmt_ssid = (
        (request.config.getoption("--cpe-24-mgmt-ssid") or "").strip()
        or str(cpe24.get("cpe_mgmt_ssid") or "").strip()
        or default_mgmt_ssid
    )
    return CpeApi24Config(
        base_url=base_url,
        btsconnect_path=BTSCONNECT_PATH,
        wifi_interface=request.config.getoption("--cpe-24-wifi-interface") or None,
        wifi_connection_name=cpe_mgmt_ssid,
        wifi_settle_s=8.0,
        cpe_mgmt_ssid=cpe_mgmt_ssid,
        cpe_mgmt_password=_cli_password(request, "--cpe-24-mgmt-password")
        if request.config.getoption("--cpe-24-mgmt-password") is not None
        else (
            cpe24.get("cpe_mgmt_password")
            if cpe24.get("cpe_mgmt_password") is not None
            else default_mgmt_password
        ),
        cpe_mgmt_hidden=True,
        bts_ssid=(request.config.getoption("--cpe-24-bts-ssid") or "").strip(),
        bts_password=_cli_password(request, "--cpe-24-bts-password"),
        btsconnect_timeout_s=float(
            request.config.getoption("--cpe-24-btsconnect-timeout")
            or DEFAULT_BTSCONNECT_TIMEOUT_S
        ),
        btsconnect_negative_timeout_s=float(
            request.config.getoption("--cpe-24-btsconnect-negative-timeout")
            or DEFAULT_BTSCONNECT_NEGATIVE_TIMEOUT_S
        ),
        reset_wait_s=float(
            request.config.getoption("--cpe-24-reset-wait-s")
            or cpe24.get("reset_wait_s")
            or DEFAULT_RESET_WAIT_S
        ),
        factory_reset_command=factory_reset_command,
        factory_reset_path=request.config.getoption("--cpe-24-factory-reset-path"),
        cpe_ssh_host=str(
            request.config.getoption("--cpe-24-ssh-host")
            or cpe24.get("cpe_ssh_host")
            or DEFAULT_CPE_SSH_HOST
        ),
        cpe_ssh_fallback_host=cpe_ssh_fallback or None,
        cpe_ssh_tunnel_host=str(cpe24.get("cpe_ssh_tunnel_host")).strip() if cpe24.get("cpe_ssh_tunnel_host") else None,
        cpe_ssh_via_bts_tunnel=bool(cpe24.get("cpe_ssh_via_bts_tunnel", False)),
        cpe_ssh_user=cpe_ssh_user,
        cpe_ssh_password=str(cpe_ssh_password),
        invalid_ssid=str(
            request.config.getoption("--cpe-24-invalid-ssid")
            or "UBR_INVALID_SSID_NOT_FOUND"
        ),
        invalid_password=str(
            request.config.getoption("--cpe-24-invalid-password") or "wrong-password-99"
        ),
        fetch_bts_via_ssh=not request.config.getoption("--cpe-24-no-bts-ssh-fetch"),
        fw_file_path=resolve_fw_file_path(request, cpe24),
        fw_upload_timeout_s=float(
            request.config.getoption("--cpe-24-fw-upload-timeout")
            or cpe24.get("fw_upload_timeout_s")
            or DEFAULT_FW_UPLOAD_TIMEOUT_S
        ),
        cpe_model=(
            (request.config.getoption("--cpe-24-cpe-model") or cpe24.get("cpe_model") or "").strip()
            or None
        ),
    )


def resolve_fw_file_path(request, cpe24: dict | None = None) -> str | None:
    """Resolve firmware image from --cpe-24-fw-file under /tftpboot (or absolute path)."""
    cpe24 = cpe24 or {}
    name = (request.config.getoption("--cpe-24-fw-file") or cpe24.get("fw_file") or "").strip()
    if not name:
        return None
    direct = Path(name).expanduser()
    if direct.is_file():
        return str(direct.resolve())
    base = Path(
        request.config.getoption("--cpe-24-fw-dir") or cpe24.get("fw_dir") or DEFAULT_FW_TFTPBOOT_DIR
    ).expanduser()
    candidate = base / name
    if candidate.is_file():
        return str(candidate.resolve())
    raise RuntimeError(
        f"Firmware file not found for API_09.\n"
        f"  Tried: {candidate}\n"
        f"  Also tried: {direct}\n"
        f"  Place image in {base} or pass full path via --cpe-24-fw-file."
    )


def _list_tftpboot_fw_images(base: Path) -> list[Path]:
    return sorted(p for p in base.glob("Senao-UBR*.tgz") if p.is_file())


def resolve_invalid_fw_file_path(
    request,
    cpe24: dict | None = None,
    *,
    valid_fw_path: str | None = None,
) -> str:
    """API_15: wrong-model Senao FW from /tftpboot (e.g. UBR660 image on UBR650 CPE)."""
    cpe24 = cpe24 or {}
    name = (request.config.getoption("--cpe-24-invalid-fw-file") or cpe24.get("invalid_fw_file") or "").strip()
    base = Path(
        request.config.getoption("--cpe-24-fw-dir") or cpe24.get("fw_dir") or DEFAULT_FW_TFTPBOOT_DIR
    ).expanduser()
    if name:
        direct = Path(name).expanduser()
        if direct.is_file():
            return str(direct.resolve())
        candidate = base / name
        if candidate.is_file():
            return str(candidate.resolve())
        raise RuntimeError(
            f"Invalid firmware file not found for API_15.\n"
            f"  Tried: {candidate}\n"
            f"  Also tried: {direct}\n"
            f"  Place image in {base} or pass only the filename."
        )

    cpe_model = infer_cpe_model_from_fw_path(valid_fw_path)
    valid_resolved = Path(valid_fw_path).resolve() if valid_fw_path else None
    images = _list_tftpboot_fw_images(base)
    if not images:
        raise RuntimeError(
            f"API_15 needs at least two Senao-UBR*.tgz files in {base} "
            f"(correct model + wrong model), or pass --cpe-24-invalid-fw-file."
        )

    preferred_names = (
        "Senao-UBR660-xxx-0.0.0.655-apps.tgz",
        "Senao-UBR630-xxx-0.0.0.655-apps.tgz",
        "Senao-UBR655-xxx-0.0.0.655-apps.tgz",
    )
    for pref in preferred_names:
        candidate = base / pref
        if not candidate.is_file():
            continue
        if valid_resolved and candidate.resolve() == valid_resolved:
            continue
        wrong_model = infer_cpe_model_from_fw_path(str(candidate))
        if not cpe_model or (wrong_model and wrong_model != cpe_model):
            return str(candidate.resolve())

    mismatched: list[Path] = []
    for candidate in images:
        if valid_resolved and candidate.resolve() == valid_resolved:
            continue
        wrong_model = infer_cpe_model_from_fw_path(str(candidate))
        if cpe_model and wrong_model and wrong_model != cpe_model:
            mismatched.append(candidate)
    if mismatched:
        return str(random.choice(mismatched).resolve())

    other = [c for c in images if not valid_resolved or c.resolve() != valid_resolved]
    if other:
        return str(random.choice(other).resolve())

    raise RuntimeError(
        f"API_15: could not pick a different-model FW in {base}.\n"
        f"  CPE model (from --cpe-24-fw-file): {cpe_model or 'unknown'}\n"
        f"  Found: {[p.name for p in images]}\n"
        f"  Add e.g. Senao-UBR660-xxx-0.0.0.655-apps.tgz or pass --cpe-24-invalid-fw-file."
    )


def merge_cli_overrides(config: CpeApi24Config, request) -> CpeApi24Config:
    """CLI flags override SSH-fetched values when explicitly set."""
    mgmt_ssid = (request.config.getoption("--cpe-24-mgmt-ssid") or "").strip()
    bts_ssid = (request.config.getoption("--cpe-24-bts-ssid") or "").strip()
    mgmt_pw = request.config.getoption("--cpe-24-mgmt-password")
    bts_pw = request.config.getoption("--cpe-24-bts-password")
    updates: dict = {}
    if mgmt_ssid:
        updates["cpe_mgmt_ssid"] = mgmt_ssid
    if mgmt_pw is not None:
        updates["cpe_mgmt_password"] = str(mgmt_pw)
    if bts_ssid:
        updates["bts_ssid"] = bts_ssid
    if bts_pw is not None:
        updates["bts_password"] = str(bts_pw)
    return replace(config, **updates) if updates else config


def _cli_password(request, option: str) -> str:
    value = request.config.getoption(option)
    return "" if value is None else str(value)


def require_cpe_api_24_cli(config: CpeApi24Config) -> None:
    missing: list[str] = []
    if not config.bts_ssid:
        missing.append("--cpe-24-bts-ssid (or BTS SSH via --local-ipv6)")
    if config.bts_password == "":
        missing.append("--cpe-24-bts-password (or BTS SSH via --local-ipv6)")
    if missing:
        raise RuntimeError(
            "CPE API tests (169.254.254.1) need BTS btsconnect credentials:\n"
            "  BTS SSH: --profile=default --local-ipv6=<BTS>\n"
            "  or CLI: --cpe-24-bts-ssid / --cpe-24-bts-password\n"
            + "\n".join(f"  Missing: {m}" for m in missing)
            + "\nExample (join CPE mgmt Wi‑Fi on PC first, no mgmt CLI flags):\n"
            "  python3 -m pytest tests/API/test_API.py -m CPE_API_24 \\\n"
            "    --profile=default --local-ipv6=2401:4900:d0:40d4::17b8:0:330 -v\n"
        )
