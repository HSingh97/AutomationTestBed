"""Sanity-only firmware discovery from lab PC /tftpboot."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from scrapli.driver.generic import AsyncGenericDriver

from utils.sanity_commands import MODEL_PROBES, SanityCommands
from utils.sanity_ssh import normalize_fw_version, sanity_ssh_run

_FIRMWARE_NAME_RE = re.compile(r"Senao-(UBR\d{3})-[^/\\]+\.tgz$", re.IGNORECASE)
_MODEL_RE = re.compile(r"UBR\d{3}", re.IGNORECASE)
_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+(?:\.\d+)?)")


@dataclass(frozen=True)
class SanityFirmwarePick:
    model: str
    image_path: Path
    version: str | None

    @property
    def image_name(self) -> str:
        return self.image_path.name


def parse_device_model(text: str) -> str | None:
    if not text:
        return None
    match = _MODEL_RE.search(text)
    return match.group(0).upper() if match else None


def infer_fw_version(path: str | Path | None) -> str | None:
    if not path:
        return None
    match = _VERSION_RE.search(Path(path).name)
    return match.group(1) if match else None


def _version_sort_key(version: str | None) -> tuple[int, ...]:
    if not version:
        return (0,)
    parts: list[int] = []
    for piece in re.split(r"[.\-_]", version):
        if piece.isdigit():
            parts.append(int(piece))
    return tuple(parts) if parts else (0,)


async def read_device_model(ssh: AsyncGenericDriver, *, timeout_s: int = 30) -> str:
    last_raw = ""
    for command in MODEL_PROBES:
        try:
            raw = await sanity_ssh_run(ssh, command, timeout_s=min(timeout_s, 15))
        except Exception:
            continue
        if raw:
            last_raw = raw
        model = parse_device_model(raw)
        if model:
            return model
    raise RuntimeError(
        "[sanity] Could not detect UBR model via SSH "
        "(tried /etc/ademodel, hostname, wireless). "
        f"Last probe: {last_raw[:200] or '(empty)'}"
    )


def _firmware_matches_model(path: Path, model: str) -> bool:
    match = _FIRMWARE_NAME_RE.match(path.name)
    return bool(match and match.group(1).upper() == model.upper())


def list_firmware_for_model(model: str, fw_dir: str | Path) -> list[Path]:
    base = Path(str(fw_dir)).expanduser()
    if not base.is_dir():
        raise FileNotFoundError(f"[sanity] Firmware directory not found: {base}")
    model_u = model.upper()
    return [
        p.resolve()
        for p in base.glob("Senao-UBR*.tgz")
        if p.is_file() and _firmware_matches_model(p, model_u)
    ]


def pick_latest_firmware(candidates: list[Path]) -> Path:
    if not candidates:
        raise FileNotFoundError("[sanity] No firmware candidates supplied")

    def sort_key(path: Path) -> tuple[tuple[int, ...], float]:
        return (_version_sort_key(infer_fw_version(path)), path.stat().st_mtime)

    return sorted(candidates, key=sort_key, reverse=True)[0]


def pick_upgrade_firmware(
    model: str,
    fw_dir: str | Path,
    *,
    current_fw: str = "",
    cli_path: str | None = None,
) -> SanityFirmwarePick:
    """
    Pick tftpboot image for upgrade.

    Prefers the newest image whose version differs from ``current_fw`` so the
    flash actually changes /etc/version. Falls back to newest if only one version exists.
    """
    if cli_path and str(cli_path).strip():
        return latest_firmware_for_model(model, fw_dir, cli_path=cli_path)

    model_u = model.upper()
    candidates = list_firmware_for_model(model_u, fw_dir)
    if not candidates:
        base = Path(str(fw_dir)).expanduser()
        raise FileNotFoundError(
            f"[sanity] No Senao-{model_u} image under {base}. "
            f"Expected Senao-{model_u}-*.tgz"
        )

    current = normalize_fw_version(current_fw) if current_fw else ""
    sorted_cands = sorted(
        candidates,
        key=lambda p: (_version_sort_key(infer_fw_version(p)), p.stat().st_mtime),
        reverse=True,
    )
    current_key = _version_sort_key(current) if current else ()

    if current:
        for path in sorted_cands:
            version = infer_fw_version(path)
            if version and _version_sort_key(version) > current_key:
                return SanityFirmwarePick(
                    model=model_u,
                    image_path=path.resolve(),
                    version=version,
                )
        print(
            f"[sanity] No newer {model_u} image than current FW {current}; "
            f"re-flashing newest ({sorted_cands[0].name})",
            flush=True,
        )

    image = sorted_cands[0]
    return SanityFirmwarePick(
        model=model_u,
        image_path=image.resolve(),
        version=infer_fw_version(image),
    )


def latest_firmware_for_model(
    model: str,
    fw_dir: str | Path,
    *,
    cli_path: str | None = None,
) -> SanityFirmwarePick:
    if cli_path and str(cli_path).strip():
        image = Path(str(cli_path).strip()).expanduser()
        if not image.is_file():
            raise FileNotFoundError(f"[sanity] Firmware not found: {image}")
        image = image.resolve()
        detected = parse_device_model(image.name) or model.upper()
        return SanityFirmwarePick(
            model=detected,
            image_path=image,
            version=infer_fw_version(image),
        )

    model_u = model.upper()
    candidates = list_firmware_for_model(model_u, fw_dir)
    if not candidates:
        base = Path(str(fw_dir)).expanduser()
        raise FileNotFoundError(
            f"[sanity] No Senao-{model_u} image under {base}. "
            f"Expected Senao-{model_u}-*.tgz"
        )
    image = pick_latest_firmware(candidates)
    return SanityFirmwarePick(
        model=model_u,
        image_path=image,
        version=infer_fw_version(image),
    )


async def resolve_sanity_firmware(
    ssh: AsyncGenericDriver,
    fw_dir: str | Path,
    *,
    cli_path: str | None = None,
    label: str = "device",
    current_fw: str = "",
) -> SanityFirmwarePick:
    model = await read_device_model(ssh)
    current = current_fw or normalize_fw_version(await read_fw_version(ssh))
    pick = pick_upgrade_firmware(
        model,
        fw_dir,
        current_fw=current,
        cli_path=cli_path,
    )
    print(
        f"[sanity] {label} firmware: model={model} current={current or '(unknown)'} "
        f"image={pick.image_name} target={pick.version or '(unknown)'} "
        f"dir={Path(str(fw_dir)).expanduser()}",
        flush=True,
    )
    return pick


async def read_fw_version(ssh: AsyncGenericDriver) -> str:
    return normalize_fw_version(await sanity_ssh_run(ssh, SanityCommands.GET_FW_VERSION))


def list_all_firmware(fw_dir: str | Path) -> list[Path]:
    base = Path(str(fw_dir)).expanduser()
    if not base.is_dir():
        raise FileNotFoundError(f"[sanity] Firmware directory not found: {base}")
    return sorted(p.resolve() for p in base.glob("Senao-UBR*.tgz") if p.is_file())


def pick_wrong_model_firmware(
    device_model: str,
    fw_dir: str | Path,
    *,
    cli_path: str | None = None,
) -> SanityFirmwarePick:
    """
    Pick a Senao-UBR*.tgz image whose model differs from ``device_model``.

    Used by SANITY_04 (wrong-model GUI upload must be rejected).
    """
    model_u = device_model.upper()
    if cli_path and str(cli_path).strip():
        image = Path(str(cli_path).strip()).expanduser()
        if not image.is_file():
            base = Path(str(fw_dir)).expanduser()
            candidate = base / str(cli_path).strip()
            if candidate.is_file():
                image = candidate
            else:
                raise FileNotFoundError(f"[sanity] Wrong-model firmware not found: {image}")
        image = image.resolve()
        image_model = parse_device_model(image.name)
        if image_model and image_model == model_u:
            raise ValueError(
                f"[sanity] --sanity-wrong-model-fw {image.name} matches device model {model_u}; "
                "pick a different-model image for SANITY_04"
            )
        return SanityFirmwarePick(
            model=image_model or "UNKNOWN",
            image_path=image,
            version=infer_fw_version(image),
        )

    images = list_all_firmware(fw_dir)
    if not images:
        base = Path(str(fw_dir)).expanduser()
        raise FileNotFoundError(f"[sanity] No Senao-UBR*.tgz images under {base}")

    mismatched = [
        p
        for p in images
        if (fw_model := parse_device_model(p.name)) and fw_model.upper() != model_u
    ]
    if not mismatched:
        base = Path(str(fw_dir)).expanduser()
        raise FileNotFoundError(
            f"[sanity] No wrong-model firmware for {model_u} under {base}. "
            f"Add e.g. Senao-UBR660-*.tgz when DUT is {model_u}, "
            "or pass --sanity-wrong-model-fw."
        )

    image = pick_latest_firmware(mismatched)
    fw_model = parse_device_model(image.name) or "UNKNOWN"
    return SanityFirmwarePick(
        model=fw_model,
        image_path=image,
        version=infer_fw_version(image),
    )


async def resolve_wrong_model_firmware(
    ssh: AsyncGenericDriver,
    fw_dir: str | Path,
    *,
    cli_path: str | None = None,
    label: str = "device",
) -> tuple[str, SanityFirmwarePick]:
    model = await read_device_model(ssh)
    pick = pick_wrong_model_firmware(model, fw_dir, cli_path=cli_path)
    print(
        f"[sanity] {label} wrong-model FW: device={model} "
        f"image={pick.image_name} (model={pick.model}) "
        f"dir={Path(str(fw_dir)).expanduser()}",
        flush=True,
    )
    return model, pick
