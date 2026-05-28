"""Vaunix Lab Brick LDA digital attenuator control (VNX_atten API via ctypes)."""

from __future__ import annotations

import ctypes
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Linux LDAhid + HR API: attenuation in 0.05 dB integer steps (multiply dB by 20).
ATTENUATION_STEP_DB = 0.05
LDAHID_UNITS_PER_DB = 20
MAX_DEVICES = 32
LVSTATUS_OK = 0


@dataclass
class LdaDeviceInfo:
    device_id: int
    serial: int
    model: str


def _db_to_hr(attenuation_db: float) -> int:
    return int(round(float(attenuation_db) / ATTENUATION_STEP_DB))


def _hr_to_db(hr_value: int) -> float:
    return round(hr_value * ATTENUATION_STEP_DB, 2)


def _lvstatus_failed(status: int) -> bool:
    """Vaunix LVSTATUS: 0 = OK; error codes are non-zero (often 0x8000xxxx)."""
    return int(status) != LVSTATUS_OK


def wifi_channel_to_mhz(channel: int | str) -> int:
    """Map Wi-Fi channel number to center frequency in MHz."""
    ch = int(channel)
    if ch == 14:
        return 2484
    if 1 <= ch <= 14:
        return 2407 + ch * 5
    if 36 <= ch <= 177:
        return 5000 + ch * 5
    raise ValueError(f"Unsupported Wi-Fi channel: {channel}")


class MockVaunixLda:
    """In-memory attenuator when hardware/SDK is unavailable."""

    def __init__(self) -> None:
        self._devices: dict[int, dict[str, Any]] = {
            0: {"serial": 602001, "model": "LDA-602", "att_hr": 0, "freq_hz": 5_800_000_000},
            1: {"serial": 602002, "model": "LDA-602", "att_hr": 0, "freq_hz": 5_800_000_000},
        }

    def get_num_devices(self) -> int:
        return len(self._devices)

    def get_dev_info(self) -> list[int]:
        return sorted(self._devices.keys())

    def init_device(self, device_id: int) -> None:
        if device_id not in self._devices:
            raise RuntimeError(f"Mock LDA: unknown device_id {device_id}")

    def close_device(self, device_id: int) -> None:
        pass

    def get_serial_number(self, device_id: int) -> int:
        return int(self._devices[device_id]["serial"])

    def get_model_name(self, device_id: int) -> str:
        return str(self._devices[device_id]["model"])

    def set_attenuation_db(self, device_id: int, attenuation_db: float) -> None:
        self._devices[device_id]["att_hr"] = _db_to_hr(attenuation_db)

    def get_attenuation_db(self, device_id: int) -> float:
        return _hr_to_db(int(self._devices[device_id]["att_hr"]))

    def set_working_frequency_mhz(self, device_id: int, frequency_mhz: float) -> None:
        self._devices[device_id]["freq_hz"] = int(frequency_mhz * 1_000_000)

    def get_working_frequency_mhz(self, device_id: int) -> float:
        return self._devices[device_id]["freq_hz"] / 1_000_000.0


class VaunixLdaApi:
    """
    Thin wrapper over Vaunix ``VNX_atten`` / ``VNX_atten64`` (Windows) or ``libVNX_atten`` (Linux).

    Install Vaunix Lab Brick SDK and set ``dll_path`` / ``VAUNIX_LDA_SDK_PATH`` to the folder
    containing the library. Use ``backend=mock`` for dry-run without hardware.
    """

    def __init__(
        self,
        *,
        dll_path: str | Path | None = None,
        backend: str = "auto",
    ) -> None:
        self._backend = (backend or "auto").lower()
        self._dll_path = Path(dll_path) if dll_path else None
        self._mock: MockVaunixLda | None = None
        self._dll: Any = None
        self._device_ids: list[int] = []
        self._open = False
        self._use_legacy_att = False
        self._devices_ready: set[int] = set()

    def open(self) -> None:
        if self._open:
            return
        if self._backend == "mock":
            self._mock = MockVaunixLda()
            self._device_ids = self._mock.get_dev_info()
            self._open = True
            return
        try:
            self._dll = self._load_library()
            self._bind_functions()
            if hasattr(self._dll, "fnLDA_SetTestMode"):
                self._dll.fnLDA_SetTestMode(False)
            if hasattr(self._dll, "fnLDA_Init"):
                self._dll.fnLDA_Init()
            count = int(self._dll.fnLDA_GetNumDevices())
            if count <= 0:
                raise RuntimeError("No Vaunix LDA devices found on USB.")
            ids = (ctypes.c_int * MAX_DEVICES)()
            active = int(self._dll.fnLDA_GetDevInfo(ids))
            self._device_ids = [int(ids[i]) for i in range(active)]
            self._devices_ready.clear()
            for dev_id in self._device_ids:
                self._prepare_device(dev_id)
            self._use_legacy_att = not hasattr(self._dll, "fnLDA_SetAttenuationHR")
            self._open = True
        except Exception as exc:
            if self._backend == "auto":
                print(f"    -> [ATTEN] Vaunix SDK unavailable ({exc}); using mock backend.")
                self._backend = "mock"
                self._mock = MockVaunixLda()
                self._device_ids = self._mock.get_dev_info()
                self._open = True
                return
            raise

    def close(self) -> None:
        if not self._open:
            return
        if self._mock:
            self._mock = None
        elif self._dll:
            for dev_id in self._device_ids:
                try:
                    self._dll.fnLDA_CloseDevice(dev_id)
                except Exception:
                    pass
        self._device_ids = []
        self._devices_ready = set()
        self._open = False

    def _prepare_device(self, device_id: int) -> None:
        """Open HID session and enable RF path (matches Vaunix reference apps)."""
        if self._mock:
            return
        if device_id in self._devices_ready:
            return
        status = int(self._dll.fnLDA_InitDevice(device_id))
        if _lvstatus_failed(status):
            raise RuntimeError(f"fnLDA_InitDevice({device_id}) failed: {status:#x}")
        if hasattr(self._dll, "fnLDA_SetRFOn"):
            rf_status = int(self._dll.fnLDA_SetRFOn(device_id, True))
            if _lvstatus_failed(rf_status):
                print(f"    -> [ATTEN] SetRFOn({device_id}) warning: {rf_status:#x}")
        self._devices_ready.add(device_id)
        time.sleep(0.75)

    def list_devices(self) -> list[LdaDeviceInfo]:
        self.open()
        if self._mock:
            return [
                LdaDeviceInfo(
                    device_id=dev_id,
                    serial=self._mock.get_serial_number(dev_id),
                    model=self._mock.get_model_name(dev_id),
                )
                for dev_id in self._device_ids
            ]
        devices: list[LdaDeviceInfo] = []
        for dev_id in self._device_ids:
            serial = int(self._dll.fnLDA_GetSerialNumber(dev_id))
            name_len = int(self._dll.fnLDA_GetModelName(dev_id, None))
            buf = ctypes.create_string_buffer(name_len + 1)
            self._dll.fnLDA_GetModelName(dev_id, buf)
            devices.append(LdaDeviceInfo(device_id=dev_id, serial=serial, model=buf.value.decode()))
        return devices

    def resolve_device_id(
        self,
        chain: int,
        *,
        serial: int | None = None,
        device_id: int | None = None,
        lab_brick_id: int | None = None,
    ) -> int:
        """Resolve Linux DEVID (1..n) from profile device_id, serial, or chain index."""
        self.open()
        devices = self.list_devices()
        for candidate in (device_id, lab_brick_id, serial):
            if candidate is None:
                continue
            cid = int(candidate)
            for info in devices:
                if info.device_id == cid or info.serial == cid:
                    return info.device_id
        if chain < 0 or chain >= len(self._device_ids):
            raise RuntimeError(
                f"Chain index {chain} out of range; {len(self._device_ids)} device(s) connected."
            )
        return self._device_ids[chain]

    def set_attenuation_db(self, device_id: int, attenuation_db: float) -> None:
        self.open()
        if self._mock:
            self._mock.set_attenuation_db(device_id, attenuation_db)
            return
        self._prepare_device(device_id)
        if self._use_legacy_att:
            units = int(round(float(attenuation_db) * LDAHID_UNITS_PER_DB))
            status = int(self._dll.fnLDA_SetAttenuation(device_id, units))
            if _lvstatus_failed(status):
                raise RuntimeError(f"fnLDA_SetAttenuation({device_id}, {units}) failed: {status:#x}")
            readback = self.get_attenuation_db(device_id)
            if abs(readback - float(attenuation_db)) > 1.0:
                print(
                    f"    -> [ATTEN] Warning: dev {device_id} readback {readback} dB "
                    f"!= requested {attenuation_db} dB"
                )
            time.sleep(0.1)
            return
        hr = _db_to_hr(attenuation_db)
        status = int(self._dll.fnLDA_SetAttenuationHR(device_id, hr))
        if _lvstatus_failed(status):
            raise RuntimeError(f"fnLDA_SetAttenuationHR({device_id}, {hr}) failed: {status:#x}")

    def get_attenuation_db(self, device_id: int) -> float:
        self.open()
        if self._mock:
            return self._mock.get_attenuation_db(device_id)
        if self._use_legacy_att:
            units = int(self._dll.fnLDA_GetAttenuation(device_id))
            return round(units / float(LDAHID_UNITS_PER_DB), 2)
        hr = int(self._dll.fnLDA_GetAttenuationHR(device_id))
        return _hr_to_db(hr)

    def set_working_frequency_mhz(self, device_id: int, frequency_mhz: float) -> None:
        """Set working frequency (MHz). Only HiRes / frequency-tuned LDA models (not LDA-602)."""
        self.open()
        if self._mock:
            self._mock.set_working_frequency_mhz(device_id, frequency_mhz)
            return
        if not hasattr(self._dll, "fnLDA_SetWorkingFrequency"):
            print(f"    -> [ATTEN] SetWorkingFrequency not in SDK; skipping {frequency_mhz} MHz")
            return
        # API typically uses Hz as integer.
        # SDK expects 100 kHz units (see fnLDA_SetWorkingFrequency in LDAhid.c).
        freq_100khz = int(round(frequency_mhz * 10.0))
        status = int(self._dll.fnLDA_SetWorkingFrequency(device_id, freq_100khz))
        if status != 0:
            print(
                f"    -> [ATTEN] SetWorkingFrequency skipped for dev {device_id} "
                f"({frequency_mhz} MHz / {freq_100khz}×100kHz, status={status})"
            )

    def _load_library(self) -> Any:
        search_dirs: list[Path] = []
        if self._dll_path:
            search_dirs.append(self._dll_path)
        env = os.environ.get("VAUNIX_LDA_SDK_PATH", "").strip()
        if env:
            search_dirs.append(Path(env))
        repo_root = Path(__file__).resolve().parents[1]
        repo_vendor = repo_root / "vendor" / "vaunix_linux_sdk" / "lib"
        search_dirs.extend(
            [
                repo_vendor,
                Path("/usr/lib"),
                Path("/usr/local/lib"),
                Path("C:/Program Files/Vaunix/Lab Brick LDA Software"),
                Path("C:/Vaunix"),
            ]
        )

        if sys.platform == "win32":
            names = ["VNX_atten64.dll", "VNX_atten.dll"]
        else:
            names = [
                "libLDAhid.so",
                "liblda.so",
                "libVNX_atten.so",
                "libvnx_atten.so",
                "VNX_atten64.so",
            ]

        for directory in search_dirs:
            for name in names:
                candidate = directory / name
                if candidate.exists():
                    return ctypes.CDLL(str(candidate))

        for name in names:
            try:
                return ctypes.CDLL(name)
            except OSError:
                continue
        raise OSError(
            "Vaunix LDA library not found. Set VAUNIX_LDA_SDK_PATH or attenuator.dll_path in profile."
        )

    def _bind_functions(self) -> None:
        dll = self._dll
        dll.fnLDA_GetNumDevices.restype = ctypes.c_int
        dll.fnLDA_GetNumDevices.argtypes = []

        dll.fnLDA_GetDevInfo.restype = ctypes.c_int
        dll.fnLDA_GetDevInfo.argtypes = [ctypes.POINTER(ctypes.c_int)]

        dll.fnLDA_InitDevice.restype = ctypes.c_int
        dll.fnLDA_InitDevice.argtypes = [ctypes.c_int]

        dll.fnLDA_CloseDevice.restype = ctypes.c_int
        dll.fnLDA_CloseDevice.argtypes = [ctypes.c_int]

        dll.fnLDA_GetSerialNumber.restype = ctypes.c_int
        dll.fnLDA_GetSerialNumber.argtypes = [ctypes.c_int]

        dll.fnLDA_GetModelName.restype = ctypes.c_int
        dll.fnLDA_GetModelName.argtypes = [ctypes.c_int, ctypes.c_char_p]

        if hasattr(dll, "fnLDA_Init"):
            dll.fnLDA_Init.restype = None
            dll.fnLDA_Init.argtypes = []

        if hasattr(dll, "fnLDA_SetTestMode"):
            dll.fnLDA_SetTestMode.restype = None
            dll.fnLDA_SetTestMode.argtypes = [ctypes.c_bool]

        if hasattr(dll, "fnLDA_SetRFOn"):
            dll.fnLDA_SetRFOn.restype = ctypes.c_int
            dll.fnLDA_SetRFOn.argtypes = [ctypes.c_int, ctypes.c_bool]
            dll.fnLDA_GetRF_On.restype = ctypes.c_int
            dll.fnLDA_GetRF_On.argtypes = [ctypes.c_int]

        if hasattr(dll, "fnLDA_SetAttenuation"):
            dll.fnLDA_SetAttenuation.restype = ctypes.c_int
            dll.fnLDA_SetAttenuation.argtypes = [ctypes.c_int, ctypes.c_int]
            dll.fnLDA_GetAttenuation.restype = ctypes.c_int
            dll.fnLDA_GetAttenuation.argtypes = [ctypes.c_int]

        if hasattr(dll, "fnLDA_SetAttenuationHR"):
            dll.fnLDA_SetAttenuationHR.restype = ctypes.c_int
            dll.fnLDA_SetAttenuationHR.argtypes = [ctypes.c_int, ctypes.c_int]
            dll.fnLDA_GetAttenuationHR.restype = ctypes.c_int
            dll.fnLDA_GetAttenuationHR.argtypes = [ctypes.c_int]

        if hasattr(dll, "fnLDA_SetWorkingFrequency"):
            dll.fnLDA_SetWorkingFrequency.restype = ctypes.c_int
            dll.fnLDA_SetWorkingFrequency.argtypes = [ctypes.c_int, ctypes.c_int]
        if hasattr(dll, "fnLDA_GetWorkingFrequency"):
            dll.fnLDA_GetWorkingFrequency.restype = ctypes.c_int
            dll.fnLDA_GetWorkingFrequency.argtypes = [ctypes.c_int]
