"""Security lab: LuCI HTTP probes, port scan, UCI identifier checks."""

from __future__ import annotations

import json
import os
import re
import socket
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from traffic.vlan_lab import _ssh_cmd, lab_hosts
from utils.firewall_lab import nmap_state, tcp_port_open
from utils.net_utils import format_http_host

ARTIFACT_DIR = Path("reports/artifacts/security_evidence")
ALLOWED_TCP = {443}
ALLOWED_UDP = {53}
COMMON_TCP = (21, 22, 23, 80, 443, 161, 4430, 8080, 8443)


def mgmt_host(profile_active: dict[str, Any]) -> str:
    dut = (profile_active or {}).get("dut") or {}
    hosts = lab_hosts(profile_active)
    return str(
        dut.get("local_ip") or dut.get("ssh_host") or hosts.get("dut_ipv4") or hosts.get("dut_host") or ""
    ).split("/")[0]


def creds(profile_active: dict[str, Any]) -> tuple[str, str, str]:
    dut = (profile_active or {}).get("dut") or {}
    hosts = lab_hosts(profile_active)
    host = mgmt_host(profile_active)
    user = str(hosts.get("dut_user") or dut.get("username") or "root")
    password = str(hosts.get("dut_password") or dut.get("password") or "")
    return host, user, password


def ssh(profile_active: dict[str, Any], remote: str, *, timeout_s: int = 40) -> str:
    host, user, password = creds(profile_active)
    return _ssh_cmd(host, password, remote, user=user, timeout_s=timeout_s)


def write_evidence(case_id: str, payload: dict[str, Any]) -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    path = ARTIFACT_DIR / f"{case_id}.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"[security] evidence -> {path}")


def _ssl_ctx() -> ssl.SSLContext:
    ctx = ssl._create_unverified_context()
    return ctx


def http_request(
    url: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout_s: float = 12,
    method: str | None = None,
) -> tuple[int, str, str]:
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout_s, context=_ssl_ctx()) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return int(resp.status), str(resp.geturl()), body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        return int(exc.code), url, body
    except Exception as exc:
        return 0, url, str(exc)


def luci_login(host: str, user: str, password: str) -> dict[str, Any]:
    base = f"https://{format_http_host(host)}/cgi-bin/luci/"
    payload = urlencode({"luci_username": user, "luci_password": password}).encode()
    status, final, body = http_request(
        base,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout_s=15,
    )
    stok = ""
    m = re.search(r"stok=([a-fA-F0-9]+)", final + "\n" + body)
    if m:
        stok = m.group(1)
    return {"status": status, "url": final, "stok": stok, "body": body[-3000:]}


def luci_get(host: str, stok: str, path: str) -> tuple[int, str]:
    url = f"https://{format_http_host(host)}/cgi-bin/luci/;stok={stok}{path}"
    status, _, body = http_request(url, timeout_s=15)
    return status, body


def luci_post(host: str, stok: str, path: str, fields: dict[str, str]) -> tuple[int, str]:
    url = f"https://{format_http_host(host)}/cgi-bin/luci/;stok={stok}{path}"
    status, _, body = http_request(
        url,
        data=urlencode(fields).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout_s=20,
    )
    return status, body


def scan_tcp_ports(host: str) -> dict[int, bool]:
    found: dict[int, bool] = {}
    extra = os.environ.get("SECURITY_SCAN_PORTS", "")
    ports = list(COMMON_TCP)
    if extra:
        ports.extend(int(p) for p in extra.split(",") if p.strip().isdigit())
    for port in sorted(set(ports)):
        nmap = nmap_state(host, port)
        if nmap == "open":
            found[port] = True
        elif nmap in {"closed", "filtered"}:
            found[port] = False
        else:
            found[port] = tcp_port_open(host, port, timeout_s=2.0)
    return found


def banner(host: str, port: int, *, timeout_s: float = 3.0) -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout_s)
    try:
        sock.connect((host, int(port)))
        if port == 443:
            ctx = _ssl_ctx()
            tls = ctx.wrap_socket(sock, server_hostname=host)
            try:
                tls.sendall(b"HEAD / HTTP/1.0\r\nHost: %s\r\n\r\n" % host.encode())
                return tls.recv(512).decode("utf-8", errors="replace")
            finally:
                tls.close()
        try:
            sock.sendall(b"\r\n")
            return sock.recv(256).decode("utf-8", errors="replace")
        except OSError:
            return ""
    except OSError as exc:
        return str(exc)
    finally:
        try:
            sock.close()
        except OSError:
            pass


def ping_alive(host: str, timeout_s: float = 3.0) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout_s)
    try:
        sock.connect((host, 443))
        return True
    except OSError:
        try:
            sock.close()
        except OSError:
            pass
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout_s)
        try:
            sock.connect((host, 22))
            return True
        except OSError:
            return False
    finally:
        try:
            sock.close()
        except OSError:
            pass


def wait_alive(host: str, *, timeout_s: float = 90) -> bool:
    end = time.time() + timeout_s
    while time.time() < end:
        if ping_alive(host):
            return True
        time.sleep(3)
    return False
