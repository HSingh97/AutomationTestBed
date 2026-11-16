"""Resolve Senao firmware images from lab PC tftpboot based on live device model."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from scrapli.driver.generic import AsyncGenericDriver

from utils.cpe_api_24_config import infer_fw_version_from_fw_path
from utils.parsers import clean_ssh_output
from utils.verify_output import print_kv_block

# Senao-UBR655-xxx-0.0.0.802-apps.tgz
_FIRMWARE_NAME_RE = re.compile(
    r"Senao-(UBR\d{3})-[^/\\]+\.tgz$",
    re.IGNORECASE,
)
_MODEL_RE = re.compile(r"UBR\d{3}", re.IGNORECASE)


@dataclass(frozen=True)
class FirmwarePick:
    """Device model + selected tftpboot image."""

    model: str
    image_path: Path
    version: str | None

    @property
    def image_name(self) -> str:
        return self.image_path.name


def parse_device_model(text: str) -> str | None:
    """Extract UBR650 / UBR655 / … from SSH or config text."""
    if not text:
        return None
    match = _MODEL_RE.search(text)
    return match.group(0).upper() if match else None


def _version_sort_key(version: str | None) -> tuple[int, ...]:
    if not version:
        return (0,)
    parts: list[int] = []
    for piece in re.split(r"[.\-_]", version):
        if piece.isdigit():
            parts.append(int(piece))
    return tuple(parts) if parts else (0,)


async def read_device_model(ssh: AsyncGenericDriver, *, timeout_s: int = 30) -> str:
    """
    Read hardware model from the DUT over SSH.

    Tries device-tree, UCI, board.json, then /etc/version.
    """
    probes = (
        "grep -oE 'UBR[0-9]{3}' /etc/board.json 2>/dev/null | head -1",
        "uci -q get system.@system[0].model 2>/dev/null",
        "uci -q get system.@system[0].hostname 2>/dev/null",
        "cat /etc/version 2>/dev/null",
        "hostname 2>/dev/null",
    )
    last_raw = ""
    for command in probes:
        try:
            response = await ssh.send_command(command, timeout_ops=min(timeout_s, 15))
        except Exception:
            continue
        raw = clean_ssh_output(response.result)
        if raw:
            last_raw = raw
        model = parse_device_model(raw)
        if model:
            return model
    raise RuntimeError(
        "Could not detect device model (UBRxxx) via SSH. "
        f"Last probe output: {last_raw[:200] or '(empty)'}"
    )


def _firmware_matches_model(path: Path, model: str) -> bool:
    match = _FIRMWARE_NAME_RE.match(path.name)
    if not match:
        return False
    return match.group(1).upper() == model.upper()


def list_firmware_for_model(model: str, fw_dir: str | Path) -> list[Path]:
    """All Senao-<model>-*.tgz files under tftpboot."""
    base = Path(str(fw_dir)).expanduser()
    if not base.is_dir():
        raise FileNotFoundError(f"Firmware directory not found: {base}")

    model_u = model.upper()
    matches = [
        p.resolve()
        for p in base.glob("Senao-UBR*.tgz")
        if p.is_file() and _firmware_matches_model(p, model_u)
    ]
    return matches


def pick_latest_firmware(candidates: list[Path]) -> Path:
    """
    Choose the newest image for a model.

    Sorts by parsed firmware version in the filename (highest first),
    then by file modification time as a tie-breaker.
    """
    if not candidates:
        raise FileNotFoundError("No firmware candidates supplied")

    def sort_key(path: Path) -> tuple[tuple[int, ...], float]:
        version = infer_fw_version_from_fw_path(str(path))
        return (_version_sort_key(version), path.stat().st_mtime)

    return sorted(candidates, key=sort_key, reverse=True)[0]


def latest_firmware_for_model(
    model: str,
    fw_dir: str | Path,
    *,
    cli_path: str | None = None,
) -> FirmwarePick:
    """
    Return the latest tftpboot image for a given UBR model.

    When ``cli_path`` is set, that file is used instead of auto-discovery.
    """
    if cli_path and str(cli_path).strip():
        image = Path(str(cli_path).strip()).expanduser()
        if not image.is_file():
            raise FileNotFoundError(f"Firmware not found: {image}")
        image = image.resolve()
        detected = parse_device_model(image.name) or model.upper()
        return FirmwarePick(
            model=detected,
            image_path=image,
            version=infer_fw_version_from_fw_path(str(image)),
        )

    model_u = model.upper()
    candidates = list_firmware_for_model(model_u, fw_dir)
    if not candidates:
        base = Path(str(fw_dir)).expanduser()
        raise FileNotFoundError(
            f"No Senao firmware for {model_u} under {base}. "
            f"Expected names like Senao-{model_u}-xxx-0.0.0.802-apps.tgz"
        )

    image = pick_latest_firmware(candidates)
    return FirmwarePick(
        model=model_u,
        image_path=image,
        version=infer_fw_version_from_fw_path(str(image)),
    )


async def resolve_firmware_for_device(
    ssh: AsyncGenericDriver,
    fw_dir: str | Path,
    *,
    cli_path: str | None = None,
    label: str = "device",
    log: bool = True,
) -> FirmwarePick:
    """
    Detect device model over SSH, then pick the latest matching FW in tftpboot.

    This is the main entry point for upgrade tests:

        pick = await resolve_firmware_for_device(root_ssh, "/tftpboot")
        # pick.model -> UBR655
        # pick.image_path -> /tftpboot/Senao-UBR655-xxx-0.0.0.802-apps.tgz
    """
    model = await read_device_model(ssh)
    pick = latest_firmware_for_model(model, fw_dir, cli_path=cli_path)
    if log:
        print_kv_block(
            f"Firmware resolve — {label}",
            {
                "Device model (SSH)": model,
                "Selected image": pick.image_name,
                "Image version": pick.version or "(unknown)",
                "tftpboot dir": str(Path(str(fw_dir)).expanduser()),
            },
        )
    return pick
