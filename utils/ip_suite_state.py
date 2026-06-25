"""Session-scoped IP suite state (BTS/CPE LAN IPs + bench health) for smart preflight skip."""

from __future__ import annotations

import ipaddress
import json
import time
from pathlib import Path
from typing import Any

from utils.net_utils import normalize_ip

STATE_REL_PATH = Path("reports/artifacts/ip_suite_state.json")


def default_chain() -> dict[str, Any]:
    return {
        "ok": False,
        "bts_lan_ipv4": "",
        "cpe_lan_ipv4": "",
        "cpe_lan_ipv6": "",
        "suite_healthy": False,
        "bts_ping_ok": False,
        "cpe_ping_ok": False,
        "local_pc_ok": False,
        "remote_pc_ok": False,
        "updated_at": 0.0,
    }


def state_path(repo_root: Path | str) -> Path:
    return Path(repo_root) / STATE_REL_PATH


def load_chain(repo_root: Path | str) -> dict[str, Any]:
    path = state_path(repo_root)
    if not path.is_file():
        return default_chain()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        chain = default_chain()
        if isinstance(raw, dict):
            chain.update(raw)
        cpe4 = normalize_ip(str(chain.get("cpe_lan_ipv4", "")).split("/")[0])
        if cpe4 and _is_ipv6(cpe4):
            if not chain.get("cpe_lan_ipv6"):
                chain["cpe_lan_ipv6"] = cpe4
            chain["cpe_lan_ipv4"] = ""
        return chain
    except (OSError, json.JSONDecodeError):
        return default_chain()


def save_chain(repo_root: Path | str, chain: dict[str, Any]) -> None:
    path = state_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(chain)
    payload["updated_at"] = time.time()
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def apply_chain_to_cfg(cfg: dict[str, Any], chain: dict[str, Any]) -> None:
    cfg["_ip_suite_chain"] = chain
    bts = normalize_ip(str(chain.get("bts_lan_ipv4", "")).split("/")[0])
    cpe_v4 = normalize_ip(str(chain.get("cpe_lan_ipv4", "")).split("/")[0])
    if bts:
        cfg["_preflight_bts_lan_ipv4"] = bts
    if cpe_v4 and _is_ipv4(cpe_v4):
        cfg["_preflight_cpe_ipv4"] = cpe_v4


def _is_ipv4(host: str) -> bool:
    try:
        return isinstance(ipaddress.ip_address(host), ipaddress.IPv4Address)
    except ValueError:
        return False


def _is_ipv6(host: str) -> bool:
    try:
        return isinstance(ipaddress.ip_address(host), ipaddress.IPv6Address)
    except ValueError:
        return False


def _split_cpe_ip(cpe_ip: str) -> tuple[str, str]:
    clean = normalize_ip(str(cpe_ip).split("/")[0])
    if not clean:
        return "", ""
    if _is_ipv4(clean):
        return clean, ""
    if _is_ipv6(clean):
        return "", clean
    return "", ""


def mark_suite_healthy(
    chain: dict[str, Any],
    *,
    bts_ip: str = "",
    cpe_ip: str = "",
    local_pc_ok: bool = True,
    remote_pc_ok: bool = True,
    bts_ping_ok: bool = True,
    cpe_ping_ok: bool | None = None,
) -> None:
    bts = normalize_ip(str(bts_ip).split("/")[0])
    cpe_v4, cpe_v6 = _split_cpe_ip(cpe_ip)
    if bts:
        chain["bts_lan_ipv4"] = bts
    if cpe_v4:
        chain["cpe_lan_ipv4"] = cpe_v4
    if cpe_v6:
        chain["cpe_lan_ipv6"] = cpe_v6
    chain["local_pc_ok"] = bool(local_pc_ok)
    chain["remote_pc_ok"] = bool(remote_pc_ok)
    chain["bts_ping_ok"] = bool(bts_ping_ok)
    if cpe_ping_ok is not None:
        chain["cpe_ping_ok"] = bool(cpe_ping_ok)
    elif cpe_v4 or cpe_v6:
        chain["cpe_ping_ok"] = True
    chain["suite_healthy"] = bool(
        chain.get("bts_lan_ipv4")
        and chain.get("bts_ping_ok")
        and chain.get("local_pc_ok")
    )


def chain_allows_preflight_skip(
    chain: dict[str, Any],
    *,
    case_id: str,
    require_cpe: bool,
) -> bool:
    if not chain.get("ok", False):
        return False
    if not chain.get("suite_healthy", False):
        return False
    if not chain.get("bts_lan_ipv4"):
        return False
    if not chain.get("bts_ping_ok") or not chain.get("local_pc_ok"):
        return False
    if require_cpe:
        if not chain.get("cpe_ping_ok"):
            return False
        if not chain.get("remote_pc_ok"):
            return False
        from config.ip_test_cases import case_by_id

        stack = case_by_id(case_id).stack
        if stack == "v4" and not chain.get("cpe_lan_ipv4"):
            return False
        if stack == "v6" and not chain.get("cpe_lan_ipv6"):
            return False
        if stack == "dual" and not (chain.get("cpe_lan_ipv4") or chain.get("cpe_lan_ipv6")):
            return False
    return True
