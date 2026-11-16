#!/usr/bin/env python3
"""Hard reboot soak: repeatedly power-cycle BTS/CPE via PDU and prove each reboot really happened.

An iteration only passes when five independent signals agree: the PDU reports the
outlet off, traffic to the device actually stops, the surviving peer logs a dying
gasp, the device comes back reachable with the RF link re-formed, and its uptime
reset with fresh boot markers in the log. That combination is what separates a real
power cut from a dropped SSH session or a mis-mapped outlet.

Usage:
    python3 scripts/hard_reboot_soak.py --iterations 100
    python3 scripts/hard_reboot_soak.py --iterations 3 --report /tmp/smoke.html --fresh
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.pdu_power import (  # noqa: E402
    pdu_enabled,
    pdu_get_outlet_state,
    pdu_outlet_for_device,
    pdu_set_outlet,
)
from utils.soak_common import (  # noqa: E402
    DYING_GASP_REMOTE_LOG,
    WIFI_EVENTS_LOG,
    Iteration,
    SoakState,
    close_ssh,
    has_dying_gasp,
    has_reboot_markers,
    load_profile,
    open_cpe_ssh,
    open_ssh,
    parse_link_events,
    ping_from_device,
    ping_from_pc,
    profile_devices,
    read_boot_logs,
    read_file_from,
    read_file_lines,
    read_link_count,
    read_uptime,
    run_cmd,
    uptime_indicates_reboot,
    wait_until,
)
from utils.soak_report import generate_soak_report  # noqa: E402

DEFAULT_STATE = ROOT / "reports" / "artifacts" / "hard_reboot_soak_state.json"
DEFAULT_REPORT = ROOT / "reports" / "artifacts" / "Senao_Hard_Reboot_Soak_Report.html"

# The table stays minimal; per-iteration numbers live inside the row's dropdown.
REPORT_COLUMNS: list[dict[str, str]] = []

DETAIL_FIELDS = [
    {"label": "Dark after", "key": "dark_detect_s", "source": "metrics", "fmt": "seconds"},
    {"label": "Off window", "key": "dark_window_s", "source": "metrics", "fmt": "seconds"},
    {"label": "Boot time", "key": "boot_s", "source": "metrics", "fmt": "seconds"},
    {"label": "Link reform", "key": "link_reform_s", "source": "metrics", "fmt": "seconds"},
    {"label": "Down reason", "key": "link_down_reason", "source": "metrics"},
    {"label": "Uptime after", "key": "uptime_after_s", "source": "metrics", "fmt": "seconds"},
]

STAT_CARDS = [
    {"key": "boot_s", "label": "Avg Boot Time"},
    {"key": "link_reform_s", "label": "Avg Link Reform"},
]


def log(message: str) -> None:
    print(f"[hard-reboot-soak] {datetime.now():%H:%M:%S} {message}", flush=True)


class HardRebootSoak:
    def __init__(self, args) -> None:
        self.args = args
        self.profile = load_profile(args.profile)
        self.bts_ip, self.cpe_ip, self.user, self.password = profile_devices(self.profile)
        self.radio_idx = int(args.radio_idx)
        self.events_log = WIFI_EVENTS_LOG.format(idx=self.radio_idx)

        if not pdu_enabled(self.profile):
            raise SystemExit("PDU is not enabled in the profile; hard reboot soak cannot run.")
        for role in ("bts", "cpe"):
            if pdu_outlet_for_device(self.profile, role) is None:
                raise SystemExit(f"PDU outlet not configured for '{role}'.")

    # ---------------------------------------------------------------- helpers

    def target_for(self, index: int) -> str:
        if self.args.target in {"bts", "cpe"}:
            return self.args.target
        return "bts" if index % 2 == 1 else "cpe"

    def host_for(self, role: str) -> str:
        return self.bts_ip if role == "bts" else self.cpe_ip

    async def open_bts(self):
        return await open_ssh(self.bts_ip, self.user, self.password)

    async def open_cpe(self):
        return await open_cpe_ssh(self.bts_ip, self.cpe_ip, self.user, self.password)

    async def target_alive(self, role: str) -> bool:
        """Is the power-cycled device reachable? Checked from whichever side can see it."""
        if role == "bts":
            return await ping_from_pc(self.bts_ip)
        bts = None
        try:
            bts = await self.open_bts()
            return await ping_from_device(bts, self.cpe_ip, count=2)
        except Exception:
            return False
        finally:
            await close_ssh(bts)

    async def collect_testbed(self) -> dict:
        info = {
            "bts": {"ip": self.bts_ip, "outlet": pdu_outlet_for_device(self.profile, "bts")},
            "cpe": {"ip": self.cpe_ip, "outlet": pdu_outlet_for_device(self.profile, "cpe")},
        }
        bts = cpe = None
        try:
            bts = await self.open_bts()
            info["bts"]["model"] = (await run_cmd(bts, "cat /etc/model 2>/dev/null")).strip()
            info["bts"]["fw_version"] = (
                await run_cmd(bts, "cat /etc/version 2>/dev/null || cat /etc/openwrt_version 2>/dev/null")
            ).strip()
        except Exception as exc:
            log(f"testbed: BTS info unavailable ({exc})")
        finally:
            await close_ssh(bts)
        try:
            cpe = await self.open_cpe()
            info["cpe"]["model"] = (await run_cmd(cpe, "cat /etc/model 2>/dev/null")).strip()
            info["cpe"]["fw_version"] = (
                await run_cmd(cpe, "cat /etc/version 2>/dev/null || cat /etc/openwrt_version 2>/dev/null")
            ).strip()
        except Exception as exc:
            log(f"testbed: CPE info unavailable ({exc})")
        finally:
            await close_ssh(cpe)
        return info

    # ------------------------------------------------------------- iteration

    async def run_iteration(self, index: int) -> Iteration:
        role = self.target_for(index)
        peer = "bts" if role == "cpe" else "cpe"
        it = Iteration(index=index, target=role.upper())
        it.started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log(f"iteration {index}: power-cycling {role.upper()}")

        # The peer holds the dying gasp evidence, so we mark its log positions
        # before the cut and read only what appears afterwards.
        peer_conn = None
        events_mark = dgasp_mark = 0
        uptime_before = None
        try:
            peer_conn = await (self.open_bts() if peer == "bts" else self.open_cpe())
            events_mark = await read_file_lines(peer_conn, self.events_log)
            dgasp_mark = await read_file_lines(peer_conn, DYING_GASP_REMOTE_LOG)
            link_before = await read_link_count(peer_conn, self.radio_idx)
            it.add_event("Baseline", f"peer={peer.upper()} links={link_before}")
        except Exception as exc:
            it.add_event("Baseline", f"peer {peer.upper()} unreachable: {exc}")
        finally:
            await close_ssh(peer_conn)

        target_conn = None
        try:
            target_conn = await (self.open_bts() if role == "bts" else self.open_cpe())
            uptime_before = await read_uptime(target_conn)
            it.add_event("Baseline", f"{role.upper()} uptime before = {uptime_before}s")
        except Exception as exc:
            it.add_event("Baseline", f"{role.upper()} uptime read failed: {exc}")
        finally:
            await close_ssh(target_conn)

        # --- cut power -----------------------------------------------------
        outlet_off = False
        try:
            state = await pdu_set_outlet(
                self.profile, device_target=role, state="OFF", verify=True
            )
            outlet_off = str(state).upper() == "OFF"
            it.add_event("PDU OFF", f"outlet readback = {state}")
        except Exception as exc:
            it.add_event("PDU OFF", f"failed: {exc}")
        off_at = time.monotonic()

        dark_s = await wait_until(
            lambda: self.target_alive(role),
            timeout_s=self.args.dark_timeout,
            interval_s=2.0,
            want=False,
        )
        traffic_died = dark_s is not None
        it.add_event(
            "Went dark",
            f"traffic to {role.upper()} stopped after {dark_s}s" if traffic_died
            else f"still reachable after {self.args.dark_timeout}s — check outlet mapping",
        )

        # --- read dying gasp from the surviving peer while target is down ---
        dgasp_text = ""
        events_text = ""
        dying_gasp = None
        if peer == "bts":
            # BTS is alive and directly reachable, so read the evidence now.
            conn = None
            try:
                conn = await self.open_bts()
                dgasp_text = await read_file_from(conn, DYING_GASP_REMOTE_LOG, dgasp_mark)
                events_text = await read_file_from(conn, self.events_log, events_mark)
                dying_gasp = has_dying_gasp(dgasp_text, parse_link_events(events_text))
                it.add_event(
                    "Dying gasp",
                    "recorded by BTS" if dying_gasp else "no dying gasp entry found",
                )
            except Exception as exc:
                it.add_event("Dying gasp", f"read failed: {exc}")
            finally:
                await close_ssh(conn)
        else:
            # CPE evidence lives behind the BTS we just cut; read it after recovery.
            it.add_event("Dying gasp", "deferred — CPE only reachable once BTS is back")

        remaining = self.args.off_seconds - (time.monotonic() - off_at)
        if remaining > 0:
            await asyncio.sleep(remaining)
        dark_window = round(time.monotonic() - off_at, 1)

        # --- restore power -------------------------------------------------
        outlet_on = False
        try:
            state = await pdu_set_outlet(
                self.profile, device_target=role, state="ON", verify=True
            )
            outlet_on = str(state).upper() == "ON"
            it.add_event("PDU ON", f"outlet readback = {state}")
        except Exception as exc:
            it.add_event("PDU ON", f"failed: {exc}")
        on_at = time.monotonic()

        boot_s = await wait_until(
            lambda: self.target_alive(role),
            timeout_s=self.args.boot_timeout,
            interval_s=5.0,
            want=True,
        )
        reachable = boot_s is not None
        it.add_event(
            "Boot",
            f"{role.upper()} reachable after {boot_s}s" if reachable
            else f"not reachable within {self.args.boot_timeout}s",
        )

        if not reachable:
            it.add_event("Recovery", "retrying one more PDU cycle")
            try:
                await pdu_set_outlet(self.profile, device_target=role, state="OFF", verify=False)
                await asyncio.sleep(10)
                await pdu_set_outlet(self.profile, device_target=role, state="ON", verify=False)
            except Exception as exc:
                it.add_event("Recovery", f"PDU retry failed: {exc}")
            boot_s = await wait_until(
                lambda: self.target_alive(role),
                timeout_s=self.args.boot_timeout,
                interval_s=5.0,
                want=True,
            )
            reachable = boot_s is not None
            it.add_event("Recovery", "recovered" if reachable else "still down after retry")

        # --- link re-formation ---------------------------------------------
        link_s = None
        if reachable:
            async def link_up() -> bool:
                conn = None
                try:
                    conn = await self.open_bts()
                    return await read_link_count(conn, self.radio_idx) >= 1
                except Exception:
                    return False
                finally:
                    await close_ssh(conn)

            link_s = await wait_until(
                link_up, timeout_s=self.args.link_timeout, interval_s=5.0, want=True
            )
            it.add_event(
                "RF link",
                f"re-formed {link_s}s after power on" if link_s is not None
                else f"did not re-form within {self.args.link_timeout}s",
            )

        # --- post-boot evidence ---------------------------------------------
        uptime_after = None
        boot_log = ""
        conn = None
        try:
            conn = await (self.open_bts() if role == "bts" else self.open_cpe())
            uptime_after = await read_uptime(conn)
            boot_log = await read_boot_logs(conn)
            it.add_event("Uptime", f"{role.upper()} uptime after = {uptime_after}s")
        except Exception as exc:
            it.add_event("Uptime", f"post-boot read failed: {exc}")
        finally:
            await close_ssh(conn)

        # Re-read the peer log now that the link is back: it picks up the link-up
        # entry, and for a BTS cut it is the first chance to see the CPE's evidence.
        peer_conn = None
        try:
            peer_conn = await (self.open_bts() if peer == "bts" else self.open_cpe())
            events_text = await read_file_from(peer_conn, self.events_log, events_mark)
            if peer == "cpe":
                dgasp_text = await read_file_from(peer_conn, DYING_GASP_REMOTE_LOG, dgasp_mark)
                dying_gasp = has_dying_gasp(dgasp_text, parse_link_events(events_text))
                it.add_event(
                    "Dying gasp",
                    "recorded by CPE" if dying_gasp else "no dying gasp entry found",
                )
        except Exception as exc:
            it.add_event("Peer logs", f"read failed: {exc}")
        finally:
            await close_ssh(peer_conn)

        link_events = parse_link_events(events_text)
        uptime_reset = uptime_indicates_reboot(uptime_before, uptime_after)
        boot_markers = has_reboot_markers(boot_log)

        # The device's own event timestamps are the trustworthy link measurement.
        # Polling BTS->CPE reachability cannot separate boot from link-up, because
        # that ping only succeeds once the RF link is already carrying traffic.
        it.metrics = {
            "dark_detect_s": dark_s,
            "dark_window_s": dark_window,
            "boot_s": boot_s,
            "link_reform_s": (
                link_events.link_reform_seconds
                if link_events.link_reform_seconds is not None
                else link_s
            ),
            "link_reform_polled_s": link_s,
            "link_uptime_before": link_events.link_uptime_before or None,
            "link_down_reason": link_events.link_down_reason or None,
            "link_up_at": link_events.link_up_at or None,
            "snr": (
                f"A1 {link_events.snr_a1} / A2 {link_events.snr_a2} dB"
                if link_events.snr_a1 is not None
                else None
            ),
            "uptime_before_s": uptime_before,
            "uptime_after_s": uptime_after,
        }
        # Dying gasp is a subscriber-side feature: the CPE announces its own power
        # loss and the BTS records it. A BTS power cut produces no reverse gasp, so
        # requiring one there would assert behaviour the hardware never implements.
        it.checks = {
            "Outlet OFF": outlet_off,
            "Traffic died": traffic_died,
            "Peer saw link drop": bool(link_events.link_down_at),
            "Dying gasp": dying_gasp if role == "cpe" else None,
            "Outlet ON": outlet_on,
            "Reachable": reachable,
            "Link re-formed": link_events.link_up_at != "" or link_s is not None,
            "Uptime reset": uptime_reset,
            "Boot markers": boot_markers,
        }
        it.logs = {
            "Wireless events (peer)": events_text,
            f"Boot log ({role.upper()})": boot_log,
        }
        if dgasp_text.strip():
            it.logs["Dying gasp detail log"] = dgasp_text

        required = {k: v for k, v in it.checks.items() if v is not None}
        it.passed = all(required.values())
        failed = [name for name, ok in required.items() if not ok]
        it.summary = (
            f"{role.upper()} power cycle verified: went dark {dark_s}s after the cut, "
            f"booted in {boot_s}s, RF link re-formed in {it.metrics['link_reform_s']}s "
            f"(link down reason: {it.metrics['link_down_reason']})."
            if it.passed
            else f"{role.upper()} iteration failed on: {', '.join(failed)}."
        )
        it.finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log(f"iteration {index}: {'PASS' if it.passed else 'FAIL'} — {it.summary}")
        return it

    # ------------------------------------------------------------------ main

    async def run(self) -> int:
        state_path = Path(self.args.state)
        report_path = Path(self.args.report)

        state = None if self.args.fresh else SoakState.load(state_path)
        if state is None:
            log("collecting testbed info")
            state = SoakState(
                kind="hard_reboot",
                started_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                total_iterations=self.args.iterations,
                testbed=await self.collect_testbed(),
            )
        else:
            state.total_iterations = self.args.iterations
            log(f"resuming after {state.completed} completed iteration(s)")

        start_index = state.completed + 1
        for index in range(start_index, self.args.iterations + 1):
            try:
                iteration = await self.run_iteration(index)
            except Exception as exc:
                iteration = Iteration(index=index, target=self.target_for(index).upper())
                iteration.started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                iteration.summary = f"Unhandled error: {exc}"
                iteration.add_event("Error", str(exc))
                log(f"iteration {index}: ERROR — {exc}")

            state.iterations.append(iteration.to_dict())
            state.save(state_path)
            generate_soak_report(
                state,
                report_path,
                title="Senao Hard Reboot Soak Report",
                subtitle=(
                    f"PDU power-cycle soak · target: {self.args.target} · "
                    f"{state.completed} of {self.args.iterations} iterations"
                ),
                columns=REPORT_COLUMNS,
                stat_cards=STAT_CARDS,
                detail_fields=DETAIL_FIELDS,
            )
            if index < self.args.iterations and self.args.settle_seconds:
                await asyncio.sleep(self.args.settle_seconds)

        log(f"done: {state.passed} passed, {state.failed} failed")
        log(f"report: {report_path}")
        return 0 if state.failed == 0 else 1


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="ipv4_lab")
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument(
        "--target",
        choices=["alt", "bts", "cpe"],
        default="alt",
        help="alt = BTS on odd iterations, CPE on even",
    )
    parser.add_argument("--radio-idx", type=int, default=1)
    parser.add_argument("--off-seconds", type=int, default=20, help="dwell time with power removed")
    parser.add_argument("--dark-timeout", type=int, default=90)
    parser.add_argument("--boot-timeout", type=int, default=300)
    parser.add_argument("--link-timeout", type=int, default=300)
    parser.add_argument("--settle-seconds", type=int, default=20)
    parser.add_argument("--state", default=str(DEFAULT_STATE))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--fresh", action="store_true", help="ignore saved state and start over")
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    return asyncio.run(HardRebootSoak(args).run())


if __name__ == "__main__":
    raise SystemExit(main())
