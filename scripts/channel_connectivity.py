#!/usr/bin/env python3
"""Ping the CPE once for every channel of each bandwidth.

Order is bandwidth, then channel. The HT mode is applied with the first
channel of that bandwidth. Each later channel is only a channel change,
then an end-to-end ping from this host to the CPE.

Channel numbers come from the BTS ``/www/channels.json`` list for its country.
HT mode is ``advwireless.athN.htmode``. The live channel is
``advwireless.athN.channel``.

Example:
  python3 scripts/channel_connectivity.py \\
    --bts-ip 192.168.3.20 --cpe-ip 192.168.3.21 \\
    --password '***' --bandwidths HT20,HT40,HT80
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pages.commands import RootCommands
from traffic.dut_radio_config import parse_running_htmode, run_ssh_command
from traffic.operating_rate_table import normalize_bandwidth, uci_htmode_matches, uci_htmode_value
from utils.bench_config import profile_for_stand
from utils.profile_manager import load_profile_bundle
from utils.channel_connectivity_report import link_rate_check, write_channel_connectivity_report
from utils.parsers import parse_iwconfig_active_channel


def next_channels(channels: list[int], current: int, count: int) -> list[int]:
    """Channels that follow ``current`` in the bandwidth list."""
    if count < 1:
        raise ValueError("channel count must be at least 1")
    try:
        index = channels.index(int(current))
    except ValueError as exc:
        raise ValueError(f"channel {current} is not in this bandwidth list") from exc
    selected = channels[index + 1 : index + 1 + count]
    if len(selected) < count:
        raise ValueError(
            f"only {len(selected)} channel(s) remain after {current}; asked for {count}"
        )
    return selected


def parse_channel_selection(raw: str, available: list[int]) -> list[int]:
    """Return the requested channels, or the full list when ``raw`` is blank or ``all``."""
    text = str(raw or "").strip()
    if not text or text.lower() == "all":
        return list(available)
    selected: list[int] = []
    for part in text.split(","):
        item = part.strip()
        if not item:
            continue
        if not item.isdigit():
            raise ValueError(f"channel must be a number, got {item}")
        number = int(item)
        if number not in selected:
            selected.append(number)
    if not selected:
        raise ValueError("channel list is empty")
    return selected


def channels_for_bandwidth(catalog: dict, country: str, bandwidth: str) -> list[int]:
    """Return the GUI channel list for one country and bandwidth."""
    countries = catalog.get("val") if isinstance(catalog, dict) else None
    if not isinstance(countries, dict):
        raise ValueError("channels.json has no 'val' country map")
    table = countries.get(str(country))
    if not isinstance(table, dict):
        known = ", ".join(sorted(countries))
        raise ValueError(f"country {country} is not in channels.json (have {known})")
    key = uci_htmode_value(bandwidth)
    raw = table.get(key)
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"no channels for {key} in country {country}")
    channels: list[int] = []
    for item in raw:
        number = int(str(item).strip())
        if number not in channels:
            channels.append(number)
    return channels


def parse_channels_json(raw: str) -> dict:
    text = str(raw or "")
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("channels.json payload was not valid JSON")
    return json.loads(text[start : end + 1])


def active_channel_number(iwconfig_text: str) -> int | None:
    text = str(iwconfig_text or "")
    if "Not-Associated" in text or "No such device" in text:
        return None
    parsed = parse_iwconfig_active_channel(text)
    if not parsed:
        return None
    head = str(parsed).split("(", 1)[0].strip()
    try:
        return int(head)
    except ValueError:
        return None


def apply_commands(radio_idx: int, bandwidth: str | None, channel: int) -> str:
    """One ucidyn apply. Bandwidth is included only when the width changes."""
    commands: list[str] = []
    if bandwidth is not None:
        commands.append(
            f"ucidyn set advwireless.ath{radio_idx}.htmode {uci_htmode_value(bandwidth)}"
        )
    commands.append(f"ucidyn set advwireless.ath{radio_idx}.channel {int(channel)}")
    commands.append("ucidyn apply")
    commands.append("echo APPLY_DONE")
    return " && ".join(commands)


def _firmware(args: argparse.Namespace, ip: str) -> str:
    try:
        raw = run_ssh_command(ip, args.user, args.password, "cat /etc/version", timeout_s=20)
    except RuntimeError:
        return "—"
    lines = [line.strip() for line in raw.splitlines() if line.strip() and not line.strip().startswith("cat ")]
    return lines[-1] if lines else "—"


def _last_event_line(args: argparse.Namespace) -> str:
    try:
        text = run_ssh_command(
            args.bts_ip,
            args.user,
            args.password,
            "tail -n 1 /tmp/kwn-wifi1-events.log 2>/dev/null",
            timeout_s=20,
        )
    except RuntimeError:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _link_since(args: argparse.Namespace, marker: str, *, width_change: bool) -> tuple[str, str, str]:
    """Return link_drop, note, and dfs flag from the wifi event log after marker."""
    try:
        text = run_ssh_command(
            args.bts_ip,
            args.user,
            args.password,
            "tail -n 40 /tmp/kwn-wifi1-events.log 2>/dev/null",
            timeout_s=20,
        )
    except RuntimeError:
        return "unknown", "", "no"
    if marker and marker in text:
        text = text.split(marker, 1)[-1]
    dfs = "yes" if "DFS CAC started" in text else "no"
    if dfs == "yes":
        return "expected", "DFS CAC", dfs
    if "terminated link" in text:
        if width_change:
            return "expected", "width change", dfs
        reason = ""
        for line in text.splitlines():
            if "terminated link" in line and "Reason:" in line:
                reason = line.rsplit("Reason:", 1)[-1].strip()
        return "dropped", reason or "link terminated", dfs
    return "seamless", "", dfs


def _ping_once(host: str, *, count: int, wait_s: int) -> tuple[bool, str]:
    command = ["ping", "-c", str(count), "-W", str(wait_s), host]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=count * (wait_s + 2) + 5,
        )
    except subprocess.TimeoutExpired:
        return False, "ping timed out"
    text = (completed.stdout or "") + (completed.stderr or "")
    ok = completed.returncode == 0 and "0% packet loss" in text
    summary = ""
    for line in text.splitlines():
        if "packet loss" in line or "min/avg/max" in line:
            summary = line.strip()
            break
    if summary:
        return ok, summary
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return ok, lines[-1] if lines else "ping failed"


def _radio_matches(radio: dict[str, str], bandwidth: str, channel: int) -> bool:
    """Configured GUI htmode and the live channel. cfg80211tool follows a different key."""
    return (
        uci_htmode_matches(bandwidth, radio["htmode"])
        and radio["channel"] == str(channel)
        and radio["active"] == str(channel)
    )


def _wait_for_link(
    args: argparse.Namespace,
    *,
    bandwidth: str,
    channel: int,
) -> tuple[dict[str, str], bool, str, bool]:
    """Wait until the driver has the new width and channel, then ping the CPE."""
    deadline = time.monotonic() + args.link_wait_s
    radio = {
        "htmode": "",
        "channel": "",
        "running": "",
        "active": "",
        "mode_raw": "",
        "iwconfig": "",
    }
    ping_detail = "no reply"
    config_ok = False
    while time.monotonic() < deadline:
        radio = read_radio(args.bts_ip, args.user, args.password, args.radio_index)
        config_ok = _radio_matches(radio, bandwidth, channel)
        if config_ok:
            ping_ok, ping_detail = _ping_once(args.cpe_ip, count=args.ping_count, wait_s=2)
            if ping_ok:
                return radio, True, ping_detail, True
        time.sleep(5)
    if not config_ok:
        radio = read_radio(args.bts_ip, args.user, args.password, args.radio_index)
        config_ok = _radio_matches(radio, bandwidth, channel)
    ping_ok, ping_detail = _ping_once(args.cpe_ip, count=args.ping_count, wait_s=2)
    return radio, ping_ok, ping_detail, config_ok


def _uci(ip: str, user: str, password: str, key: str) -> str:
    try:
        raw = run_ssh_command(ip, user, password, f"uci -q get {key}", timeout_s=20)
    except RuntimeError:
        return ""
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def read_radio(ip: str, user: str, password: str, radio_idx: int) -> dict[str, str]:
    htmode = _uci(ip, user, password, f"advwireless.ath{radio_idx}.htmode")
    channel = _uci(ip, user, password, f"advwireless.ath{radio_idx}.channel")
    try:
        mode = run_ssh_command(ip, user, password, RootCommands.get_bandwidth(radio_idx), timeout_s=20)
    except RuntimeError as exc:
        mode = str(exc)
    try:
        iwconfig = run_ssh_command(ip, user, password, f"iwconfig ath{radio_idx}", timeout_s=20)
    except RuntimeError as exc:
        iwconfig = str(exc)
    running = parse_running_htmode(mode) or ""
    active = active_channel_number(iwconfig)
    return {
        "htmode": htmode,
        "channel": channel,
        "running": running,
        "active": "" if active is None else str(active),
        "mode_raw": mode.replace("\n", " ").strip(),
        "iwconfig": iwconfig.strip(),
    }


def _resolve_lab_endpoints(args: argparse.Namespace) -> None:
    """Fill password and IPs from the stand profile. Explicit CLI values stay."""
    profile_name = str(args.profile or "").strip()
    if not profile_name and str(args.stand or "").strip():
        profile_name = profile_for_stand(args.stand)
    if not profile_name:
        missing = [
            name
            for name, value in (
                ("--password", args.password),
                ("--bts-ip", args.bts_ip),
                ("--cpe-ip", args.cpe_ip),
            )
            if not str(value or "").strip()
        ]
        if missing:
            raise RuntimeError(f"required when --stand is not set: {', '.join(missing)}")
        return
    bundle = load_profile_bundle(profile_name=profile_name)
    dut = bundle.active.get("dut") or {}
    if not str(args.password or "").strip():
        args.password = str(dut.get("password") or "")
    if not str(args.user or "").strip():
        args.user = str(dut.get("username") or "root")
    if not str(args.bts_ip or "").strip():
        args.bts_ip = str(dut.get("local_ip") or dut.get("ssh_host") or dut.get("local_ipv6") or "")
    if not str(args.cpe_ip or "").strip():
        remotes = dut.get("remote_ips") or dut.get("remote_ipv6s") or []
        args.cpe_ip = str(remotes[0]) if remotes else ""
    if not str(args.password or "").strip() or not str(args.bts_ip or "").strip() or not str(args.cpe_ip or "").strip():
        raise RuntimeError(f"profile {profile_name} is missing the DUT password or a BTS/CPE IP")


def run_sweep(args: argparse.Namespace) -> int:
    _resolve_lab_endpoints(args)
    catalog = parse_channels_json(
        run_ssh_command(
            args.bts_ip,
            args.user,
            args.password,
            "cat /www/channels.json",
            timeout_s=30,
        )
    )
    country = _uci(args.bts_ip, args.user, args.password, f"wireless.wifi{args.radio_index}.country")
    if args.country:
        country = args.country
    bandwidths = [normalize_bandwidth(item) for item in args.bandwidths.split(",") if item.strip()]
    start_after: int | None = None
    if args.next_channels:
        live_ht = _uci(
            args.bts_ip, args.user, args.password, f"advwireless.ath{args.radio_index}.htmode"
        )
        live_ch = _uci(
            args.bts_ip, args.user, args.password, f"advwireless.ath{args.radio_index}.channel"
        )
        if not live_ht or not live_ch:
            raise RuntimeError("could not read the current advwireless htmode and channel")
        bandwidths = [normalize_bandwidth(live_ht)]
        start_after = int(live_ch)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"channel_connectivity_{stamp}.csv"
    iwconfig_path = out_dir / f"channel_connectivity_{stamp}_iwconfig.log"
    fields = [
        "bandwidth",
        "channel",
        "htmode_uci",
        "channel_uci",
        "running_htmode",
        "active_channel",
        "ping_ok",
        "ping_detail",
        "config_ok",
        "link_drop",
        "link_note",
        "dfs",
        "note",
    ]
    rows: list[dict[str, str]] = []
    channels_by_bw: dict[str, list[int]] = {}
    print(
        f"BTS {args.bts_ip}  CPE {args.cpe_ip}  country {country}  "
        f"bandwidths {', '.join(bandwidths)}"
    )
    print(f"Ping source is this host, destination {args.cpe_ip}")
    print(f"CSV {csv_path}")
    print(f"iwconfig log {iwconfig_path}")

    with csv_path.open("w", newline="") as handle, iwconfig_path.open("w") as iw_handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for bandwidth in bandwidths:
            planned = channels_for_bandwidth(catalog, country, bandwidth)
            channels_by_bw[bandwidth] = list(planned)
            if start_after is not None:
                channels = next_channels(planned, start_after, args.next_channels)
            else:
                channels = parse_channel_selection(args.channels, planned)
                for channel in channels:
                    if channel not in channels_by_bw[bandwidth]:
                        channels_by_bw[bandwidth].append(channel)
            print(f"\n=== {bandwidth}  {len(channels)} channels ===")
            for index, channel in enumerate(channels):
                set_bandwidth = bandwidth if index == 0 else None
                command = apply_commands(args.radio_index, set_bandwidth, channel)
                link_marker = _last_event_line(args)
                note = ""
                try:
                    run_ssh_command(
                        args.bts_ip,
                        args.user,
                        args.password,
                        command,
                        timeout_s=args.apply_timeout_s,
                    )
                except RuntimeError as exc:
                    note = f"apply failed: {exc}"
                    print(f"[{bandwidth} ch {channel}] {note}")
                radio, ping_ok, ping_detail, config_ok = _wait_for_link(
                    args,
                    bandwidth=bandwidth,
                    channel=channel,
                )
                link_drop, link_note, dfs = _link_since(
                    args, link_marker, width_change=set_bandwidth is not None
                )
                passed = config_ok and ping_ok
                row = {
                    "bandwidth": bandwidth,
                    "channel": str(channel),
                    "htmode_uci": radio["htmode"],
                    "channel_uci": radio["channel"],
                    "running_htmode": radio["running"],
                    "active_channel": radio["active"],
                    "ping_ok": "yes" if ping_ok else "no",
                    "ping_detail": ping_detail,
                    "config_ok": "yes" if config_ok else "no",
                    "link_drop": link_drop,
                    "link_note": link_note,
                    "dfs": dfs,
                    "note": note,
                }
                writer.writerow(row)
                row["iwconfig"] = (radio.get("iwconfig") or "").strip()
                rows.append(row)
                handle.flush()
                iw_handle.write(f"===== {bandwidth} channel {channel} =====\n")
                iw_handle.write(radio.get("iwconfig") or "(no iwconfig output)")
                iw_handle.write("\n\n")
                iw_handle.flush()
                state = "PASS" if passed else "FAIL"
                rate = link_rate_check(row)
                if passed and rate["status"] == "mismatch":
                    state = "RATE"
                print(
                    f"[{bandwidth} {index + 1}/{len(channels)}] ch {channel} {state} "
                    f"uci={radio['htmode']}/{radio['channel']} "
                    f"running={radio['running'] or radio['mode_raw']} "
                    f"active={radio['active'] or '-'} ping={'up' if ping_ok else 'down'} "
                    f"rate={rate['label']} {rate['note']} {ping_detail}"
                )

    passed_rows = [
        row for row in rows if row["config_ok"] == "yes" and row["ping_ok"] == "yes"
    ]
    html_path = write_channel_connectivity_report(
        rows,
        out_dir / f"Channel_Connectivity_{stamp}_Grafana.html",
        bts_ip=args.bts_ip,
        cpe_ip=args.cpe_ip,
        country=country,
        channels_by_bw=channels_by_bw,
        executed_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        report_id=stamp,
        bts_fw=_firmware(args, args.bts_ip),
        cpe_fw=_firmware(args, args.cpe_ip),
    )
    print(
        f"\nDone {len(passed_rows)}/{len(rows)} passed  CSV {csv_path}\n"
        f"iwconfig log {iwconfig_path}\n"
        f"Grafana report {html_path}"
    )
    return 0 if rows and len(passed_rows) == len(rows) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bandwidth and channel ping sweep")
    parser.add_argument("--stand", default="", help="Lab bench id; supplies the DUT password and default IPs")
    parser.add_argument("--profile", default="", help="Profile name when --stand is not set")
    parser.add_argument("--bts-ip", default="", help="BTS management IP (overrides the stand profile)")
    parser.add_argument("--cpe-ip", default="", help="CPE management IP (overrides the stand profile)")
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", default="", help="DUT password (overrides the stand profile)")
    parser.add_argument("--radio-index", type=int, default=1)
    parser.add_argument("--bandwidths", default="HT20,HT40,HT80")
    parser.add_argument(
        "--channels",
        default="all",
        help="Comma-separated channel numbers, or all",
    )
    parser.add_argument(
        "--next-channels",
        type=int,
        default=0,
        help="Stay on the current advwireless bandwidth and test this many following channels",
    )
    parser.add_argument("--country", default="", help="Override wireless.wifiN.country")
    parser.add_argument("--link-wait-s", type=float, default=180.0)
    parser.add_argument("--apply-timeout-s", type=int, default=120)
    parser.add_argument("--ping-count", type=int, default=3)
    parser.add_argument(
        "--output-dir",
        default="reports/artifacts",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run_sweep(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
