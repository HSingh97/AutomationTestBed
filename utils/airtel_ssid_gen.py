"""AIRTEL_SSID_GEN wrapper — deterministic SSID/password from device serial."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SSID_RE = re.compile(r"Generated SSID:\s*(\S+)", re.IGNORECASE)
PASSWORD_RE = re.compile(r"Generated Password:\s*(\S+)", re.IGNORECASE)


@dataclass(frozen=True)
class LinkCredentials:
    serial: str
    radio_type: str
    auth_mode: str
    encryption_mode: str
    ssid: str
    password: str


def resolve_airtel_gen_binary(profile_link: dict | None = None) -> Path:
    """Locate AIRTEL_SSID_GEN: profile path → vendor/ → ~/Downloads/."""
    link = profile_link or {}
    explicit = str(link.get("airtel_gen_path", "")).strip()
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file():
            return path
        raise FileNotFoundError(f"airtel_gen_path not found: {path}")

    for candidate in (
        REPO_ROOT / "vendor" / "AIRTEL_SSID_GEN",
        Path.home() / "Downloads" / "AIRTEL_SSID_GEN",
    ):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "AIRTEL_SSID_GEN not found. Set link.airtel_gen_path or place binary under vendor/AIRTEL_SSID_GEN"
    )


def generate_link_credentials(
    serial: str,
    *,
    radio_type: str = "5G",
    auth_mode: str = "32",
    encryption_mode: str = "8",
    profile_link: dict | None = None,
) -> LinkCredentials:
    """
    Run AIRTEL_SSID_GEN and parse SSID + password.

    Example:
        ./AIRTEL_SSID_GEN 2480XCE14X3C 5G 32 8
    """
    serial = str(serial or "").strip().upper()
    if not serial:
        raise ValueError("serial number is required for AIRTEL_SSID_GEN")

    binary = resolve_airtel_gen_binary(profile_link)
    proc = subprocess.run(
        [str(binary), serial, radio_type, str(auth_mode), str(encryption_mode)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    combined = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        raise RuntimeError(
            f"AIRTEL_SSID_GEN failed (rc={proc.returncode}): {combined.strip()[:400]}"
        )

    ssid_m = SSID_RE.search(combined)
    pass_m = PASSWORD_RE.search(combined)
    if not ssid_m or not pass_m:
        raise RuntimeError(f"AIRTEL_SSID_GEN output not parsed: {combined.strip()[:400]}")

    return LinkCredentials(
        serial=serial,
        radio_type=radio_type,
        auth_mode=str(auth_mode),
        encryption_mode=str(encryption_mode),
        ssid=ssid_m.group(1).strip(),
        password=pass_m.group(1).strip(),
    )
