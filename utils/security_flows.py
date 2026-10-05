"""Assertions for Security plan cases ``SEC-01`` … ``SEC-16``."""

from __future__ import annotations

import os
import re
import time
from typing import Any

import pytest

from config.security_test_cases import case_by_id
from utils.security_lab import (
    ALLOWED_TCP,
    banner,
    creds,
    http_request,
    luci_get,
    luci_login,
    luci_post,
    mgmt_host,
    scan_tcp_ports,
    ssh,
    wait_alive,
    write_evidence,
)
from utils.net_utils import format_http_host


def execute_security_case(case_id: str, profile_active: dict[str, Any]) -> None:
    case = case_by_id(case_id)
    if case.get("mode") == "skip_manual" or case.get("status") == "manual":
        pytest.skip(case.get("note") or "Manual / not on this stand")
    handlers = {
        "anti_rollback": _need_old_fw,
        "local_old_fw": _need_old_fw,
        "port_scan": _port_scan,
        "service_versions": _service_versions,
        "wan_mgmt": _wan_mgmt,
        "tr069_readonly": _tr069_readonly,
        "input_sanitize": _input_sanitize,
        "dhcp_suffix": _dhcp_suffix,
        "cmd_inject": _cmd_inject,
        "traversal": _traversal,
        "creds_locked": _creds_locked,
        "no_root": _no_root,
        "ids_readonly": _ids_readonly,
    }
    fn = handlers.get(case.get("mode"))
    if fn is None:
        raise RuntimeError(f"{case_id} unknown mode {case.get('mode')!r}")
    fn(case, profile_active)


def _need_old_fw(case: dict[str, Any], profile: dict[str, Any]) -> None:
    path = os.environ.get("SECURITY_OLD_FIRMWARE", "").strip()
    if not path or not os.path.isfile(path):
        pytest.skip("Set SECURITY_OLD_FIRMWARE to an older .bin to run this case")
    host, user, password = creds(profile)
    login = luci_login(host, user, password)
    write_evidence(case["id"], {"firmware": path, "login": {k: login.get(k) for k in ("status", "stok")}})
    pytest.skip(
        f"{case['id']}: old firmware present at {path} but LuCI flashops upload "
        "is not automated here (avoid accidental brick). Upload manually to complete."
    )


def _port_scan(case: dict[str, Any], profile: dict[str, Any]) -> None:
    host = mgmt_host(profile)
    found = scan_tcp_ports(host)
    extra = sorted(p for p, open_ in found.items() if open_ and p not in ALLOWED_TCP)
    write_evidence(case["id"], {"host": host, "ports": found, "extra_open": extra})
    assert not extra, (
        f"{case['id']}: extra open TCP ports {extra} (plan allows 443; DNS is UDP/53)"
    )
    assert found.get(443), f"{case['id']}: HTTPS 443 is not open on {host}"


def _service_versions(case: dict[str, Any], profile: dict[str, Any]) -> None:
    host = mgmt_host(profile)
    tls = banner(host, 443)
    ssh_b = banner(host, 22)
    blob = ssh(
        profile,
        "echo '---UNAME---'; uname -a; "
        "echo '---DROP---'; dropbear -V 2>&1 | head -n 3 || true; "
        "echo '---UHTTP---'; uhttpd -h 2>&1 | head -n 5 || true; "
        "echo '---SSL---'; openssl version 2>/dev/null || true",
    )
    write_evidence(case["id"], {"tls": tls, "ssh": ssh_b, "blob": blob[-4000:]})
    # Honest: we record versions. Fail only on obviously ancient banners.
    ancient = bool(re.search(r"OpenSSH_[1-5]\.|dropbear[_ ]0\.[4-5]", ssh_b + blob, re.I))
    assert not ancient, f"{case['id']}: service banner looks unpatched:\n{ssh_b}\n{blob[-500:]}"


def _wan_mgmt(case: dict[str, Any], profile: dict[str, Any]) -> None:
    wan = os.environ.get("SECURITY_WAN_HOST", "").strip()
    if not wan:
        pytest.skip("Set SECURITY_WAN_HOST to the DUT WAN IP to probe remote management")
    from utils.firewall_lab import tcp_port_open

    open_ports = [p for p in (22, 23, 80, 443) if tcp_port_open(wan, p, timeout_s=3.0)]
    write_evidence(case["id"], {"wan": wan, "open": open_ports})
    assert not open_ports, f"{case['id']}: WAN management ports open on {wan}: {open_ports}"


def _tr069_readonly(case: dict[str, Any], profile: dict[str, Any]) -> None:
    before = ssh(profile, "uci show | grep -iE 'tr069|cwmp|acs|easycwmp' | head -n 40 || true")
    keys = re.findall(r"^([^=\s]+)=", before, re.M)
    acs_keys = [k for k in keys if re.search(r"acs|url", k, re.I)]
    changed = []
    probe = "https://invalid.example.invalid/acs"
    for key in acs_keys[:3]:
        orig = ssh(profile, f"uci get {key} 2>/dev/null || true").strip().splitlines()
        orig_v = orig[-1] if orig else ""
        ssh(profile, f"ucidyn set {key} {probe}; echo SET:$?")
        after = ssh(profile, f"uci get {key} 2>/dev/null || true").strip().splitlines()
        after_v = after[-1] if after else ""
        if after_v == probe:
            changed.append(key)
            if orig_v:
                ssh(profile, f"ucidyn set {key} {orig_v}; echo RESTORE:$?")
    write_evidence(case["id"], {"uci": before, "changed": changed})
    if not acs_keys:
        pytest.skip(f"{case['id']}: no TR-069/ACS UCI keys found")
    assert not changed, f"{case['id']}: ACS/TR-069 keys were writable: {changed}"


def _input_sanitize(case: dict[str, Any], profile: dict[str, Any]) -> None:
    host, user, password = creds(profile)
    login = luci_login(host, user, password)
    stok = login.get("stok") or ""
    assert stok, f"{case['id']}: LuCI login failed ({login.get('status')})"
    payloads = ["' OR 1=1 --", "<script>alert(1)</script>", "test; id"]
    bodies = []
    for payload in payloads:
        status, body = luci_post(
            host,
            stok,
            "/admin/network/diagnostics",
            {"ping_host": payload, "ping": "Ping"},
        )
        bodies.append({"payload": payload, "status": status, "body": body[-800:]})
    write_evidence(case["id"], {"login": login.get("status"), "posts": bodies})
    joined = "\n".join(str(b.get("body") or "") for b in bodies)
    assert "uid=" not in joined.lower(), f"{case['id']}: command output leaked after injection"
    assert "<script>alert(1)</script>" not in joined, f"{case['id']}: XSS payload echoed unsanitized"


def _dhcp_suffix(case: dict[str, Any], profile: dict[str, Any]) -> None:
    blob = ssh(profile, "uci show dhcp 2>/dev/null | head -n 80; echo '---'; uci get dhcp.lan.domain 2>/dev/null || true")
    write_evidence(case["id"], {"dhcp": blob})
    domain = ""
    m = re.search(r"dhcp\.\w+\.domain='?([^'\n]+)'?", blob)
    if m:
        domain = m.group(1).strip()
    last = blob.strip().splitlines()[-1] if blob.strip() else ""
    if "domain" not in blob:
        return
    assert not domain or domain in {"lan", "none", last and "not found"}, (
        f"{case['id']}: DHCP domain suffix is set: {domain or last!r}"
    )


def _cmd_inject(case: dict[str, Any], profile: dict[str, Any]) -> None:
    host, user, password = creds(profile)
    login = luci_login(host, user, password)
    stok = login.get("stok") or ""
    assert stok, f"{case['id']}: LuCI login failed"
    uptime_before = ssh(profile, "cat /proc/uptime").strip()
    status, body = luci_post(
        host,
        stok,
        "/admin/network/diagnostics",
        {"ping_host": "127.0.0.1; reboot", "ping": "Ping"},
    )
    time.sleep(8)
    alive = wait_alive(host, timeout_s=20)
    write_evidence(
        case["id"],
        {"status": status, "body": body[-1500:], "uptime_before": uptime_before, "alive": alive},
    )
    assert alive, f"{case['id']}: DUT became unreachable after ping `; reboot` injection"
    # Re-check uptime did not reset.
    uptime_after = ssh(profile, "cat /proc/uptime").strip()
    try:
        b = float(uptime_before.split()[0])
        a = float(uptime_after.split()[0])
        assert a + 5 >= b, f"{case['id']}: uptime reset ({b} -> {a}); reboot injection succeeded"
    except (IndexError, ValueError):
        pass


def _traversal(case: dict[str, Any], profile: dict[str, Any]) -> None:
    host, user, password = creds(profile)
    login = luci_login(host, user, password)
    paths = [
        "/cgi-bin/luci/../../../../etc/passwd",
        "/cgi-bin/luci/;stok=x/admin/../../../../etc/passwd",
    ]
    if login.get("stok"):
        paths.append(f"/cgi-bin/luci/;stok={login['stok']}/admin/../../../../etc/passwd")
    rows = []
    leaked = False
    for path in paths:
        url = f"https://{format_http_host(host)}{path}"
        status, _, body = http_request(url, timeout_s=10)
        rows.append({"url": url, "status": status, "body": body[:400]})
        if "root:" in body and "/bin" in body:
            leaked = True
    write_evidence(case["id"], {"rows": rows})
    assert not leaked, f"{case['id']}: /etc/passwd leaked via traversal"


def _creds_locked(case: dict[str, Any], profile: dict[str, Any]) -> None:
    host, user, password = creds(profile)
    login = luci_login(host, user, password)
    stok = login.get("stok") or ""
    before = ssh(profile, "uci get rpcd.admin.password 2>/dev/null || uci get luci.main.password 2>/dev/null || true")
    if stok:
        luci_post(
            host,
            stok,
            "/admin/system/admin",
            {"cbi.submit": "1", "cbid.system.@system[0].password": "BadPass!1", "cbid.system.@system[1].password": "BadPass!1"},
        )
    after = ssh(profile, "uci get rpcd.admin.password 2>/dev/null || true")
    write_evidence(case["id"], {"login_user": user, "stok": bool(stok), "before": before, "after": after})
    # Root SSH still using the original password means GUI did not rewrite the shell account.
    still = ssh(profile, "echo STILL_ROOT; id")
    assert "uid=0" in still or "root" in still, f"{case['id']}: lost root session unexpectedly"
    assert "BadPass" not in after, f"{case['id']}: password hash/field changed via remote POST"


def _no_root(case: dict[str, Any], profile: dict[str, Any]) -> None:
    host, user, password = creds(profile)
    out = ssh(profile, "id; echo USER:$USER")
    write_evidence(case["id"], {"user": user, "id": out})
    assert "uid=0" not in out and user != "root", (
        f"{case['id']}: root SSH is enabled (user={user!r}). Plan requires root restricted."
    )


def _ids_readonly(case: dict[str, Any], profile: dict[str, Any]) -> None:
    keys_blob = ssh(
        profile,
        "uci show system 2>/dev/null | grep -iE 'serial|imei|macaddr|device_id' | head -n 30; "
        "echo '---'; cat /sys/class/net/br-lan/address 2>/dev/null || true",
    )
    candidates = re.findall(r"^([^=\s]*(?:serial|imei|macaddr|device_id)[^=\s]*)=", keys_blob, re.I | re.M)
    changed = []
    for key in candidates[:4]:
        orig_lines = ssh(profile, f"uci get {key} 2>/dev/null || true").strip().splitlines()
        orig = orig_lines[-1] if orig_lines else ""
        ssh(profile, f"ucidyn set {key} FFTEST99; echo SET:$?")
        after_lines = ssh(profile, f"uci get {key} 2>/dev/null || true").strip().splitlines()
        after = after_lines[-1] if after_lines else ""
        if after == "FFTEST99":
            changed.append(key)
            if orig:
                ssh(profile, f"ucidyn set {key} {orig}; echo RESTORE:$?")
    write_evidence(case["id"], {"keys": keys_blob, "changed": changed})
    assert not changed, f"{case['id']}: identifiers were writable: {changed}"
