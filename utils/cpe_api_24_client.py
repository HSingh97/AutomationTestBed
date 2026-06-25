"""
HTTP + SSH client for CPE 2.4 GHz management (169.254.254.1).

Full CPE reset: SSH `firstboot && reboot` on the mgmt IP (no BTS-link disconnect API).
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncssh
import httpx

from utils.cpe_api_24_config import (
    API_12_MGMT_AP_GRACE_S,
    API_12_PROGRESS_INTERVAL_S,
    API_12_REJOIN_INTERVAL_S,
    CpeApi24Config,
    FW_UPGRADE_VERIFY_MAX_S,
    FW_UPGRADE_WAIT_MAX_S,
    POST_UPGRADE_LINK_POLL_DELAY_S,
    POST_UPGRADE_POLL_DELAY_S,
    RADIO24_API_REBOOT_BOOT_MIN_S,
    RADIO24_API_REJOIN_INTERVAL_S,
    UPLOAD_SW_PATH,
    DEFAULT_LINK_THROUGHPUT_DURATION_S,
    DEFAULT_LINK_THROUGHPUT_READ_TIMEOUT_S,
    LINK_THROUGHPUT_TEST_PATH,
    UPGRADE_SW_PATH,
    infer_cpe_model_from_fw_path,
    UPGRADE_SW_STATUS_PATH,
    UPGRADE_SUCCESS_STATES,
)

_UPGRADE_FAIL_STATES = frozenset({"FAILURE", "FAILED", "ERROR"})

from utils.cpe_mgmt_wifi import wait_for_mgmt_api_reachable

DEBUG_LOG_PATH = "/home/senao/Desktop/Puneet/Automation TestBed/AutomationTestBed/.cursor/debug-a5d6ea.log"
DEBUG_SESSION_ID = "a5d6ea"


def _debug_log(*, run_id: str, hypothesis_id: str, location: str, message: str, data: dict[str, Any]) -> None:
    payload = {
        "sessionId": DEBUG_SESSION_ID,
        "runId": run_id,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    with open(DEBUG_LOG_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, separators=(",", ":")) + "\n")


@dataclass
class CpeApiResponse:
    status_code: int
    json_body: dict[str, Any] | None
    raw_body: str
    transport: str = "httpx"
    headers: dict[str, str] | None = None


class CpeApi24Client:
    def __init__(self, config: CpeApi24Config):
        self.config = config
        # Set in API_09 from FW filename; drives post-upgrade link wait in API_11.
        self._fw_upload_model: str | None = None
        # Cached before reboot for hidden nmcli rejoin (case 46 / API_12).
        self._cached_mgmt_bssid: str | None = None
        self._mgmt_profile_prepared: bool = False
        self._rejoin_link_reset_done: bool = False

    def set_fw_upload_model(self, model: str) -> None:
        self._fw_upload_model = (model or "").strip().upper() or None

    def fw_upload_model(self) -> str | None:
        return self._fw_upload_model

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = f"/{path}"
        return f"{self.config.base_url}{path}"

    def _timeout(self, read_s: float) -> httpx.Timeout:
        return httpx.Timeout(connect=10.0, read=read_s, write=30.0, pool=10.0)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        content: str | bytes | None = None,
        headers: dict[str, str] | None = None,
        read_timeout_s: float = 30.0,
        log: bool = True,
    ) -> CpeApiResponse:
        url = self._url(path)
        timeout = self._timeout(read_timeout_s)
        if log:
            print(f"    -> [API] {method.upper()} {url} (read timeout {read_timeout_s:.0f}s)")
        req_headers = dict(headers or {})
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(
                method,
                url,
                json=json_body,
                content=content,
                headers=req_headers or None,
            )
        parsed: dict[str, Any] | None = None
        try:
            body = response.json()
            if isinstance(body, dict):
                parsed = body
        except Exception:
            pass
        return CpeApiResponse(
            status_code=response.status_code,
            json_body=parsed,
            raw_body=response.text,
            headers=dict(response.headers),
        )

    async def request_raw(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        content: str | bytes | None = None,
        headers: dict[str, str] | None = None,
        read_timeout_s: float = 30.0,
        log: bool = True,
    ) -> CpeApiResponse:
        """HTTP request with optional raw body / custom headers (negative tests)."""
        return await self._request(
            method,
            path,
            json_body=json_body,
            content=content,
            headers=headers,
            read_timeout_s=read_timeout_s,
            log=log,
        )

    async def get_timed(
        self,
        path: str,
        *,
        read_timeout_s: float = 30.0,
    ) -> tuple[CpeApiResponse, float]:
        """GET path and return (response, elapsed_ms)."""
        start = time.monotonic()
        response = await self.get(path, read_timeout_s=read_timeout_s)
        elapsed_ms = (time.monotonic() - start) * 1000.0
        return response, elapsed_ms

    async def _reboot_and_wait_after_reboot(
        self,
        *,
        max_wait_s: float | None = None,
        label: str = "2_4G_RADIO_46",
        bts_host: str | None = None,
        bts_username: str = "root",
        bts_password: str = "",
        ready_via: str = "api",
    ) -> float:
        """Software reboot via SSH; rejoin mgmt Wi‑Fi; wait until CPE is ready (API or backend SSH)."""
        from utils.cpe_mgmt_wifi import is_mgmt_api_reachable

        wait_s = max_wait_s if max_wait_s is not None else self.config.reset_wait_s
        backend_first = ready_via == "backend"
        ready_label = "backend SSH (/etc/version)" if backend_first else "mgmt API"
        can_rejoin = bool(
            (self.config.cpe_mgmt_ssid and self.config.cpe_mgmt_password != "")
            or (bts_host and bts_password)
        )
        if can_rejoin:
            await self._prefetch_mgmt_bssid_before_reboot(
                label=label,
                bts_host=bts_host,
                bts_username=bts_username,
                bts_password=bts_password,
            )
            await self._prepare_mgmt_profile_before_reboot(label=label)
            ssid = (self.config.cpe_mgmt_ssid or "").strip()
            bssid_note = f", BSSID {self._cached_mgmt_bssid}" if self._cached_mgmt_bssid else ""
            print(f"    -> [{label}] pre-reboot: profile {ssid!r}{bssid_note}")
        print(f"    -> [{label}] sending CPE reboot via SSH {self.config.cpe_ssh_host}...")
        try:
            await self.ssh_run("reboot", timeout_s=15.0)
        except Exception as exc:
            if type(exc).__name__ not in (
                "ConnectionLost",
                "DisconnectError",
                "ConnectionResetError",
                "TimeoutError",
            ):
                raise
        start = time.monotonic()
        await self._wait_for_mgmt_api_down(attempts=24, delay_s=3.0)
        await asyncio.sleep(API_12_MGMT_AP_GRACE_S)

        boot_min_s = RADIO24_API_REBOOT_BOOT_MIN_S
        if backend_first:
            print(
                f"    -> [{label}] reboot sent — wait {boot_min_s / 60:.0f} min, "
                f"then nmcli join {self.config.cpe_mgmt_ssid!r}..."
            )
            await asyncio.sleep(boot_min_s)
        else:
            print(
                f"    -> [{label}] CPE rebooting — waiting {boot_min_s / 60:.0f} min "
                f"before Wi‑Fi rejoin..."
            )
            await asyncio.sleep(boot_min_s)

        deadline = start + wait_s
        rejoin_attempt = 0
        rejoin_interval_s = (
            RADIO24_API_REJOIN_INTERVAL_S if backend_first else API_12_REJOIN_INTERVAL_S
        )
        mgmt_creds: Any = None

        async def _is_ready() -> bool:
            from utils.cpe_mgmt_wifi import (
                resolve_cpe_mgmt_wifi_credentials,
                verify_cpe_mgmt_wifi_linked,
            )

            creds = resolve_cpe_mgmt_wifi_credentials(self.config)
            if not await verify_cpe_mgmt_wifi_linked(
                self.config,
                creds,
                settle_s=8.0,
                require_api=not backend_first,
            ):
                return False
            if backend_first:
                result = await self._ssh_run_on_mgmt_host(
                    "cat /etc/version 2>/dev/null | head -n 1",
                    timeout_s=8.0,
                )
                if not result:
                    return False
                exit_status, stdout, _ = result
                if exit_status not in (0, None):
                    return False
                return bool((stdout or "").strip())
            return await is_mgmt_api_reachable(self.config.base_url, timeout_s=3.0)

        if backend_first and can_rejoin:
            pass  # join loop below — no extra banner

        last_rejoin = 0.0
        rejoin_announced = False
        first_rejoin_after = start + boot_min_s

        while time.monotonic() < deadline:
            if backend_first:
                if can_rejoin:
                    rejoin_attempt += 1
                    joined_ip = await self._attempt_join_known_mgmt_wifi(
                        label=label,
                        quiet=True,
                        require_api=False,
                    )
                    if joined_ip:
                        print(
                            f"    -> [{label}] joined {self.config.cpe_mgmt_ssid!r} "
                            f"({joined_ip})"
                        )
                if await _is_ready():
                    elapsed = time.monotonic() - start
                    print(f"    -> [{label}] CPE ready ({elapsed:.0f}s).")
                    return elapsed
                if rejoin_attempt > 0 and rejoin_attempt % 6 == 0:
                    print(
                        f"    -> [{label}] still joining {self.config.cpe_mgmt_ssid!r} "
                        f"(attempt {rejoin_attempt})..."
                    )
                await asyncio.sleep(rejoin_interval_s)
                continue

            if await _is_ready():
                elapsed = time.monotonic() - start
                print(f"    -> [{label}] CPE {ready_label} up ({elapsed:.0f}s).")
                return elapsed
            if (
                can_rejoin
                and time.monotonic() >= first_rejoin_after
                and time.monotonic() - last_rejoin >= API_12_REJOIN_INTERVAL_S
            ):
                last_rejoin = time.monotonic()
                if not rejoin_announced:
                    print(
                        f"    -> [{label}] rejoining CPE mgmt Wi‑Fi "
                        f"(PC drops link when AP reboots)..."
                    )
                    rejoin_announced = True
                mgmt_creds = await self._try_rejoin_mgmt_soft(
                    bts_host=bts_host or "",
                    bts_username=bts_username,
                    bts_password=bts_password,
                    mgmt_creds=mgmt_creds,
                    label=label,
                )
                if await _is_ready():
                    elapsed = time.monotonic() - start
                    print(f"    -> [{label}] CPE {ready_label} up ({elapsed:.0f}s).")
                    return elapsed
                if mgmt_creds and not self._cached_mgmt_bssid and bts_host and bts_password:
                    from utils.cpe_api_24_lab import fetch_cpe_mgmt_ap_bssid

                    bssid = await fetch_cpe_mgmt_ap_bssid(
                        username=bts_username or self.config.cpe_ssh_user,
                        password=bts_password or str(self.config.cpe_ssh_password),
                        cpe_ipv6=self.config.cpe_ssh_fallback_host,
                        bts_host=bts_host,
                        known_mgmt_ssid=mgmt_creds.ssid,
                    )
                    if bssid:
                        self._cached_mgmt_bssid = bssid
            await asyncio.sleep(POST_UPGRADE_POLL_DELAY_S)

        raise RuntimeError(
            f"{label}: CPE {ready_label} did not return within {wait_s:.0f}s after reboot.\n"
            "  Rejoin CPE 2.4 GHz mgmt Wi‑Fi manually, then re-run."
        )

    async def reboot_and_wait_for_api(
        self,
        *,
        max_wait_s: float | None = None,
        label: str = "2_4G_RADIO_46",
        bts_host: str | None = None,
        bts_username: str = "root",
        bts_password: str = "",
    ) -> float:
        """Software reboot via SSH; rejoin mgmt Wi‑Fi; wait for HTTP mgmt API."""
        return await self._reboot_and_wait_after_reboot(
            max_wait_s=max_wait_s,
            label=label,
            bts_host=bts_host,
            bts_username=bts_username,
            bts_password=bts_password,
            ready_via="api",
        )

    async def reboot_and_wait_for_backend(
        self,
        *,
        max_wait_s: float | None = None,
        label: str = "2_4G_RADIO_46",
        bts_host: str | None = None,
        bts_username: str = "root",
        bts_password: str = "",
    ) -> float:
        """Software reboot via SSH; rejoin mgmt Wi‑Fi; wait for backend SSH (/etc/version) — no HTTP yet."""
        return await self._reboot_and_wait_after_reboot(
            max_wait_s=max_wait_s,
            label=label,
            bts_host=bts_host,
            bts_username=bts_username,
            bts_password=bts_password,
            ready_via="backend",
        )

    async def btsconnect(
        self,
        ssid: str,
        password: str,
        *,
        read_timeout_s: float | None = None,
    ) -> CpeApiResponse:
        read_s = read_timeout_s if read_timeout_s is not None else self.config.btsconnect_timeout_s
        url = self._url(self.config.btsconnect_path)
        print(f"    -> [API] POST {url} (read timeout {read_s:.0f}s)")
        try:
            timeout = self._timeout(read_s)
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    url, json={"ssid": ssid, "password": password}
                )
        except httpx.ReadTimeout as exc:
            raise RuntimeError(
                f"btsconnect read timeout after {read_s:.0f}s. "
                "Increase --cpe-24-btsconnect-timeout if CPE is still linking to BTS."
            ) from exc
        parsed: dict[str, Any] | None = None
        try:
            body = response.json()
            if isinstance(body, dict):
                parsed = body
        except Exception:
            pass
        return CpeApiResponse(
            status_code=response.status_code,
            json_body=parsed,
            raw_body=response.text,
            headers=dict(response.headers),
        )

    @staticmethod
    def _parse_curl_http_response(raw: str) -> tuple[int, dict[str, str], str]:
        """Parse `curl -i` combined headers + body."""
        text = raw.replace("\r\n", "\n")
        if "\n\n" not in text:
            return 0, {}, text
        header_block, body = text.split("\n\n", 1)
        lines = [ln for ln in header_block.split("\n") if ln.strip()]
        status_code = 0
        headers: dict[str, str] = {}
        for line in lines:
            if line.upper().startswith("HTTP/"):
                match = re.search(r"\s(\d{3})\s", line)
                if match:
                    status_code = int(match.group(1))
            elif ":" in line:
                key, value = line.split(":", 1)
                headers[key.strip().lower()] = value.strip()
        return status_code, headers, body

    async def upload_sw(self, fw_path: str, *, read_timeout_s: float | None = None) -> CpeApiResponse:
        """POST multipart firmware via curl (matches manual: -H Expect: -F file=@...)."""
        image = Path(fw_path).expanduser().resolve()
        if not image.is_file():
            raise RuntimeError(f"Firmware file not found: {image}")
        read_s = read_timeout_s if read_timeout_s is not None else self.config.fw_upload_timeout_s
        url = self._url(UPLOAD_SW_PATH)
        size_bytes = image.stat().st_size
        print(
            f"    -> [API] POST {url} file={image.name} "
            f"({size_bytes} bytes, max-time {read_s:.0f}s) via curl"
        )
        if not shutil.which("curl"):
            raise RuntimeError("API_09 upload requires curl on the test PC (same as manual flow).")

        cmd = [
            "curl",
            "-sS",
            "-i",
            "-X",
            "POST",
            "-H",
            "Expect:",
            "-F",
            f"file=@{image}",
            "--max-time",
            str(int(read_s)),
            url,
        ]
        # #region agent log
        _debug_log(
            run_id="run5",
            hypothesis_id="H10",
            location="utils/cpe_api_24_client.py:upload_sw",
            message="starting curl firmware upload",
            data={"url": url, "file": str(image), "size_bytes": size_bytes, "max_time_s": int(read_s)},
        )
        # #endregion
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await proc.communicate()
        raw = (stdout_b or b"").decode(errors="replace")
        err = (stderr_b or b"").decode(errors="replace").strip()
        status_code, headers, body = self._parse_curl_http_response(raw)
        parsed: dict[str, Any] | None = None
        try:
            parsed_body = json.loads(body)
            if isinstance(parsed_body, dict):
                parsed = parsed_body
        except Exception:
            pass
        # #region agent log
        _debug_log(
            run_id="run5",
            hypothesis_id="H11",
            location="utils/cpe_api_24_client.py:upload_sw",
            message="curl firmware upload finished",
            data={
                "exit_code": proc.returncode,
                "status_code": status_code,
                "stderr": err[:200],
                "body_preview": body[:200],
            },
        )
        # #endregion
        if proc.returncode != 0:
            raise RuntimeError(
                f"API_09 curl upload failed (exit {proc.returncode}): {err or raw[:300]}"
            )
        if status_code == 0:
            raise RuntimeError(f"API_09 curl upload: could not parse HTTP status from response: {raw[:400]}")
        return CpeApiResponse(
            status_code=status_code,
            json_body=parsed,
            raw_body=body,
            headers=headers,
            transport="curl",
        )

    async def upgrade_sw(
        self,
        *,
        delay_s: str = "5",
        preserve_config: bool = True,
        read_timeout_s: float = 60.0,
    ) -> CpeApiResponse:
        """POST /api/v1/cpe/upgrade-sw to start firmware upgrade."""
        payload = {"delay": str(delay_s), "preserveConfig": preserve_config}
        url = self._url(UPGRADE_SW_PATH)
        print(f"    -> [API] POST {url} json={payload} (read timeout {read_timeout_s:.0f}s)")
        return await self._request("POST", UPGRADE_SW_PATH, json_body=payload, read_timeout_s=read_timeout_s)

    async def link_throughput_test(
        self,
        *,
        duration_s: int = DEFAULT_LINK_THROUGHPUT_DURATION_S,
        read_timeout_s: float = DEFAULT_LINK_THROUGHPUT_READ_TIMEOUT_S,
    ) -> CpeApiResponse:
        """POST /api/v1/cpe/link-throughput-test (runs for duration seconds)."""
        payload: dict[str, Any] = {"duration": duration_s}
        url = self._url(LINK_THROUGHPUT_TEST_PATH)
        print(f"    -> [API] POST {url} json={payload} (read timeout {read_timeout_s:.0f}s)")
        return await self._request(
            "POST",
            LINK_THROUGHPUT_TEST_PATH,
            json_body=payload,
            read_timeout_s=read_timeout_s,
        )

    async def get(self, path: str, *, read_timeout_s: float = 30.0, log: bool = True) -> CpeApiResponse:
        if log:
            print(
                f"    -> [API] GET {self._url(path)} (read timeout {read_timeout_s:.0f}s)"
            )
        return await self._request(
            "GET", path, read_timeout_s=read_timeout_s, log=False
        )

    async def put(
        self,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        read_timeout_s: float = 30.0,
        log: bool = True,
    ) -> CpeApiResponse:
        if log:
            print(
                f"    -> [API] PUT {self._url(path)} (read timeout {read_timeout_s:.0f}s)"
            )
        return await self._request(
            "PUT",
            path,
            json_body=json_body,
            read_timeout_s=read_timeout_s,
            log=False,
        )

    async def get_event_stream_sample(
        self,
        path: str,
        *,
        read_timeout_s: float = 20.0,
        bytes_limit: int = 4096,
    ) -> CpeApiResponse:
        """Read a short sample from an SSE endpoint without waiting for socket close."""
        url = self._url(path)
        print(f"    -> [API] GET(stream) {url} (read timeout {read_timeout_s:.0f}s)")
        timeout = self._timeout(read_timeout_s)
        chunks: list[str] = []
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("GET", url) as response:
                async for chunk in response.aiter_text():
                    if chunk:
                        chunks.append(chunk)
                    if sum(len(c) for c in chunks) >= bytes_limit:
                        break
                raw = "".join(chunks)
                return CpeApiResponse(
                    status_code=response.status_code,
                    json_body=None,
                    raw_body=raw,
                    headers=dict(response.headers),
                )

    async def _ssh_run_on_mgmt_host(
        self,
        command: str,
        *,
        timeout_s: float = 10.0,
        verify_cpe: bool = True,
    ) -> tuple[int | None, str, str] | None:
        """SSH to 169.254.254.1 on PC CPE mgmt Wi‑Fi only (fast poll; no IPv6 fallback)."""
        from utils.cpe_api_24_lab import _assert_ssh_session_is_cpe

        if not self.config.cpe_ssh_password:
            return None
        try:
            conn = await asyncssh.connect(
                self.config.cpe_ssh_host,
                username=self.config.cpe_ssh_user,
                password=str(self.config.cpe_ssh_password),
                known_hosts=None,
                connect_timeout=4.0,
            )
            if verify_cpe:
                await _assert_ssh_session_is_cpe(conn, context=f"SSH CPE @ {self.config.cpe_ssh_host}")
            async with conn:
                result = await conn.run(command, check=False, timeout=timeout_s)
                return result.exit_status, (result.stdout or ""), (result.stderr or "")
        except Exception:
            return None

    async def ssh_run(self, command: str, *, timeout_s: float = 20.0) -> tuple[int | None, str, str]:
        """Run a non-reset SSH command on the CPE (not BTS lan24 at the same mgmt IP)."""
        from utils.cpe_api_24_lab import _assert_ssh_session_is_cpe, _ssh_session_device_role

        if not self.config.cpe_ssh_password:
            raise RuntimeError("CPE SSH password missing (--password or profile dut.password).")

        hosts = [self.config.cpe_ssh_host]
        fallback = (self.config.cpe_ssh_fallback_host or "").strip()
        if fallback and fallback not in hosts:
            hosts.append(fallback)

        last_error: Exception | None = None
        for host in hosts:
            try:
                conn = await self._connect_ssh_with_retries(host=host)
                await _assert_ssh_session_is_cpe(conn, context=f"SSH CPE @ {host}")
                async with conn:
                    result = await conn.run(command, check=False, timeout=timeout_s)
                    return result.exit_status, (result.stdout or ""), (result.stderr or "")
            except Exception as exc:
                last_error = exc
                role_note = ""
                if host == self.config.cpe_ssh_host:
                    try:
                        probe = await self._connect_ssh_with_retries(host=host)
                        role = await _ssh_session_device_role(probe)
                        probe.close()
                        if role == "bts":
                            role_note = " (PC on BTS UBRMGMT* Wi‑Fi — join CPE KWDEJPOQ)"
                    except Exception:
                        pass
                if host == hosts[-1]:
                    raise RuntimeError(
                        f"CPE SSH failed on {hosts!r}{role_note}: {last_error}"
                    ) from last_error
        raise RuntimeError(f"CPE SSH failed: {last_error}")

    async def _factory_reset_via_mgmt_api(self) -> bool:
        """Optional REST override (--cpe-24-factory-reset-path). Device has no default endpoint."""
        path = self.config.factory_reset_path
        if not path:
            return False
        for method in ("POST", "PUT"):
            response = await self._request(method, path, json_body={}, read_timeout_s=60.0)
            if response.status_code in (200, 202, 204):
                print(
                    f"    -> [CPE] Factory reset OK: {method} {path} "
                    f"HTTP {response.status_code}"
                )
                return True
            print(
                f"    -> [CPE] {method} {path} -> HTTP {response.status_code}: "
                f"{response.raw_body[:160]}"
            )
        return False

    async def factory_reset_cpe(self) -> None:
        """Full CPE factory reset (clears BTS link state), not BTS disconnect only."""
        if await self._factory_reset_via_mgmt_api():
            # #region agent log
            _debug_log(
                run_id="run1",
                hypothesis_id="H2",
                location="utils/cpe_api_24_client.py:factory_reset_cpe",
                message="factory reset path used mgmt API override",
                data={"factory_reset_path": self.config.factory_reset_path},
            )
            # #endregion
            return

        host = self.config.cpe_ssh_host
        cmd = self.config.factory_reset_command
        if not cmd:
            raise RuntimeError(
                "CPE factory reset command is empty. Set recovery.factory_reset_command "
                "in profile or pass --cpe-24-factory-reset-command."
            )
        if not self.config.cpe_ssh_password:
            raise RuntimeError(
                "CPE factory reset needs root password (--password or profile dut.password)."
            )

        print(
            f"    -> [CPE] Full factory reset via SSH {host} "
            f"({self.config.cpe_ssh_user}): {cmd}"
        )
        # #region agent log
        _debug_log(
            run_id="run1",
            hypothesis_id="H14",
            location="utils/cpe_api_24_client.py:factory_reset_cpe",
            message="starting reset command over ssh foreground",
            data={"host": host, "command": cmd},
        )
        # #endregion
        try:
            conn = await self._connect_ssh_with_retries(host=host)
            bts_tunnel_conn = getattr(conn, "_agent_bts_tunnel_conn", None)
            async with conn:
                # #region agent log
                _debug_log(
                    run_id="run1",
                    hypothesis_id="H15",
                    location="utils/cpe_api_24_client.py:factory_reset_cpe",
                    message="checking reset command prerequisites",
                    data={"cmd_check": "command -v firstboot && command -v reboot && command -v nohup"},
                )
                # #endregion
                pre = await conn.run("command -v firstboot; command -v reboot; command -v nohup", check=False, timeout=8)
                # #region agent log
                _debug_log(
                    run_id="run1",
                    hypothesis_id="H15",
                    location="utils/cpe_api_24_client.py:factory_reset_cpe",
                    message="reset command prerequisites result",
                    data={"exit_status": pre.exit_status, "stdout": (pre.stdout or "")[:200], "stderr": (pre.stderr or "")[:200]},
                )
                # #endregion
                try:
                    result = await conn.run(cmd, check=False, timeout=45)
                    out = (result.stdout or "").strip()
                    err = (result.stderr or "").strip()
                    # #region agent log
                    _debug_log(
                        run_id="run1",
                        hypothesis_id="H14",
                        location="utils/cpe_api_24_client.py:factory_reset_cpe",
                        message="reset command returned before disconnect",
                        data={"exit_status": result.exit_status, "stdout": out[:200], "stderr": err[:200]},
                    )
                    # #endregion
                    # On some firmware the SSH command reports exit_status=None
                    # because reboot tears down the channel before shell exit.
                    if result.exit_status not in (0, None):
                        raise RuntimeError(
                            f"CPE factory reset SSH failed (exit {result.exit_status}): {(err or out)[:200]}"
                        )
                except Exception as exc:
                    # Expected path on reboot: transport/socket errors while rebooting.
                    # #region agent log
                    _debug_log(
                        run_id="run1",
                        hypothesis_id="H14",
                        location="utils/cpe_api_24_client.py:factory_reset_cpe",
                        message="reset command raised during reboot",
                        data={"error_type": type(exc).__name__, "error": str(exc)[:200]},
                    )
                    # #endregion
                    if type(exc).__name__ not in ("ConnectionLost", "ConnectionResetError", "DisconnectError", "TimeoutError"):
                        raise
                # #region agent log
                _debug_log(
                    run_id="run1",
                    hypothesis_id="H14",
                    location="utils/cpe_api_24_client.py:factory_reset_cpe",
                    message="reset command accepted; proceeding to reboot wait",
                    data={"host": host},
                )
                # #endregion
                print("    -> [CPE] Factory reset command sent; waiting for reboot")
            if bts_tunnel_conn is not None:
                bts_tunnel_conn.close()
        except asyncssh.Error as exc:
            raise RuntimeError(f"CPE factory reset SSH to {host} failed: {exc}") from exc
        except OSError as exc:
            raise RuntimeError(f"CPE factory reset SSH socket failed to {host}: {exc}") from exc

    async def _connect_ssh_with_retries(self, *, host: str) -> asyncssh.SSHClientConnection:
        if self.config.cpe_ssh_via_bts_tunnel and self.config.cpe_ssh_tunnel_host:
            return await self._connect_ssh_via_bts_tunnel_with_retries(
                host=host,
                tunnel_host=self.config.cpe_ssh_tunnel_host,
            )
        last_exc: Exception | None = None
        for attempt in range(1, 5):
            # #region agent log
            _debug_log(
                run_id="run1",
                hypothesis_id="H9",
                location="utils/cpe_api_24_client.py:_connect_ssh_with_retries",
                message="attempting ssh connect for reset",
                data={"host": host, "attempt": attempt},
            )
            # #endregion
            try:
                conn = await asyncssh.connect(
                    host,
                    username=self.config.cpe_ssh_user,
                    password=self.config.cpe_ssh_password,
                    known_hosts=None,
                    connect_timeout=8,
                )
                # #region agent log
                _debug_log(
                    run_id="run1",
                    hypothesis_id="H9",
                    location="utils/cpe_api_24_client.py:_connect_ssh_with_retries",
                    message="ssh connect succeeded for reset",
                    data={"host": host, "attempt": attempt},
                )
                # #endregion
                return conn
            except (asyncssh.Error, OSError) as exc:
                last_exc = exc
                # #region agent log
                _debug_log(
                    run_id="run1",
                    hypothesis_id="H9",
                    location="utils/cpe_api_24_client.py:_connect_ssh_with_retries",
                    message="ssh connect failed for reset",
                    data={"host": host, "attempt": attempt, "error_type": type(exc).__name__, "error": str(exc)[:200]},
                )
                # #endregion
                await asyncio.sleep(2)
        if isinstance(last_exc, Exception):
            raise last_exc
        raise RuntimeError(f"Unable to establish SSH connection to {host}")

    async def _connect_ssh_via_bts_tunnel_with_retries(
        self, *, host: str, tunnel_host: str
    ) -> asyncssh.SSHClientConnection:
        last_exc: Exception | None = None
        for attempt in range(1, 5):
            # #region agent log
            _debug_log(
                run_id="run1",
                hypothesis_id="H10",
                location="utils/cpe_api_24_client.py:_connect_ssh_via_bts_tunnel_with_retries",
                message="attempting cpe ssh via bts tunnel for reset",
                data={"host": host, "tunnel_host": tunnel_host, "attempt": attempt},
            )
            # #endregion
            bts_conn: asyncssh.SSHClientConnection | None = None
            try:
                bts_conn = await asyncssh.connect(
                    tunnel_host,
                    username=self.config.cpe_ssh_user,
                    password=self.config.cpe_ssh_password,
                    known_hosts=None,
                    connect_timeout=8,
                )
                cpe_conn = await asyncssh.connect(
                    host,
                    username=self.config.cpe_ssh_user,
                    password=self.config.cpe_ssh_password,
                    known_hosts=None,
                    connect_timeout=8,
                    tunnel=bts_conn,
                )
                setattr(cpe_conn, "_agent_bts_tunnel_conn", bts_conn)
                # #region agent log
                _debug_log(
                    run_id="run1",
                    hypothesis_id="H10",
                    location="utils/cpe_api_24_client.py:_connect_ssh_via_bts_tunnel_with_retries",
                    message="cpe ssh via bts tunnel connected",
                    data={"host": host, "tunnel_host": tunnel_host, "attempt": attempt},
                )
                # #endregion
                return cpe_conn
            except (asyncssh.Error, OSError) as exc:
                last_exc = exc
                if bts_conn is not None:
                    bts_conn.close()
                # #region agent log
                _debug_log(
                    run_id="run1",
                    hypothesis_id="H10",
                    location="utils/cpe_api_24_client.py:_connect_ssh_via_bts_tunnel_with_retries",
                    message="cpe ssh via bts tunnel failed",
                    data={
                        "host": host,
                        "tunnel_host": tunnel_host,
                        "attempt": attempt,
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:200],
                    },
                )
                # #endregion
                await asyncio.sleep(2)
        if isinstance(last_exc, Exception):
            raise last_exc
        raise RuntimeError(f"Unable to establish SSH tunnel {tunnel_host} -> {host}")

    @staticmethod
    def _shell_quote(command: str) -> str:
        return "'" + command.replace("'", "'\"'\"'") + "'"

    async def _wait_for_mgmt_api_down(self, *, attempts: int = 24, delay_s: float = 5.0) -> bool:
        from utils.cpe_mgmt_wifi import is_mgmt_api_reachable

        for idx in range(attempts):
            reachable = await is_mgmt_api_reachable(self.config.base_url, timeout_s=2.0)
            if idx in (0, attempts - 1) or idx % 6 == 0:
                # #region agent log
                _debug_log(
                    run_id="run1",
                    hypothesis_id="H3",
                    location="utils/cpe_api_24_client.py:_wait_for_mgmt_api_down",
                    message="mgmt api down probe",
                    data={"probe_index": idx, "attempts": attempts, "reachable": reachable},
                )
                # #endregion
            if not reachable:
                print("    -> CPE mgmt API went down (reboot in progress)")
                return True
            await asyncio.sleep(delay_s)
        print("    -> Note: CPE mgmt API did not go down before wait (may already be rebooting)")
        return False

    async def factory_reset_and_wait(self, *, label: str) -> None:
        wait_s = self.config.reset_wait_s
        # #region agent log
        _debug_log(
            run_id="run1",
            hypothesis_id="H4",
            location="utils/cpe_api_24_client.py:factory_reset_and_wait",
            message="entered factory reset and wait",
            data={"label": label, "wait_seconds": wait_s, "base_url": self.config.base_url},
        )
        # #endregion
        print(
            f"\n[Preflight] {label}: full CPE factory reset (firstboot), "
            f"then wait {wait_s:.0f}s..."
        )
        await self.factory_reset_cpe()

        await self._wait_for_mgmt_api_down()

        min_wait = self.config.reset_wait_s
        print(f"    -> waiting at least {min_wait:.0f}s for firstboot + reboot...")
        for remaining in range(int(min_wait), 0, -30):
            print(f"    -> waiting {remaining}s...")
            await asyncio.sleep(min(30, remaining))

        print("    -> waiting for CPE mgmt API to return...")
        if not await wait_for_mgmt_api_reachable(
            self.config.base_url,
            attempts=60,
            delay_s=5.0,
        ):
            # #region agent log
            _debug_log(
                run_id="run1",
                hypothesis_id="H5",
                location="utils/cpe_api_24_client.py:factory_reset_and_wait",
                message="mgmt api did not return after reset",
                data={"base_url": self.config.base_url, "attempts": 60, "delay_s": 5.0},
            )
            # #endregion
            raise RuntimeError(
                f"CPE mgmt API {self.config.base_url} did not return after factory reset.\n"
                "  1. Rejoin CPE 2.4 GHz mgmt Wi‑Fi if the SSID changed.\n"
                "  2. Confirm curl .../api/v1/cpe/sw-version returns 200.\n"
                "  3. Re-run pytest."
            )
        # #region agent log
        _debug_log(
            run_id="run1",
            hypothesis_id="H4",
            location="utils/cpe_api_24_client.py:factory_reset_and_wait",
            message="factory reset wait completed with mgmt api reachable",
            data={"label": label, "base_url": self.config.base_url},
        )
        # #endregion
        print(f"[Preflight] {label}: factory reset complete; mgmt API is up.\n")

    async def reset_and_wait(self, *, label: str) -> None:
        """Alias used by fixtures/flows — always full factory reset."""
        await self.factory_reset_and_wait(label=label)

    async def resolve_cpe_model(self) -> tuple[str, str]:
        """Return (model, source). Prefer model parsed at API_09 FW upload."""
        if self._fw_upload_model:
            return self._fw_upload_model, "fw_upload"
        inferred = infer_cpe_model_from_fw_path(self.config.fw_file_path)
        if inferred:
            return inferred, "fw_file"
        if self.config.cpe_model:
            return self.config.cpe_model, "cli"
        exit_status, stdout, _ = await self.ssh_run(
            "cat /etc/ademodel 2>/dev/null || echo UNKNOWN",
            timeout_s=10.0,
        )
        model = (stdout or "").strip() if exit_status == 0 else "UNKNOWN"
        return model or "UNKNOWN", "ssh"

    async def wait_post_upgrade_and_link(
        self,
        *,
        bts_host: str | None = None,
        bts_username: str = "root",
        bts_password: str = "",
    ) -> None:
        """Wait for reboot, CPE up, then BTS link — polls until each step completes."""
        from utils.cpe_api_24_lab import is_bts_link_broken
        from utils.cpe_mgmt_wifi import is_mgmt_api_reachable

        upgrade_start = time.monotonic()
        deadline = upgrade_start + FW_UPGRADE_WAIT_MAX_S
        model, model_source = await self.resolve_cpe_model()
        can_check_bts = bool(bts_host and bts_password)
        # #region agent log
        _debug_log(
            run_id="post-fix",
            hypothesis_id="H1",
            location="utils/cpe_api_24_client.py:wait_post_upgrade_and_link",
            message="post-upgrade wait plan",
            data={
                "model": model,
                "model_source": model_source,
                "wait_max_s": FW_UPGRADE_WAIT_MAX_S,
                "bts_host_set": can_check_bts,
            },
        )
        # #endregion

        print(
            f"\n[API_11] Waiting for reboot, device up, then BTS link "
            f"(polls until ready, max {FW_UPGRADE_WAIT_MAX_S / 60:.0f} min)..."
        )
        if not can_check_bts:
            raise RuntimeError("API_11 link wait requires --local-ipv6 for BTS partner check.")

        reboot_observed = False
        reboot_at_s: float | None = None
        device_up_at: float | None = None
        api_up_at_s: float | None = None
        link_formed_at_s: float | None = None

        while time.monotonic() < deadline and not reboot_observed:
            if await is_bts_link_broken(
                bts_host=bts_host,
                username=bts_username,
                password=bts_password,
                radio_idx=1,
                log=False,
            ):
                reboot_observed = True
                reboot_at_s = time.monotonic() - upgrade_start
                break
            await asyncio.sleep(POST_UPGRADE_POLL_DELAY_S)

        if not reboot_observed:
            raise RuntimeError(
                f"Reboot/link drop not seen within {FW_UPGRADE_WAIT_MAX_S / 60:.0f} min ({model})."
            )

        # Mgmt API must go down after link drop (avoids false "device up" at ~1s).
        while time.monotonic() < deadline:
            if not await is_mgmt_api_reachable(self.config.base_url, timeout_s=2.0):
                break
            await asyncio.sleep(POST_UPGRADE_POLL_DELAY_S)

        while time.monotonic() < deadline and device_up_at is None:
            if await is_mgmt_api_reachable(self.config.base_url, timeout_s=3.0):
                device_up_at = time.monotonic()
                api_up_at_s = time.monotonic() - upgrade_start
                break
            await asyncio.sleep(POST_UPGRADE_POLL_DELAY_S)

        if device_up_at is None:
            raise RuntimeError(
                f"CPE mgmt API did not return within {FW_UPGRADE_WAIT_MAX_S / 60:.0f} min ({model})."
            )

        last_progress_print = 0.0

        while time.monotonic() < deadline:
            elapsed = time.monotonic() - upgrade_start
            link_elapsed = time.monotonic() - device_up_at

            if not await is_bts_link_broken(
                bts_host=bts_host,
                username=bts_username,
                password=bts_password,
                radio_idx=1,
                log=False,
            ):
                link_formed_at_s = elapsed
                # #region agent log
                _debug_log(
                    run_id="post-fix",
                    hypothesis_id="H3",
                    location="utils/cpe_api_24_client.py:wait_post_upgrade_and_link",
                    message="BTS link up",
                    data={
                        "model": model,
                        "link_formed_at_s": link_formed_at_s,
                        "link_elapsed_after_device_up_s": link_elapsed,
                        "reboot_at_s": reboot_at_s,
                        "api_up_at_s": api_up_at_s,
                    },
                )
                # #endregion
                break

            if link_elapsed - last_progress_print >= 180.0:
                print(f"    -> still waiting for BTS link ({link_elapsed / 60:.0f} min since device up)...")
                last_progress_print = link_elapsed

            await asyncio.sleep(POST_UPGRADE_LINK_POLL_DELAY_S)

        total_elapsed = time.monotonic() - upgrade_start

        if link_formed_at_s is None:
            broken = await is_bts_link_broken(
                bts_host=bts_host,
                username=bts_username,
                password=bts_password,
                radio_idx=1,
                log=False,
            )
            # #region agent log
            _debug_log(
                run_id="post-fix",
                hypothesis_id="H2",
                location="utils/cpe_api_24_client.py:wait_post_upgrade_and_link",
                message="BTS link safety timeout",
                data={
                    "model": model,
                    "total_elapsed_s": total_elapsed,
                    "wait_max_s": FW_UPGRADE_WAIT_MAX_S,
                    "bts_link_broken": broken,
                },
            )
            # #endregion
            if broken:
                raise RuntimeError(
                    f"BTS link did not return within {FW_UPGRADE_WAIT_MAX_S / 60:.0f} min ({model}). "
                    f"Elapsed {total_elapsed:.0f}s from upgrade start."
                )

        print(
            f"[API_11] BTS link up — {link_formed_at_s:.0f}s ({link_formed_at_s / 60:.1f} min) from upgrade start."
        )

    @staticmethod
    def _sw_version_from_body(body: dict[str, Any]) -> str | None:
        for key in ("swversion", "sw_version", "version", "swVersion"):
            value = body.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text and text != "-":
                return text
        return None

    @staticmethod
    def _upgrade_state_from_body(body: dict[str, Any]) -> str:
        raw = str(body.get("state", body.get("status", ""))).upper().replace(" ", "_")
        if raw == "IN PROGRESS":
            return "IN_PROGRESS"
        return raw

    async def wait_upgrade_sw_status_success(
        self,
        *,
        max_wait_s: float = FW_UPGRADE_VERIFY_MAX_S,
        label: str = "API_14",
    ) -> dict[str, Any]:
        """Poll GET upgrade-sw-status until firmware upgrade reports success."""
        from utils.cpe_mgmt_wifi import is_mgmt_api_reachable

        start = time.monotonic()
        deadline = start + max_wait_s
        last_progress = 0.0
        last_state = ""
        last_body: dict[str, Any] = {}

        print(f"\n[{label}] Waiting for upgrade-sw-status SUCCESS (fw upgrade complete)...")

        while time.monotonic() < deadline:
            elapsed = time.monotonic() - start
            if await is_mgmt_api_reachable(self.config.base_url, timeout_s=3.0):
                response = await self.get(
                    UPGRADE_SW_STATUS_PATH,
                    read_timeout_s=30.0,
                    log=False,
                )
                if response.status_code == 200:
                    last_body = response.json_body if isinstance(response.json_body, dict) else {}
                    last_state = self._upgrade_state_from_body(last_body)
                    if last_state in UPGRADE_SUCCESS_STATES:
                        print(
                            f"[{label}] Upgrade status SUCCESS — {elapsed:.0f}s "
                            f"({elapsed / 60:.1f} min) from wait start."
                        )
                        return {
                            "state": last_state,
                            "body": last_body,
                            "elapsed_s": elapsed,
                            "response": response,
                        }
                    if last_state in _UPGRADE_FAIL_STATES:
                        raise RuntimeError(
                            f"{label}: upgrade-sw-status failure: {last_body!r}"
                        )

            if elapsed - last_progress >= 180.0:
                print(
                    f"    -> still waiting for upgrade SUCCESS "
                    f"(last state={last_state or 'unreachable'}, {elapsed / 60:.0f} min)..."
                )
                last_progress = elapsed

            await asyncio.sleep(POST_UPGRADE_LINK_POLL_DELAY_S)

        total = time.monotonic() - start
        raise RuntimeError(
            f"{label}: upgrade did not reach SUCCESS within {max_wait_s / 60:.0f} min "
            f"({total:.0f}s elapsed). Last state={last_state!r} body={last_body!r}"
        )

    async def wait_bts_link_up_after_device(
        self,
        *,
        bts_host: str | None = None,
        bts_username: str = "root",
        bts_password: str = "",
        label: str = "API_14",
        upgrade_start: float | None = None,
    ) -> None:
        """After upgrade completes: wait for mgmt API up, then BTS↔CPE link."""
        from utils.cpe_api_24_lab import is_bts_link_broken
        from utils.cpe_mgmt_wifi import is_mgmt_api_reachable

        _ = upgrade_start  # kept for call-site compatibility; each phase gets a full wait budget
        if not bts_host or not bts_password:
            raise RuntimeError(f"{label} link wait requires --local-ipv6 for BTS partner check.")

        wait_start = time.monotonic()
        deadline = wait_start + FW_UPGRADE_WAIT_MAX_S

        print(
            f"\n[{label}] Waiting for CPE mgmt API up, then BTS link "
            f"(polls until ready, max {FW_UPGRADE_WAIT_MAX_S / 60:.0f} min)..."
        )

        device_up_at: float | None = None
        while time.monotonic() < deadline and device_up_at is None:
            if await is_mgmt_api_reachable(self.config.base_url, timeout_s=3.0):
                device_up_at = time.monotonic()
                break
            await asyncio.sleep(POST_UPGRADE_POLL_DELAY_S)

        if device_up_at is None:
            raise RuntimeError(
                f"{label}: CPE mgmt API did not return within {FW_UPGRADE_WAIT_MAX_S / 60:.0f} min."
            )

        link_formed_at_s: float | None = None
        last_progress_print = 0.0

        while time.monotonic() < deadline:
            elapsed = time.monotonic() - wait_start
            link_elapsed = time.monotonic() - device_up_at

            if not await is_bts_link_broken(
                bts_host=bts_host,
                username=bts_username,
                password=bts_password,
                radio_idx=1,
                log=False,
            ):
                link_formed_at_s = elapsed
                break

            if link_elapsed - last_progress_print >= 180.0:
                print(
                    f"    -> still waiting for BTS link ({link_elapsed / 60:.0f} min since device up)..."
                )
                last_progress_print = link_elapsed

            await asyncio.sleep(POST_UPGRADE_LINK_POLL_DELAY_S)

        total_elapsed = time.monotonic() - wait_start
        if link_formed_at_s is None:
            broken = await is_bts_link_broken(
                bts_host=bts_host,
                username=bts_username,
                password=bts_password,
                radio_idx=1,
                log=False,
            )
            if broken:
                raise RuntimeError(
                    f"{label}: BTS link did not return within {FW_UPGRADE_WAIT_MAX_S / 60:.0f} min. "
                    f"Elapsed {total_elapsed:.0f}s from wait start."
                )
            link_formed_at_s = total_elapsed

        print(
            f"[{label}] BTS link up — {link_formed_at_s:.0f}s ({link_formed_at_s / 60:.1f} min) "
            f"from wait start."
        )

    @staticmethod
    def _fw_version_matches(actual: str | None, expected: str) -> bool:
        if not actual or not expected:
            return False
        actual_n = actual.strip().lower()
        expected_n = expected.strip().lower()
        return actual_n == expected_n or expected_n in actual_n or actual_n.startswith(expected_n)

    async def _read_sw_version_via_ssh(self) -> str | None:
        try:
            exit_status, stdout, _ = await self.ssh_run(
                "cat /etc/version 2>/dev/null | head -n 1",
                timeout_s=10.0,
            )
        except (OSError, asyncssh.Error, ConnectionError, TimeoutError):
            return None
        if exit_status not in (0, None):
            return None
        lines = (stdout or "").strip().splitlines()
        return lines[0].strip() if lines else None

    async def _read_sw_version(
        self,
        *,
        log_api: bool = False,
        allow_ssh: bool = True,
    ) -> tuple[str | None, str]:
        from utils.cpe_mgmt_wifi import is_mgmt_api_reachable

        if await is_mgmt_api_reachable(self.config.base_url, timeout_s=3.0):
            response = await self.get(
                "/api/v1/cpe/sw-version",
                read_timeout_s=20.0,
                log=log_api,
            )
            if response.status_code == 200:
                body = response.json_body if isinstance(response.json_body, dict) else {}
                return self._sw_version_from_body(body), "api"
        if allow_ssh:
            ssh_ver = await self._read_sw_version_via_ssh()
            if ssh_ver:
                return ssh_ver, "ssh"
        return None, ""

    async def _prefetch_mgmt_bssid_before_reboot(
        self,
        *,
        label: str,
        bts_host: str | None,
        bts_username: str,
        bts_password: str,
    ) -> str | None:
        """Read CPE ath0 BSSID while still on mgmt Wi‑Fi (before reboot drops the link)."""
        if self._cached_mgmt_bssid:
            return self._cached_mgmt_bssid

        from utils.cpe_api_24_lab import _CPE_AP_BSSID_RE, fetch_cpe_mgmt_ap_bssid

        result = await self._ssh_run_on_mgmt_host(
            "ifconfig ath0 2>/dev/null | awk '/HWaddr/{print $5; exit}'",
            timeout_s=10.0,
        )
        if result:
            _, stdout, _ = result
            text = (stdout or "").strip()
            if text:
                match = _CPE_AP_BSSID_RE.search(text)
                bssid = match.group(1).upper() if match else text.split()[0].upper()
                if bssid:
                    self._cached_mgmt_bssid = bssid
                    return bssid

        bssid = await fetch_cpe_mgmt_ap_bssid(
            username=bts_username or self.config.cpe_ssh_user,
            password=bts_password or str(self.config.cpe_ssh_password),
            cpe_ipv6=self.config.cpe_ssh_fallback_host,
            bts_host=bts_host,
            known_mgmt_ssid=self.config.cpe_mgmt_ssid,
        )
        if bssid:
            self._cached_mgmt_bssid = bssid
        return bssid

    async def _prepare_mgmt_profile_before_reboot(self, *, label: str) -> None:
        """Pre-create nmcli profile with cached BSSID so post-reboot rejoin can use connection up."""
        from utils.cpe_mgmt_wifi import (
            ensure_cpe_hidden_mgmt_profile,
            resolve_cpe_mgmt_wifi_credentials,
            resolve_wifi_interface,
        )

        creds = resolve_cpe_mgmt_wifi_credentials(self.config)
        iface = await resolve_wifi_interface(self.config.wifi_interface or "")
        if await ensure_cpe_hidden_mgmt_profile(
            iface,
            creds,
            connection_name=self.config.wifi_connection_name,
            bssid=self._cached_mgmt_bssid,
        ):
            self._mgmt_profile_prepared = True

    async def _attempt_join_known_mgmt_wifi(
        self,
        *,
        label: str,
        quiet: bool = True,
        require_api: bool = False,
    ) -> str | None:
        """nmcli join using profile SSID/password + cached BSSID (no BTS fetch)."""
        from utils.cpe_mgmt_wifi import (
            CpeMgmtWifiCredentials,
            connect_pc_to_cpe_hidden_wifi,
            resolve_cpe_mgmt_wifi_credentials,
            _wait_for_iface_ipv4,
        )

        creds = resolve_cpe_mgmt_wifi_credentials(self.config)
        if not creds.ssid or creds.password == "":
            return None
        wifi = CpeMgmtWifiCredentials(
            ssid=creds.ssid,
            password=str(creds.password),
            hidden=creds.hidden,
        )
        try:
            iface = await connect_pc_to_cpe_hidden_wifi(
                self.config.wifi_interface or "",
                wifi,
                connection_name=self.config.wifi_connection_name,
                settle_s=min(self.config.wifi_settle_s, 8.0),
                bts_ssid=self.config.bts_ssid,
                quiet=quiet,
                config=self.config,
                cached_bssid=self._cached_mgmt_bssid,
                require_api=require_api,
            )
            return await _wait_for_iface_ipv4(iface, timeout_s=8, prefix="169.254.")
        except Exception:
            return None

    async def _resolve_rejoin_mgmt_creds(
        self,
        *,
        bts_host: str,
        bts_username: str,
        bts_password: str,
        mgmt_creds: Any,
    ) -> Any:
        from utils.cpe_api_24_lab import FetchedWifiCredentials, fetch_cpe_mgmt_ap_credentials
        from utils.cpe_mgmt_wifi import _mgmt_fetch_kwargs

        if mgmt_creds is not None:
            return mgmt_creds
        if self.config.cpe_mgmt_ssid and self.config.cpe_mgmt_password != "":
            return FetchedWifiCredentials(
                ssid=self.config.cpe_mgmt_ssid,
                password=str(self.config.cpe_mgmt_password),
                hidden=self.config.cpe_mgmt_hidden,
            )
        if bts_host and bts_password:
            return await fetch_cpe_mgmt_ap_credentials(
                **_mgmt_fetch_kwargs(
                    self.config,
                    bts_host=bts_host,
                    bts_username=bts_username,
                    bts_password=bts_password,
                )
            )
        return None

    async def _try_rejoin_mgmt_soft(
        self,
        *,
        bts_host: str,
        bts_username: str,
        bts_password: str,
        mgmt_creds: Any,
        label: str,
        require_api: bool = True,
        max_attempts: int = 3,
    ) -> Any:
        """Best-effort hidden rejoin after reboot — never raises; retries connection up only."""
        from utils.cpe_mgmt_wifi import (
            CpeMgmtWifiCredentials,
            _wait_for_iface_ipv4,
            resolve_wifi_interface,
            try_rejoin_cpe_mgmt_wifi,
        )

        mgmt_creds = await self._resolve_rejoin_mgmt_creds(
            bts_host=bts_host,
            bts_username=bts_username,
            bts_password=bts_password,
            mgmt_creds=mgmt_creds,
        )
        if mgmt_creds is None:
            return None

        wifi_creds = CpeMgmtWifiCredentials(
            ssid=mgmt_creds.ssid,
            password=mgmt_creds.password,
            hidden=mgmt_creds.hidden,
        )
        joined = await try_rejoin_cpe_mgmt_wifi(
            self.config,
            wifi_creds,
            bssid=self._cached_mgmt_bssid,
            ensure_profile=not self._mgmt_profile_prepared,
            reset_link=not self._rejoin_link_reset_done,
            require_api=require_api,
            max_attempts=max_attempts,
        )
        if not self._rejoin_link_reset_done:
            self._rejoin_link_reset_done = True
        if joined:
            iface_ip = await _wait_for_iface_ipv4(
                (await resolve_wifi_interface(self.config.wifi_interface or "")),
                timeout_s=5,
            )
            ip_note = f", {iface_ip}" if iface_ip else ""
            print(f"    -> [{label}] CPE mgmt Wi‑Fi linked {wifi_creds.ssid!r}{ip_note}")
        elif self._cached_mgmt_bssid and not quiet:
            print(
                f"    -> [{label}] rejoin pending "
                f"(BSSID {self._cached_mgmt_bssid} not joined yet)"
            )
        if not self._mgmt_profile_prepared:
            self._mgmt_profile_prepared = True
        return mgmt_creds

    async def _api_12_try_rejoin_mgmt(
        self,
        *,
        bts_host: str,
        bts_username: str,
        bts_password: str,
        mgmt_creds: Any,
    ) -> Any:
        """Rejoin PC to CPE hidden mgmt AP (profile creds first; never BTS UBRMGMT*)."""
        from utils.cpe_mgmt_wifi import CpeMgmtWifiCredentials, rejoin_cpe_mgmt_wifi

        mgmt_creds = await self._resolve_rejoin_mgmt_creds(
            bts_host=bts_host,
            bts_username=bts_username,
            bts_password=bts_password,
            mgmt_creds=mgmt_creds,
        )
        if mgmt_creds is None:
            raise RuntimeError(
                "Cannot rejoin CPE mgmt Wi‑Fi: set cpe_mgmt_ssid/password in profile or pass --local-ipv6."
            )
        await rejoin_cpe_mgmt_wifi(
            self.config,
            CpeMgmtWifiCredentials(
                ssid=mgmt_creds.ssid,
                password=mgmt_creds.password,
                hidden=mgmt_creds.hidden,
            ),
            label="API_12",
            quiet=True,
            bts_host=bts_host or None,
            bts_username=bts_username,
            bts_password=bts_password,
            cached_bssid=self._cached_mgmt_bssid,
        )
        return mgmt_creds

    async def wait_mgmt_device_up_after_reboot(
        self,
        *,
        max_wait_s: float = FW_UPGRADE_VERIFY_MAX_S,
        bts_host: str | None = None,
        bts_username: str = "root",
        bts_password: str = "",
        label: str = "API_12",
    ) -> dict[str, Any]:
        """
        After upgrade-sw (preserveConfig=false): wait for reboot, rejoin CPE 2.4 GHz mgmt AP, API up.
        """
        from utils.cpe_mgmt_wifi import is_mgmt_api_reachable

        start = time.monotonic()
        deadline = start + max_wait_s
        poll_s = POST_UPGRADE_POLL_DELAY_S
        mgmt_creds: Any = None
        last_rejoin = 0.0
        last_progress = 0.0
        rejoin_announced = False
        rejoin_err_logged = False
        can_rejoin = bool(bts_host and bts_password)

        print(
            f"[{label}] Waiting for reboot, then CPE mgmt API up "
            f"(max {max_wait_s / 60:.0f} min; rejoin CPE 2.4 GHz mgmt AP if SSID changed)..."
        )

        while time.monotonic() < deadline:
            if not await is_mgmt_api_reachable(self.config.base_url, timeout_s=2.0):
                print("    -> reboot detected (mgmt API down).")
                break
            await asyncio.sleep(poll_s)
        else:
            print(f"    -> [{label}] reboot not seen yet; waiting for device to return...")

        await asyncio.sleep(API_12_MGMT_AP_GRACE_S)

        while time.monotonic() < deadline:
            elapsed = time.monotonic() - start
            if await is_mgmt_api_reachable(self.config.base_url, timeout_s=3.0):
                joined = getattr(mgmt_creds, "ssid", "") if mgmt_creds else ""
                print(
                    f"[{label}] CPE mgmt API up ({elapsed:.0f}s / {elapsed / 60:.1f} min)"
                    + (f"; mgmt SSID {joined!r}" if joined else "")
                    + "."
                )
                return {"elapsed_s": elapsed, "cpe_mgmt_ssid": joined}

            if can_rejoin and time.monotonic() - last_rejoin >= API_12_REJOIN_INTERVAL_S:
                last_rejoin = time.monotonic()
                if not rejoin_announced:
                    print(f"    -> [{label}] rejoining CPE 2.4 GHz mgmt Wi‑Fi (SSID may have changed)...")
                    rejoin_announced = True
                try:
                    mgmt_creds = await self._api_12_try_rejoin_mgmt(
                        bts_host=bts_host or "",
                        bts_username=bts_username,
                        bts_password=bts_password,
                        mgmt_creds=mgmt_creds,
                    )
                except Exception as exc:
                    if not rejoin_err_logged:
                        print(f"    -> [{label}] mgmt rejoin note: {exc}")
                        rejoin_err_logged = True

            if elapsed - last_progress >= API_12_PROGRESS_INTERVAL_S:
                print(f"    -> [{label}] still waiting for CPE mgmt API ({elapsed / 60:.0f} min)...")
                last_progress = elapsed

            await asyncio.sleep(poll_s)

        total = time.monotonic() - start
        raise RuntimeError(
            f"{label}: CPE mgmt API not reachable within {max_wait_s / 60:.0f} min ({total:.0f}s).\n"
            "  preserveConfig=false changes CPE mgmt SSID — rejoin CPE 2.4 GHz mgmt AP (not BTS)."
        )
