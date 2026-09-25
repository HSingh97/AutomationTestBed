"""Sanity-only SSH helpers — IPv6 bind, retry/wait, no VLAN or bootstrap deps."""

from __future__ import annotations

import asyncio
import re
import time

from scrapli.driver.generic import AsyncGenericDriver

from utils.net_utils import is_ipv6_literal, normalize_ip
from utils.parsers import clean_ssh_output

_OPENWRT_PROMPT = r"[#$]\s*$"
_PROMPT_SUFFIX = re.compile(r"root@[^:]+:[~#$]\s*$")
_PROMPT_ONLY = re.compile(r"^root@[^:]+:[~#$]\s*$")
_FW_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+(?:\.\d+)?)")


def sanitize_sanity_scalar(raw: str) -> str:
    """Strip shell prompts glued to command output (common on OpenWrt scrapli sessions)."""
    text = clean_ssh_output(raw).strip()
    text = _PROMPT_SUFFIX.sub("", text).strip()
    if _PROMPT_ONLY.match(text):
        return ""
    if "root@" in text:
        version = _FW_VERSION_RE.search(text)
        if version:
            return version.group(1)
        head = text.split("root@", 1)[0].strip()
        if head and not head.startswith("root@"):
            return head
        return ""
    return text


def normalize_fw_version(raw: str) -> str:
    """Extract dotted firmware version from noisy SSH output."""
    clean = sanitize_sanity_scalar(raw)
    match = _FW_VERSION_RE.search(clean)
    return match.group(1) if match else clean


def _transport_options(host: str, source_v6: str) -> dict | None:
    if source_v6 and is_ipv6_literal(source_v6) and is_ipv6_literal(host):
        return {"local_addr": normalize_ip(source_v6)}
    return None


async def sanity_ssh_run(
    ssh: AsyncGenericDriver,
    command: str,
    *,
    timeout_s: int = 60,
) -> str:
    response = await ssh.send_command(command, timeout_ops=timeout_s)
    return sanitize_sanity_scalar(response.result)


async def tune_uhttpd_for_playwright(ssh: AsyncGenericDriver, *, quiet: bool = True) -> None:
    """Make LuCI reachable from Chromium (no HTTPS redirect / rfc1918 filter).

    CPE uhttpd defaults (redirect_https=1 + rfc1918_filter=1 + max_requests=3)
    cause Playwright to hang on HTTP→HTTPS while curl still works.
    """
    try:
        await ensure_sanity_ssh_open(ssh, retries=2, timeout_s=10)
        script = (
            "need=0; "
            "r=$(uci -q get uhttpd.main.redirect_https); "
            "f=$(uci -q get uhttpd.main.rfc1918_filter); "
            "m=$(uci -q get uhttpd.main.max_requests); "
            "[ \"$r\" = 0 ] || { uci set uhttpd.main.redirect_https=0; need=1; }; "
            "[ \"$f\" = 0 ] || { uci set uhttpd.main.rfc1918_filter=0; need=1; }; "
            "[ \"${m:-0}\" -ge 50 ] 2>/dev/null || { uci set uhttpd.main.max_requests=50; need=1; }; "
            "if [ \"$need\" = 1 ]; then uci commit uhttpd; "
            "/etc/init.d/uhttpd reload 2>/dev/null || /etc/init.d/uhttpd restart; "
            "echo UHTTPD_TUNED; else echo UHTTPD_OK; fi"
        )
        out = await sanity_ssh_run(ssh, script, timeout_s=45)
        if not quiet and "TUNED" in (out or ""):
            print(f"[sanity] uhttpd tuned for Playwright ({out.strip()})", flush=True)
    except Exception as exc:
        if not quiet:
            print(f"[sanity] uhttpd tune skipped: {exc}", flush=True)


async def ensure_sanity_ssh_open(
    ssh: AsyncGenericDriver,
    *,
    retries: int = 3,
    timeout_s: int = 15,
) -> None:
    """Keep scrapli session usable; reopen on timeout or closed transport."""
    last_error = ""
    for attempt in range(1, retries + 1):
        try:
            await ssh.send_command("echo ok", timeout_ops=timeout_s)
            return
        except Exception as exc:
            last_error = str(exc)
            try:
                await ssh.close()
            except Exception:
                pass
            try:
                await ssh.open()
            except Exception as open_exc:
                last_error = str(open_exc)
            if attempt < retries:
                await asyncio.sleep(min(5 * attempt, 15))
    raise ConnectionError(
        f"[sanity] SSH session not stable after {retries} attempts: {last_error}"
    )


async def open_sanity_plain_ssh(
    host: str,
    password: str,
    *,
    label: str = "device",
    timeout_s: int = 30,
) -> AsyncGenericDriver:
    """Plain IPv4 SSH (no source bind) — for tagged mgmt VLAN access."""
    host = normalize_ip(str(host or "").split("/")[0])
    conn = AsyncGenericDriver(
        host=host,
        auth_username="root",
        auth_password=password,
        auth_strict_key=False,
        transport="asyncssh",
        comms_prompt_pattern=_OPENWRT_PROMPT,
    )
    try:
        await asyncio.wait_for(conn.open(), timeout=max(5, int(timeout_s)))
        await conn.send_command("echo ok", timeout_ops=20)
        return conn
    except Exception as exc:
        try:
            await conn.close()
        except Exception:
            pass
        raise ConnectionError(f"[sanity] {label} SSH failed for {host}: {exc}") from exc


async def open_sanity_plain_ssh_retry(
    hosts: list[str],
    password: str,
    *,
    label: str,
    timeout_s: int = 45,
    attempts: int = 8,
    poll_s: float = 5.0,
) -> AsyncGenericDriver:
    """Try multiple IPv4 hosts with retries (post-mgmt-VLAN tagged access)."""
    last_exc: Exception | None = None
    clean_hosts = [normalize_ip(str(h or "").split("/")[0]) for h in hosts if str(h or "").strip()]
    clean_hosts = list(dict.fromkeys(h for h in clean_hosts if h))
    for attempt in range(1, max(1, attempts) + 1):
        for host in clean_hosts:
            try:
                return await open_sanity_plain_ssh(
                    host, password, label=f"{label}/{host}", timeout_s=timeout_s
                )
            except Exception as exc:
                last_exc = exc
        if attempt < attempts:
            await asyncio.sleep(poll_s)
    raise ConnectionError(f"[sanity] {label} SSH failed for {clean_hosts}: {last_exc}")


async def open_sanity_ssh(
    host: str,
    password: str,
    *,
    source_v6: str = "",
    label: str = "device",
    timeout_s: int = 30,
) -> AsyncGenericDriver:
    host = normalize_ip(host)
    last_error = ""
    for wait_s in (0, 5, 10, 20):
        if wait_s:
            await asyncio.sleep(wait_s)
        conn = AsyncGenericDriver(
            host=host,
            auth_username="root",
            auth_password=password,
            auth_strict_key=False,
            transport="asyncssh",
            transport_options=_transport_options(host, source_v6),
            comms_prompt_pattern=_OPENWRT_PROMPT,
        )
        try:
            await asyncio.wait_for(conn.open(), timeout=timeout_s)
            await conn.send_command("echo ok", timeout_ops=20)
            return conn
        except Exception as exc:
            last_error = str(exc)
            try:
                await conn.close()
            except Exception:
                pass
    raise ConnectionError(f"[sanity] {label} SSH failed for {host}: {last_error}")


async def _probe_boot_id(
    host: str,
    password: str,
    *,
    source_v6: str = "",
    timeout_s: int = 10,
) -> tuple[AsyncGenericDriver | None, str]:
    """One short SSH attempt returning the current Linux boot ID."""
    host = normalize_ip(host)
    conn = AsyncGenericDriver(
        host=host,
        auth_username="root",
        auth_password=password,
        auth_strict_key=False,
        transport="asyncssh",
        transport_options=_transport_options(host, source_v6),
        comms_prompt_pattern=_OPENWRT_PROMPT,
    )
    try:
        await asyncio.wait_for(conn.open(), timeout=timeout_s)
        boot_id = await sanity_ssh_run(
            conn,
            "cat /proc/sys/kernel/random/boot_id",
            timeout_s=timeout_s,
        )
        return conn, boot_id.strip()
    except Exception:
        await close_sanity_ssh(conn)
        return None, ""


async def read_sanity_boot_id(ssh: AsyncGenericDriver) -> str:
    return (
        await sanity_ssh_run(
            ssh,
            "cat /proc/sys/kernel/random/boot_id",
            timeout_s=15,
        )
    ).strip()


async def wait_sanity_reboot_cycle(
    host: str,
    password: str,
    *,
    boot_id_before: str,
    source_v6: str = "",
    label: str = "device",
    transition_timeout_s: int = 240,
    recovery_timeout_s: int = 600,
    poll_s: int = 10,
    settle_s: int = 20,
    alt_hosts: list[str] | None = None,
) -> AsyncGenericDriver:
    """
    Require an actual reboot, then return a stable post-reboot SSH session.

    Firmware flashes can drop SSH briefly before the real reboot. A single
    offline blip is not enough — keep polling until boot_id changes or the
    recovery window expires.

    ``alt_hosts`` are tried in parallel with ``host`` (e.g. lab IPv4 while
    primary is IPv6) so recovery still succeeds if one stack comes up first.
    """
    primary = normalize_ip(host)
    hosts: list[tuple[str, str]] = [
        (primary, source_v6 if is_ipv6_literal(primary) else ""),
    ]
    # Only use caller-provided alts — do NOT guess both lab IPv4s (BTS↔CPE swap risk).
    for alt in list(alt_hosts or []):
        alt_n = normalize_ip(str(alt or "").split("/")[0])
        if not alt_n or alt_n == primary:
            continue
        if any(h == alt_n for h, _ in hosts):
            continue
        hosts.append((alt_n, source_v6 if is_ipv6_literal(alt_n) else ""))

    wait_timeout_s = max(transition_timeout_s, recovery_timeout_s)
    deadline = time.monotonic() + wait_timeout_s
    saw_offline = False
    attempt = 0
    recovered_host = primary
    recovered_bind = hosts[0][1]

    print(
        f"[sanity] {label}: waiting for reboot "
        f"(boot_id={boot_id_before or '(unknown)'}, timeout={wait_timeout_s}s"
        f"{', alt=' + ','.join(h for h, _ in hosts[1:]) if len(hosts) > 1 else ''})",
        flush=True,
    )
    force_reboot_kicked = False
    while time.monotonic() < deadline:
        attempt += 1
        # Mid-wait: if still online with same boot_id, kick SSH reboot once
        # (GUI Proceed / factory reset sometimes never triggers reboot).
        # Kick earlier (attempt>=4) — keep-settings scripts often stall without reboot.
        if (
            not force_reboot_kicked
            and boot_id_before
            and attempt >= 4
            and (deadline - time.monotonic()) < (wait_timeout_s * 0.85)
        ):
            for probe_host, bind in hosts:
                try:
                    kick = await open_sanity_ssh(
                        probe_host,
                        password,
                        source_v6=bind,
                        label=f"{label} force-reboot",
                        timeout_s=12,
                    )
                    try:
                        print(
                            f"[sanity] {label}: GUI reboot stalled — forcing SSH reboot "
                            f"via {probe_host}",
                            flush=True,
                        )
                        await sanity_ssh_run(
                            kick, "(sleep 1; reboot) & echo REBOOT_KICKED", timeout_s=10
                        )
                        force_reboot_kicked = True
                    finally:
                        await close_sanity_ssh(kick)
                    break
                except Exception:
                    continue
        any_online = False
        boot_changed = False
        for probe_host, bind in hosts:
            conn, boot_id = await _probe_boot_id(
                probe_host,
                password,
                source_v6=bind,
                timeout_s=min(10, max(5, poll_s)),
            )
            if conn is None:
                continue
            any_online = True
            if boot_id_before and boot_id and boot_id != boot_id_before:
                print(
                    f"[sanity] {label}: boot ID changed "
                    f"{boot_id_before} → {boot_id} via {probe_host}",
                    flush=True,
                )
                await close_sanity_ssh(conn)
                recovered_host = probe_host
                recovered_bind = bind
                boot_changed = True
                break
            await close_sanity_ssh(conn)

        if boot_changed:
            break

        if not any_online:
            if not saw_offline:
                print(f"[sanity] {label}: device went offline — waiting for reboot", flush=True)
            saw_offline = True

        if attempt == 1 or attempt % 6 == 0:
            detail = "offline" if not any_online else "ssh up, boot_id unchanged"
            print(
                f"[sanity] {label}: reboot pending ({detail}, "
                f"{int(deadline - time.monotonic())}s left)",
                flush=True,
            )
        await asyncio.sleep(max(1, poll_s))
    else:
        raise TimeoutError(
            f"[sanity] {label}: boot ID did not change within {wait_timeout_s}s "
            f"after clicking Proceed (before={boot_id_before or '(unknown)'})"
        )

    print(
        f"[sanity] {label}: reboot confirmed — settling {settle_s}s before SSH",
        flush=True,
    )
    if settle_s > 0:
        await asyncio.sleep(settle_s)

    last_err: Exception | None = None
    # Prefer recovered host, then every candidate (IPv4 often comes up before IPv6).
    settle_order: list[tuple[str, str]] = [(recovered_host, recovered_bind)]
    for h, b in hosts:
        if (h, b) not in settle_order:
            settle_order.append((h, b))
    conn = None
    # Post-reboot SSH can lag well beyond settle_s — retry each host several times.
    settle_deadline = time.monotonic() + max(120, settle_s + 90)
    while time.monotonic() < settle_deadline and conn is None:
        for settle_host, settle_bind in settle_order:
            try:
                conn = await open_sanity_ssh(
                    settle_host,
                    password,
                    source_v6=settle_bind,
                    label=f"{label} post-settle",
                    timeout_s=min(60, max(30, poll_s + 20)),
                )
                recovered_host = settle_host
                recovered_bind = settle_bind
                break
            except Exception as exc:
                last_err = exc
                print(
                    f"[sanity] {label}: post-settle SSH miss at {settle_host} ({exc})",
                    flush=True,
                )
        if conn is None:
            await asyncio.sleep(max(3, poll_s))
    if conn is None:
        raise ConnectionError(
            f"[sanity] {label} post-settle SSH failed for "
            f"{[h for h, _ in settle_order]}: {last_err}"
        )

    boot_id_final = await read_sanity_boot_id(conn)
    if boot_id_before and boot_id_final == boot_id_before:
        await close_sanity_ssh(conn)
        raise RuntimeError(
            f"[sanity] {label}: post-settle SSH recovered but boot ID did not change; "
            "firmware reboot was not completed"
        )
    print(
        f"[sanity] {label}: SSH stable with boot ID {boot_id_final} at {recovered_host}",
        flush=True,
    )
    try:
        await tune_uhttpd_for_playwright(conn, quiet=False)
    except Exception:
        pass
    return conn


async def wait_sanity_ssh(
    host: str,
    password: str,
    *,
    source_v6: str = "",
    label: str = "device",
    timeout_s: int = 600,
    poll_s: int = 10,
) -> AsyncGenericDriver:
    """Poll until device SSH is reachable (post-reboot / post-upgrade)."""
    bind = source_v6 if is_ipv6_literal(host) else ""
    deadline = time.monotonic() + timeout_s
    last_error = ""
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        try:
            conn = await open_sanity_ssh(
                host,
                password,
                source_v6=bind,
                label=label,
                timeout_s=min(30, max(15, poll_s + 5)),
            )
            if attempt > 1:
                print(f"[sanity] {label} SSH ready at {host} (attempt {attempt})", flush=True)
            return conn
        except Exception as exc:
            last_error = str(exc)
            if attempt == 1 or attempt % 6 == 0:
                print(
                    f"[sanity] waiting {label} SSH at {host} "
                    f"(attempt {attempt}, {int(deadline - time.monotonic())}s left)",
                    flush=True,
                )
            await asyncio.sleep(max(1, poll_s))
    raise TimeoutError(f"[sanity] {label} SSH not ready at {host} after {timeout_s}s ({last_error})")


async def wait_stable_ssh_after_power_loss(
    host: str,
    password: str,
    *,
    source_v6: str = "",
    label: str = "device",
    timeout_s: int = 600,
    poll_s: int = 10,
    settle_s: int = 30,
) -> AsyncGenericDriver:
    """Wait for SSH after PDU power cut; settle; return a fresh stable session.

    The first SSH after an interrupted flash install is often transient (device
    still rebooting). Close it, wait for boot to settle, then reconnect.
    """
    bind = source_v6 if is_ipv6_literal(host) else ""
    first = await wait_sanity_ssh(
        host,
        password,
        source_v6=bind,
        label=label,
        timeout_s=timeout_s,
        poll_s=poll_s,
    )
    await close_sanity_ssh(first)
    if settle_s > 0:
        print(
            f"[sanity] {label}: post-power SSH detected — settling {settle_s}s "
            "before backend checks",
            flush=True,
        )
        await asyncio.sleep(settle_s)
    remaining = max(120, timeout_s - settle_s)
    return await wait_sanity_ssh(
        host,
        password,
        source_v6=bind,
        label=f"{label} stable",
        timeout_s=remaining,
        poll_s=poll_s,
    )


async def wait_host_offline(
    host: str,
    *,
    label: str = "device",
    timeout_s: int = 600,
    poll_s: int = 10,
    consecutive: int = 3,
) -> None:
    """ICMP wait until host stays unreachable for `consecutive` polls."""
    host = normalize_ip(host)
    binary = "ping6" if is_ipv6_literal(host) else "ping"
    deadline = time.monotonic() + timeout_s
    streak = 0
    attempt = 0
    print(
        f"[sanity] {label}: waiting for {host} offline "
        f"({consecutive} consecutive misses, timeout={timeout_s}s)",
        flush=True,
    )
    while time.monotonic() < deadline:
        attempt += 1
        proc = await asyncio.create_subprocess_exec(
            binary,
            "-c",
            "1",
            "-W",
            "2",
            host,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        rc = await proc.wait()
        if rc != 0:
            streak += 1
            print(
                f"[sanity] {label}: {host} offline "
                f"(stable {streak}/{consecutive})",
                flush=True,
            )
            if streak >= consecutive:
                return
        else:
            streak = 0
            if attempt == 1 or attempt % 6 == 0:
                print(
                    f"[sanity] {label}: {host} still reachable "
                    f"({int(deadline - time.monotonic())}s left)",
                    flush=True,
                )
        await asyncio.sleep(max(1, poll_s))
    raise TimeoutError(
        f"[sanity] {label}: {host} did not stay offline within {timeout_s}s"
    )


async def close_sanity_ssh(ssh: AsyncGenericDriver | None) -> None:
    if ssh is None:
        return
    try:
        await ssh.close()
    except Exception:
        pass
