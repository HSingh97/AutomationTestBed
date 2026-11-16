"""PDU helpers for automated hard reboot (power cycle).

Digital Loggers Web Power Switch:
- Legacy: MD5 challenge login via /login.tgi, then /outlet?N=ON|OFF
- REST (newer models): HTTP digest auth on /restapi/relay/outlets/N/state/
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Any

import httpx


class PduError(RuntimeError):
    """Base PDU control error."""


class PduAuthError(PduError):
    """PDU login/authentication failed."""


class PduOutletError(PduError):
    """PDU outlet command did not change state."""


def _pdu_cfg(profile: dict[str, Any]) -> dict[str, Any]:
    raw = profile.get("pdu") if isinstance(profile, dict) else None
    return raw if isinstance(raw, dict) else {}


def pdu_enabled(profile: dict[str, Any]) -> bool:
    cfg = _pdu_cfg(profile)
    host = str(cfg.get("host", "")).strip()
    return bool(cfg.get("enabled", False) and host)


def pdu_outlet_for_device(profile: dict[str, Any], device_target: str) -> int | None:
    cfg = _pdu_cfg(profile)
    outlets = cfg.get("outlets") or {}
    raw = outlets.get(str(device_target).lower())
    if raw is None:
        return None
    try:
        outlet = int(raw)
        return outlet if outlet > 0 else None
    except (TypeError, ValueError):
        return None


def _challenge_from_html(html: str) -> str:
    match = re.search(r'name="Challenge"\s+value="([^"]+)"', html or "")
    return match.group(1) if match else ""


def _legacy_login_digest(challenge: str, username: str, password: str) -> str:
    payload = f"{challenge}{username}{password}{challenge}"
    return hashlib.md5(payload.encode()).hexdigest()  # nosec B324 — device protocol


def _parse_legacy_outlet_states(html: str) -> dict[int, str]:
    states: dict[int, str] = {}
    for match in re.finditer(
        r"<tr[^>]*>.*?<td[^>]*>\s*(\d+)\s*</td>.*?<font[^>]*>\s*(ON|OFF)\s*</font>",
        html or "",
        flags=re.IGNORECASE | re.DOTALL,
    ):
        states[int(match.group(1))] = match.group(2).upper()
    return states


class _PduClient:
    def __init__(self, cfg: dict[str, Any], *, outlet: int):
        self.cfg = cfg
        self.outlet = outlet
        self.host = str(cfg.get("host", "")).strip()
        self.scheme = str(cfg.get("scheme", "http")).strip() or "http"
        self.username = str(cfg.get("username", "admin")).strip() or "admin"
        self.password = str(cfg.get("password", "")).strip()
        self.timeout_s = int(cfg.get("timeout_s", 10))
        self.verify_ssl = bool(cfg.get("verify_ssl", False))
        self.api_mode = str(cfg.get("api", "auto")).strip().lower() or "auto"
        # Front-panel / profile outlets are 1-based; Digital Loggers REST is 0-based.
        self.rest_zero_based = bool(cfg.get("rest_zero_based", True))
        self._client: httpx.AsyncClient | None = None
        self._legacy_logged_in = False
        self._rest_ok = False

    def _rest_outlet_index(self, outlet: int) -> int:
        """Map profile outlet number to REST path index."""
        n = int(outlet)
        if self.rest_zero_based and n >= 1:
            return n - 1
        return max(0, n)

    @property
    def base_url(self) -> str:
        return f"{self.scheme}://{self.host}"

    async def __aenter__(self) -> _PduClient:
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout_s,
            verify=self.verify_ssl,
            follow_redirects=True,
        )
        await self._authenticate()
        return self

    async def __aexit__(self, *_) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _authenticate(self) -> None:
        assert self._client is not None
        modes = [self.api_mode] if self.api_mode in {"legacy", "rest"} else ["rest", "legacy"]
        errors: list[str] = []
        for mode in modes:
            try:
                if mode == "rest":
                    await self._login_rest()
                    return
                await self._login_legacy()
                return
            except PduAuthError as exc:
                errors.append(f"{mode}: {exc}")
        raise PduAuthError(
            f"PDU login failed at {self.host} ({'; '.join(errors)}). "
            "Check pdu.username/password, security lockout, and enable legacy or REST API on the PDU."
        )

    async def _login_legacy(self) -> None:
        assert self._client is not None
        response = await self._client.get("/")
        challenge = _challenge_from_html(response.text)
        if not challenge:
            raise PduAuthError("legacy login page missing Challenge field")
        digest = _legacy_login_digest(challenge, self.username, self.password)
        login = await self._client.post(
            "/login.tgi",
            data={"Username": self.username, "Password": digest},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if login.status_code != 200 or not self._client.cookies:
            raise PduAuthError(f"legacy login HTTP {login.status_code} (no session cookie)")
        # Confirm we are past the login form.
        index = await self._client.get("/index.htm")
        if "Challenge" in index.text and "login.tgi" in index.text:
            raise PduAuthError("legacy login returned login page (bad credentials?)")
        self._legacy_logged_in = True

    async def _login_rest(self) -> None:
        assert self._client is not None
        auth = httpx.DigestAuth(self.username, self.password)
        response = await self._client.get(
            "/restapi/relay/outlets/",
            auth=auth,
            headers={"Accept": "application/json"},
        )
        if response.status_code == 403 and "lockout" in (response.text or "").lower():
            raise PduAuthError("REST API security lockout — wait or clear lockout on PDU")
        if response.status_code not in {200, 401}:
            raise PduAuthError(f"REST probe HTTP {response.status_code}")
        if response.status_code == 401:
            raise PduAuthError("REST digest authentication rejected")
        self._rest_ok = True
        self._rest_auth = auth

    async def get_outlet_state(self, outlet: int | None = None) -> str | None:
        outlet_no = int(outlet if outlet is not None else self.outlet)
        if self._rest_ok:
            state = await self._get_outlet_state_rest(outlet_no)
            if state:
                return state
        if self._legacy_logged_in:
            return await self._get_outlet_state_legacy(outlet_no)
        return None

    async def _get_outlet_state_rest(self, outlet: int) -> str | None:
        assert self._client is not None
        rest_idx = self._rest_outlet_index(outlet)
        response = await self._client.get(
            f"/restapi/relay/outlets/{rest_idx}/state/",
            auth=self._rest_auth,
            headers={"Accept": "application/json"},
        )
        if response.status_code != 200:
            return None
        try:
            payload = response.json()
        except json.JSONDecodeError:
            return None
        # Digital Loggers REST often returns a bare true/false body, not {"value": ...}.
        if isinstance(payload, bool):
            return "ON" if payload else "OFF"
        if isinstance(payload, dict):
            value = payload.get("value", payload.get("state"))
            if isinstance(value, bool):
                return "ON" if value else "OFF"
            if isinstance(value, str):
                return value.strip().upper()
        if isinstance(payload, str):
            return payload.strip().upper()
        return None

    async def _get_outlet_state_legacy(self, outlet: int) -> str | None:
        assert self._client is not None
        response = await self._client.get("/index.htm")
        if response.status_code != 200:
            return None
        return _parse_legacy_outlet_states(response.text).get(outlet)

    async def set_outlet(self, state: str, *, outlet: int | None = None) -> str | None:
        outlet_no = int(outlet if outlet is not None else self.outlet)
        normalized = str(state).strip().upper()
        if normalized not in {"ON", "OFF"}:
            raise ValueError(f"PDU outlet state must be ON or OFF, got {state!r}")

        if self._rest_ok:
            ok = await self._set_outlet_rest(outlet_no, normalized)
            if ok:
                return await self.get_outlet_state(outlet_no)

        if self._legacy_logged_in:
            assert self._client is not None
            response = await self._client.get(f"/outlet?{outlet_no}={normalized}")
            if response.status_code != 200:
                raise PduOutletError(f"legacy outlet command HTTP {response.status_code}")
            return await self.get_outlet_state(outlet_no)

        raise PduOutletError("PDU not authenticated")

    async def _set_outlet_rest(self, outlet: int, state: str) -> bool:
        assert self._client is not None
        rest_idx = self._rest_outlet_index(outlet)
        value = "true" if state == "ON" else "false"
        response = await self._client.put(
            f"/restapi/relay/outlets/{rest_idx}/state/",
            auth=self._rest_auth,
            headers={"X-CSRF": "x", "Content-Type": "application/x-www-form-urlencoded"},
            content=f"value={value}",
        )
        return response.status_code in {200, 204}


async def _with_pdu_client(
    profile: dict[str, Any],
    device_target: str,
) -> tuple[_PduClient, int]:
    cfg = _pdu_cfg(profile)
    if not pdu_enabled(profile):
        raise PduError("PDU is not configured/enabled in profile.")
    outlet = pdu_outlet_for_device(profile, device_target)
    if outlet is None:
        raise PduError(f"PDU outlet not configured for device '{device_target}'.")
    client = _PduClient(cfg, outlet=outlet)
    await client.__aenter__()
    return client, outlet


async def pdu_get_outlet_state(profile: dict[str, Any], *, device_target: str) -> str | None:
    client, outlet = await _with_pdu_client(profile, device_target)
    try:
        return await client.get_outlet_state(outlet)
    finally:
        await client.__aexit__(None, None, None)


async def pdu_set_outlet(
    profile: dict[str, Any],
    *,
    device_target: str,
    state: str,
    verify: bool = False,
    verify_timeout_s: float = 15.0,
    poll_s: float = 1.0,
) -> str | None:
    """Set outlet ON/OFF. Optionally verify state via PDU status page/API."""
    client, outlet = await _with_pdu_client(profile, device_target)
    try:
        result = await client.set_outlet(state, outlet=outlet)
        if not verify:
            return result
        expected = str(state).strip().upper()
        deadline = asyncio.get_event_loop().time() + max(verify_timeout_s, 1.0)
        last = result
        while asyncio.get_event_loop().time() < deadline:
            last = await client.get_outlet_state(outlet)
            if last == expected:
                return last
            await asyncio.sleep(max(poll_s, 0.5))
        raise PduOutletError(
            f"PDU outlet {outlet} did not reach {expected} (last={last!r}) at {client.host}"
        )
    finally:
        await client.__aexit__(None, None, None)


async def pdu_power_off(profile: dict[str, Any], *, device_target: str, verify: bool = True) -> None:
    await pdu_set_outlet(profile, device_target=device_target, state="OFF", verify=verify)


async def pdu_power_on(profile: dict[str, Any], *, device_target: str, verify: bool = True) -> None:
    await pdu_set_outlet(profile, device_target=device_target, state="ON", verify=verify)


async def pdu_hard_reboot(profile: dict[str, Any], *, device_target: str) -> None:
    cfg = _pdu_cfg(profile)
    off_seconds = int(cfg.get("cycle_off_s", 15))
    on_boot_wait_s = int(cfg.get("post_on_wait_s", 45))
    await pdu_power_off(profile, device_target=device_target, verify=False)
    await asyncio.sleep(max(off_seconds, 1))
    await pdu_power_on(profile, device_target=device_target, verify=False)
    await asyncio.sleep(max(on_boot_wait_s, 0))


async def pdu_power_loss_during_install(
    profile: dict[str, Any],
    *,
    device_target: str,
    install_delay_s: float = 8.0,
) -> None:
    """Wait briefly after flash Proceed, then hard power-cycle the DUT outlet."""
    target = str(device_target).lower()
    delay = max(float(install_delay_s), 0.0)
    print(
        f"[sanity] PDU: waiting {delay}s after install start, "
        f"then power-cut {target.upper()}",
        flush=True,
    )
    if delay:
        await asyncio.sleep(delay)
    await pdu_hard_reboot(profile, device_target=target)
