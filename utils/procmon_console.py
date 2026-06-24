"""Serial console transport for the Process Monitor suite.

The BTS exposes a USB serial console (typically ``/dev/ttyUSB0`` on the
Jenkins agent host). Using it gives ProcessMonitor a "side channel" that
keeps working when SSH dies — so we can:

* Issue ``kill`` / ``ubus`` commands without depending on the WAN link.
* Read kernel + procd messages that never make it to ``logread``.
* Trigger a clean ``reboot`` between sweep phases (e.g. after the SEGV
  cases destroy services) without arming a dead-man timer on the device.

The wrapper is opt-in (``--procmon-serial-device``). If the port cannot be
opened (e.g. ``minicom`` is holding it, or the user is not in ``dialout``)
the fixture logs a warning and the suite falls back to the SSH-only path.

Usage::

    console = await ProcmonConsole.open("/dev/ttyUSB0", password="...")
    out = await console.run("ubus call service list", timeout_s=10)
    await console.reboot_and_wait()
    await console.close()
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    import serial as _pyserial  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - pyserial is in requirements.txt
    _pyserial = None  # type: ignore[assignment]


# OpenWrt prompts are typically `root@hostname:~#` (login shell) or
# `root@(none):/#` early in boot. Match both with a generous regex.
_PROMPT_RE = re.compile(r"\r?\nroot@[\w\-.\(\)]+:[^\r\n]*[#$]\s*$")
_LOGIN_PROMPT_RE = re.compile(r"login:\s*$", re.IGNORECASE)
_PASSWORD_PROMPT_RE = re.compile(r"password:\s*$", re.IGNORECASE)
_BOOT_DONE_RE = re.compile(
    r"Please press Enter to activate this console|Welcome to .*OpenWrt|"
    r"procd: - init complete -|"
    r"BusyBox v\d|"
    r"root@[\w\-.\(\)]+:",
    re.IGNORECASE,
)


@dataclass
class ProcmonConsoleConfig:
    """User-configurable knobs for the serial console transport."""

    device: str
    baudrate: int = 115200
    username: str = "root"
    password: str = ""
    log_path: Optional[Path] = None
    open_timeout_s: float = 5.0
    response_timeout_s: float = 10.0


class ProcmonConsoleError(RuntimeError):
    """Raised when the serial console can't be opened or commanded."""


class ProcmonConsole:
    """Async-friendly OpenWrt serial console driver."""

    def __init__(self, port, cfg: ProcmonConsoleConfig):
        self._port = port
        self._cfg = cfg
        self._lock = asyncio.Lock()
        self._log_fh = None
        if cfg.log_path:
            cfg.log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log_fh = cfg.log_path.open("ab", buffering=0)

    # ------------------------------------------------------------------ open

    @classmethod
    async def open(
        cls,
        device: str,
        *,
        baudrate: int = 115200,
        username: str = "root",
        password: str = "",
        log_path: Optional[Path] = None,
        open_timeout_s: float = 5.0,
        response_timeout_s: float = 10.0,
    ) -> "ProcmonConsole":
        """Open the serial port (or socket://host:port URI) and ensure we're at a shell prompt.

        Accepts:
          * Local devices: ``/dev/ttyUSB0`` (single owner; nothing else may hold the port).
          * pyserial URIs: ``socket://host:port`` (recommended — works with ser2net /
            tio --socket so minicom and the automation can share the line).
        """
        if _pyserial is None:
            raise ProcmonConsoleError(
                "pyserial is not installed. Add 'pyserial' to requirements.txt."
            )

        is_url = "://" in device
        if not is_url:
            if not os.path.exists(device):
                raise ProcmonConsoleError(f"Serial device not found: {device}")
            if not os.access(device, os.R_OK | os.W_OK):
                raise ProcmonConsoleError(
                    f"No read/write permission on {device}. "
                    f"Add the user to the 'dialout' group "
                    f"(`sudo usermod -aG dialout $USER` then log out/in), "
                    f"or front the port with ser2net/tio so it's reachable via "
                    f"socket://host:port instead."
                )

        cfg = ProcmonConsoleConfig(
            device=device,
            baudrate=baudrate,
            username=username,
            password=password,
            log_path=log_path,
            open_timeout_s=open_timeout_s,
            response_timeout_s=response_timeout_s,
        )

        def _blocking_open():
            if is_url:
                # Shared-access mode (ser2net / tio --socket / pyserial URI).
                port = _pyserial.serial_for_url(device, do_not_open=True)
                port.baudrate = baudrate
                port.bytesize = 8
                port.parity = "N"
                port.stopbits = 1
                port.timeout = 0.2
                port.write_timeout = 2.0
                port.open()
                return port
            return _pyserial.Serial(
                port=device,
                baudrate=baudrate,
                bytesize=8,
                parity="N",
                stopbits=1,
                timeout=0.2,
                write_timeout=2.0,
                exclusive=True,
            )

        try:
            port = await asyncio.wait_for(
                asyncio.to_thread(_blocking_open), timeout=open_timeout_s
            )
        except _pyserial.SerialException as exc:  # type: ignore[union-attr]
            raise ProcmonConsoleError(
                f"Could not open {device}: {exc}. "
                f"{'Is ser2net/tio running on the configured port?' if is_url else 'Is minicom/tio/picocom still holding the port?'}"
            ) from exc
        except asyncio.TimeoutError as exc:
            raise ProcmonConsoleError(
                f"Timed out opening {device} within {open_timeout_s}s"
            ) from exc

        console = cls(port, cfg)
        try:
            await console._ensure_root_prompt()
        except Exception:
            await console.close()
            raise
        return console

    # ----------------------------------------------------------------- close

    async def close(self) -> None:
        try:
            await asyncio.to_thread(self._port.close)
        except Exception:
            pass
        if self._log_fh is not None:
            try:
                self._log_fh.close()
            except Exception:
                pass
            self._log_fh = None

    # ----------------------------------------------------------------- I/O

    async def _write(self, data: bytes) -> None:
        await asyncio.to_thread(self._port.write, data)

    async def _read_some(self) -> bytes:
        return await asyncio.to_thread(self._port.read, 4096)

    def _log_bytes(self, data: bytes) -> None:
        if self._log_fh is None or not data:
            return
        try:
            self._log_fh.write(data)
        except Exception:
            pass

    async def _drain(self, settle_s: float = 0.1) -> bytes:
        """Drain whatever is sitting in the read buffer right now."""
        chunks: list[bytes] = []
        deadline = time.monotonic() + settle_s
        while time.monotonic() < deadline:
            chunk = await self._read_some()
            if chunk:
                chunks.append(chunk)
                self._log_bytes(chunk)
                deadline = time.monotonic() + settle_s
            else:
                await asyncio.sleep(0.02)
        return b"".join(chunks)

    async def _read_until(
        self,
        pattern: re.Pattern[str],
        *,
        timeout_s: float,
    ) -> str:
        """Read until ``pattern`` matches the accumulated text or we time out."""
        buf = bytearray()
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            chunk = await self._read_some()
            if chunk:
                buf.extend(chunk)
                self._log_bytes(chunk)
                text = buf.decode("utf-8", errors="replace")
                if pattern.search(text):
                    return text
            else:
                await asyncio.sleep(0.05)
        raise ProcmonConsoleError(
            f"Console wait timed out after {timeout_s:.1f}s "
            f"(pattern={pattern.pattern!r}); last bytes={bytes(buf[-160:])!r}"
        )

    # ----------------------------------------------------------------- session setup

    async def _ensure_root_prompt(self) -> None:
        """Send a newline, log in if needed, settle at a shell prompt."""
        async with self._lock:
            await self._write(b"\r\n")
            buf = await self._drain(settle_s=0.5)
            text = buf.decode("utf-8", errors="replace")
            if _PROMPT_RE.search(text):
                return
            if _PASSWORD_PROMPT_RE.search(text):
                await self._write(self._cfg.password.encode() + b"\r\n")
                await self._read_until(_PROMPT_RE, timeout_s=self._cfg.response_timeout_s)
                return
            if _LOGIN_PROMPT_RE.search(text):
                await self._write(self._cfg.username.encode() + b"\r\n")
                await self._read_until(
                    _PASSWORD_PROMPT_RE, timeout_s=self._cfg.response_timeout_s
                )
                await self._write(self._cfg.password.encode() + b"\r\n")
                await self._read_until(_PROMPT_RE, timeout_s=self._cfg.response_timeout_s)
                return
            await self._write(b"\r\n")
            try:
                await self._read_until(_PROMPT_RE, timeout_s=2.0)
                return
            except ProcmonConsoleError:
                pass
            await self._write(self._cfg.username.encode() + b"\r\n")
            try:
                await self._read_until(
                    _PASSWORD_PROMPT_RE, timeout_s=self._cfg.response_timeout_s
                )
            except ProcmonConsoleError:
                await self._read_until(_PROMPT_RE, timeout_s=self._cfg.response_timeout_s)
                return
            await self._write(self._cfg.password.encode() + b"\r\n")
            await self._read_until(_PROMPT_RE, timeout_s=self._cfg.response_timeout_s)

    # ----------------------------------------------------------------- exec

    async def run(self, command: str, *, timeout_s: float = 10.0) -> str:
        """Send a command and return its stdout (excluding the echoed command and prompt).

        Wraps the command between two unique sentinels so the captured payload
        survives shell echo and trailing prompt rendering:

            printf '\\n<START>\\n'; <command>; printf '\\n<END>\\n'
        """
        nonce = f"{int(time.time() * 1000)}_{os.urandom(2).hex()}"
        start = f"__PRC_BEG_{nonce}__"
        end = f"__PRC_END_{nonce}__"
        wrapped = f"printf '\\n{start}\\n'; {command}; printf '\\n{end}\\n'\r\n"
        async with self._lock:
            await self._drain(settle_s=0.05)
            await self._write(wrapped.encode())
            sentinel = re.compile(re.escape(end))
            text = await self._read_until(sentinel, timeout_s=timeout_s)
        return _extract_payload_between(text, start=start, end=end)

    async def kill_pids(
        self,
        pids: list[int],
        *,
        signal: str = "KILL",
        timeout_s: float = 10.0,
    ) -> str:
        if not pids:
            return ""
        sig = signal.upper().replace("SIG", "")
        flag = "-9" if sig == "KILL" else f"-{sig}"
        return await self.run(
            f"kill {flag} {' '.join(str(p) for p in pids)} 2>/dev/null || true",
            timeout_s=timeout_s,
        )

    async def reboot_and_wait(
        self,
        *,
        boot_timeout_s: float = 180.0,
    ) -> None:
        """Reboot the device via console and wait for the next root prompt."""
        async with self._lock:
            await self._write(b"reboot\r\n")
            await asyncio.sleep(2.0)
            try:
                await self._read_until(_BOOT_DONE_RE, timeout_s=boot_timeout_s)
            except ProcmonConsoleError:
                pass
            await self._write(b"\r\n")
            await asyncio.sleep(0.5)
        await self._ensure_root_prompt()


def _extract_payload_between(text: str, *, start: str, end: str) -> str:
    """Return the text between the LAST start sentinel and the LAST end sentinel.

    The shell echoes the whole wrapped line first, so the start/end tokens each
    appear twice (once in the echoed command, once printed by ``printf``). We
    want the SECOND occurrence of each — that's the actual delimited payload.
    """
    starts = [m.end() for m in re.finditer(re.escape(start), text)]
    ends = [m.start() for m in re.finditer(re.escape(end), text)]
    if not starts or not ends:
        return text.strip()
    start_idx = starts[-1]
    end_idx = next((e for e in ends if e > start_idx), ends[-1])
    payload = text[start_idx:end_idx]
    return payload.strip("\r\n").rstrip()
