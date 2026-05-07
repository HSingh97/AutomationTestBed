"""Profile loading and validation for testbed/runtime settings."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import ipaddress

import yaml


REQUIRED_TOP_LEVEL_KEYS = {"dut", "link", "recovery", "traffic"}


@dataclass(frozen=True)
class ProfileBundle:
    active_name: str
    recovery_name: str
    active: dict[str, Any]
    recovery: dict[str, Any]


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _profiles_dir() -> Path:
    return _repo_root() / "profiles"


def _load_profile_file(profile_name: str) -> dict[str, Any]:
    profile_path = _profiles_dir() / f"{profile_name}.yaml"
    if not profile_path.exists():
        raise FileNotFoundError(f"Profile file not found: {profile_path}")
    with profile_path.open("r", encoding="utf-8") as fp:
        data = yaml.safe_load(fp) or {}
    _validate_profile(profile_name, data)
    return data


def _validate_profile(profile_name: str, profile_data: dict[str, Any]) -> None:
    missing = REQUIRED_TOP_LEVEL_KEYS - set(profile_data.keys())
    if missing:
        raise ValueError(f"Profile '{profile_name}' missing required sections: {sorted(missing)}")
    dut = profile_data["dut"]
    ip_mode = str(dut.get("ip_mode", "ipv4")).lower()
    strict_ipv6 = bool(dut.get("strict_ipv6", False))
    if ip_mode == "ipv6" or strict_ipv6:
        if not dut.get("local_ipv6"):
            raise ValueError(f"Profile '{profile_name}' missing dut.local_ipv6 for IPv6 mode")
        try:
            ipaddress.IPv6Address(str(dut["local_ipv6"]))
        except ValueError as exc:
            raise ValueError(f"Profile '{profile_name}' has invalid dut.local_ipv6") from exc
        for idx, remote_ip in enumerate(dut.get("remote_ipv6s", [])):
            try:
                ipaddress.IPv6Address(str(remote_ip))
            except ValueError as exc:
                raise ValueError(f"Profile '{profile_name}' has invalid dut.remote_ipv6s[{idx}]") from exc
    elif not dut.get("local_ip"):
        raise ValueError(f"Profile '{profile_name}' missing dut.local_ip")
    if not profile_data["dut"].get("username"):
        raise ValueError(f"Profile '{profile_name}' missing dut.username")
    if not profile_data["dut"].get("password"):
        raise ValueError(f"Profile '{profile_name}' missing dut.password")


def _apply_cli_overrides(profile_data: dict[str, Any], *, local_ip: str | None, username: str | None, password: str | None):
    if local_ip:
        if profile_data["dut"].get("ip_mode") == "ipv6" or profile_data["dut"].get("strict_ipv6"):
            # In strict IPv6 mode, only accept IPv6-shaped CLI override values.
            if ":" in local_ip:
                profile_data["dut"]["local_ipv6"] = local_ip
        else:
            profile_data["dut"]["local_ip"] = local_ip
    if username:
        profile_data["dut"]["username"] = username
    if password:
        profile_data["dut"]["password"] = password


def load_profile_bundle(
    *,
    profile_name: str = "default",
    recovery_profile_name: str = "link_formation",
    local_ip: str | None = None,
    username: str | None = None,
    password: str | None = None,
) -> ProfileBundle:
    active = _load_profile_file(profile_name)
    recovery = _load_profile_file(recovery_profile_name)
    _apply_cli_overrides(active, local_ip=local_ip, username=username, password=password)
    _apply_cli_overrides(recovery, local_ip=local_ip, username=username, password=password)
    return ProfileBundle(
        active_name=profile_name,
        recovery_name=recovery_profile_name,
        active=active,
        recovery=recovery,
    )

