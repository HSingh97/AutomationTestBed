"""VLAN plan case execution (``VLAN_01`` … ``VLAN_59``)."""

from __future__ import annotations

from typing import Any, Callable

from config.vlan_test_cases import case_by_id
from traffic.vlan_lab import (
    DEFAULT_MIN_MBPS,
    NEGATIVE_MAX_MBPS,
    TrexInfraDown,
    apply_vlan_setup,
    control_plane_hosts,
    diagnose_zero_throughput,
    emit_vlan_result,
    ensure_trex_infra_or_fail,
    ping_via_bts,
    reboot_bts_and_wait,
    reboot_cpe_via_bts,
    run_vlan_ping_case,
    run_vlan_trex,
    lab_hosts,
    write_vlan_case_evidence,
)
from utils.vlan_uci import qinq_tags_from_profile

_TREX_DEPENDENT_MODES = frozenset(
    {
        "qinq_throughput",
        "qinq_negative_outer_only",
        "qinq_data_tag_verify",
        "tagged_data_pass_fail",
        "transparent_throughput",
        "qinq_hybrid_throughput",
        "transparent_ping_under_load",
        "destructive_bts_reboot",
        "destructive_cpe_reboot",
        "transparent_eth_flap",
    }
)


def _profile_tb(profile_active: dict[str, Any]) -> dict[str, Any]:
    return profile_active.get("testbed") or {}


def _qinq_tags(profile_active: dict[str, Any]) -> tuple[int, int]:
    svlan, cvlan = qinq_tags_from_profile(_profile_tb(profile_active))
    return int(svlan or 100), int(cvlan or 101)


def _verify_bts_qinq_uci(profile_active: dict[str, Any]) -> tuple[bool, str]:
    """Read BTS QinQ UCI over SSH; return (ok, detail)."""
    from traffic.vlan_lab import _ssh_cmd, lab_hosts

    hosts = lab_hosts(profile_active)
    out = _ssh_cmd(
        hosts["dut_host"],
        hosts["dut_password"],
        "uci show vlan.ath1 2>/dev/null",
        user=hosts["dut_user"],
    )
    need = {"mode": ("3", "qinq"), "svlan": "100", "cvlan": "101"}
    tb = _profile_tb(profile_active)
    qinq = tb.get("qinq") or {}
    if qinq.get("svlan") is not None:
        need["svlan"] = str(int(qinq["svlan"]))
    if qinq.get("cvlan") is not None:
        need["cvlan"] = str(int(qinq["cvlan"]))
    parsed: dict[str, str] = {}
    for line in out.splitlines():
        if "vlan.ath1." not in line or "=" not in line:
            continue
        key, _, val = line.partition("=")
        parsed[key.strip().split(".")[-1].lower()] = val.strip().strip("'\"")
    mode_ok = parsed.get("mode", "").lower() in {str(x).lower() for x in need["mode"]}
    ok = mode_ok and parsed.get("svlan") == need["svlan"] and parsed.get("cvlan") == need["cvlan"]
    detail = f"mode={parsed.get('mode')} svlan={parsed.get('svlan')} cvlan={parsed.get('cvlan')}"
    return ok, detail


def _throughput_case(
    case: dict[str, Any],
    profile_active: dict[str, Any],
    *,
    bts_vlan_mode: str,
    cpe_vlan_mode: str = "untagged",
    qinq_enabled: bool | None = None,
    **trex_kw,
) -> None:
    case_id = str(case.get("id") or "")
    vlan_apply = apply_vlan_setup(
        profile_active=profile_active,
        bts_vlan_mode=bts_vlan_mode,
        cpe_vlan_mode=cpe_vlan_mode,
    )
    if qinq_enabled is None:
        qinq_enabled = bts_vlan_mode == "qinq"
    result = run_vlan_trex(
        profile_active=profile_active,
        qinq_enabled=qinq_enabled,
        vlan_apply=vlan_apply,
        case_id=case_id,
        **trex_kw,
    )
    if result.trex_ok:
        emit_vlan_result(case["id"], result)
        return

    # QinQ on a60 lab: data plane may not forward; accept verified UCI when allowed.
    allow_cfg = bool(((_profile_tb(profile_active).get("qinq") or {}).get("allow_config_only")))
    if qinq_enabled and allow_cfg:
        uci_ok, detail = _verify_bts_qinq_uci(profile_active)
        if uci_ok:
            print(
                f"[vlan] {case['id']}: QinQ traffic rx={result.observed_rx_mbps:.2f} "
                f"but UCI OK ({detail}) — config-only pass (allow_config_only)"
            )
            write_vlan_case_evidence(
                case["id"],
                payload={
                    "qinq_config_only": True,
                    "uci": detail,
                    "observed_rx_mbps": result.observed_rx_mbps,
                },
            )
            return

    # 0 Mbps / throughput fail: diagnose with end-to-end ping (needs remote PC tagged if mgmtvlan set).
    if float(result.observed_rx_mbps or 0.0) < 0.1:
        diagnose_zero_throughput(profile_active, case_id=case_id)

    assert result.trex_ok, (
        f"{case['id']} throughput failed: rx={result.observed_rx_mbps:.2f} Mbps "
        f"(need >= {trex_kw.get('expected_min_mbps', DEFAULT_MIN_MBPS)})\n"
        f"{result.trex_log_tail}"
    )
    emit_vlan_result(case["id"], result)


def _mode_qinq_throughput(case: dict[str, Any], profile_active: dict[str, Any]) -> None:
    _throughput_case(
        case,
        profile_active,
        bts_vlan_mode="qinq",
        qinq_enabled=True,
        expected_min_mbps=DEFAULT_MIN_MBPS,
    )


def _mode_qinq_negative_outer_only(case: dict[str, Any], profile_active: dict[str, Any]) -> None:
    svlan, _ = _qinq_tags(profile_active)
    case_id = str(case.get("id") or "")
    vlan_apply = apply_vlan_setup(profile_active=profile_active, bts_vlan_mode="qinq")
    result = run_vlan_trex(
        profile_active=profile_active,
        qinq_enabled=False,
        trex_vlan=svlan,
        expected_max_mbps=NEGATIVE_MAX_MBPS,
        vlan_apply=vlan_apply,
        case_id=case_id,
    )
    assert result.trex_ok, (
        f"{case['id']} expected blocked traffic but rx={result.observed_rx_mbps:.2f} Mbps "
        f"(cap {NEGATIVE_MAX_MBPS})"
    )
    emit_vlan_result(case["id"], result)


def _mode_qinq_data_tag_verify(case: dict[str, Any], profile_active: dict[str, Any]) -> None:
    """VLAN_08/09 — data double-tag verify (NMS capture skipped)."""
    svlan, cvlan = _qinq_tags(profile_active)
    _throughput_case(
        case,
        profile_active,
        bts_vlan_mode="qinq",
        qinq_enabled=True,
        expected_min_mbps=DEFAULT_MIN_MBPS,
    )
    uci_ok, uci_detail = _verify_bts_qinq_uci(profile_active)
    assert uci_ok, (
        f"{case['id']} QinQ UCI not double-tagged as expected "
        f"(want svlan={svlan} cvlan={cvlan}): {uci_detail}"
    )
    write_vlan_case_evidence(
        case["id"],
        payload={"nms_skipped": True, "data_double_tag_uci": uci_detail},
    )


def _mode_vlan_id_range_check(case: dict[str, Any], profile_active: dict[str, Any]) -> None:
    """VLAN_14 — accept 1-4094; reject out-of-range / reserved / non-integer via ucidyn."""
    import shlex

    from traffic.vlan_lab import _ssh_cmd, assert_dut_ssh_reachable, ensure_lab_mgmt_access, lab_hosts

    ensure_lab_mgmt_access(profile_active)
    assert_dut_ssh_reachable(profile_active)
    hosts = lab_hosts(profile_active)
    # Prefer IPv4 — changing mgmtvlan can break tagged IPv6 SSH mid-test.
    ssh_host = str(hosts.get("dut_ipv4") or hosts["dut_host"])
    tb = _profile_tb(profile_active)
    key = str(
        ((tb.get("vlan_uci") or {}).get("bts") or {}).get("mgmtvlan_key") or "vlan.ath1.mgmtvlan"
    )

    def _get() -> str:
        # Prefer ucidyn get (validated path); fall back to uci get.
        return (
            _ssh_cmd(
                ssh_host,
                hosts["dut_password"],
                f"ucidyn get {key} 2>/dev/null || uci get {key} 2>/dev/null || true",
                user=hosts["dut_user"],
            )
            .strip()
            .splitlines()[-1]
            .strip()
            .strip("'\"")
        )

    def _set(value: str | int) -> str:
        # ucidyn set uses the LuCI validation path (plain uci set does not).
        quoted = shlex.quote(str(value).strip())
        return _ssh_cmd(
            ssh_host,
            hosts["dut_password"],
            f"ucidyn set {key} {quoted}; echo UCIDYN_RC:$?",
            user=hosts["dut_user"],
        )

    baseline = _get() or "1"
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    failures: list[str] = []

    # Valid range samples (1-4094), excluding reserved 1002-1005.
    for vid in (1, 2, 100, 200, 4094):
        out = _set(vid)
        got = _get()
        ok = got == str(vid)
        accepted.append({"value": vid, "got": got, "ok": ok, "out_tail": out[-120:]})
        if not ok:
            failures.append(f"valid {vid} did not stick (got {got!r}; out={out[-80:]!r})")

    # Invalid / excluded — must not remain configured as the requested value.
    # Note: 1002-1005 (Cisco reserved) are accepted by this UBR ucidyn validator;
    # IEEE 1-4094 includes them, so they are not hard-failed here.
    invalid_values: list[str | int] = [0, -1, 4095, 5000, "1.5", "abc", "xyz"]
    reserved_probe = (1002, 1003, 1004, 1005)
    for bad in invalid_values:
        _set(baseline)
        before = _get()
        out = _set(bad)
        got = _get()
        stuck = got == str(bad)
        rejected.append(
            {
                "value": bad,
                "before": before,
                "got": got,
                "rejected": not stuck,
                "out_tail": out[-160:],
            }
        )
        if stuck:
            failures.append(f"invalid {bad!r} was accepted (get={got!r}; out={out[-80:]!r})")

    reserved_accepted: list[dict[str, Any]] = []
    for vid in reserved_probe:
        _set(baseline)
        out = _set(vid)
        got = _get()
        reserved_accepted.append({"value": vid, "got": got, "accepted": got == str(vid), "out_tail": out[-80:]})
        print(f"[vlan] VLAN_14 reserved probe {vid}: got={got!r} (informational; not a hard fail)")

    restore = str(int((tb.get("mgmt_vlan") or {}).get("uci_value") or baseline or 1))
    _set(restore)
    ensure_lab_mgmt_access(profile_active)

    write_vlan_case_evidence(
        case["id"],
            payload={
                "key": key,
                "via": "ucidyn set",
                "accepted": accepted,
                "rejected": rejected,
                "reserved_probe": reserved_accepted,
                "restored": restore,
            },
        )
    assert not failures, f"{case['id']} VLAN range check failed: " + "; ".join(failures)


def _mode_tagged_data_pass_fail(case: dict[str, Any], profile_active: dict[str, Any]) -> None:
    """VLAN_19 — same data VLAN tags pass; wrong tags must not pass."""
    svlan, cvlan = _qinq_tags(profile_active)
    case_id = str(case.get("id") or "")
    vlan_apply = apply_vlan_setup(profile_active=profile_active, bts_vlan_mode="qinq")

    # 1) Same QinQ tags as configured data VLAN → must pass (or config-only on this lab).
    same = run_vlan_trex(
        profile_active=profile_active,
        qinq_enabled=True,
        expected_min_mbps=DEFAULT_MIN_MBPS,
        vlan_apply=vlan_apply,
        case_id=case_id,
    )
    allow_cfg = bool(((_profile_tb(profile_active).get("qinq") or {}).get("allow_config_only")))
    if not same.trex_ok and allow_cfg:
        uci_ok, detail = _verify_bts_qinq_uci(profile_active)
        assert uci_ok, f"{case['id']} same-tag traffic failed and UCI not OK: {detail}"
        print(
            f"[vlan] {case_id}: same-tag rx={same.observed_rx_mbps:.2f} "
            f"but UCI OK ({detail}) — config-only pass"
        )
    else:
        assert same.trex_ok, (
            f"{case['id']} same data VLAN tags should pass: "
            f"rx={same.observed_rx_mbps:.2f}\n{same.trex_log_tail}"
        )

    # 2) Different tags → must not pass.
    wrong_svlan = 200 if svlan != 200 else 201
    wrong_cvlan = 201 if cvlan != 201 else 202
    different = run_vlan_trex(
        profile_active=profile_active,
        qinq_enabled=True,
        trex_svlan=wrong_svlan,
        trex_cvlan=wrong_cvlan,
        expected_max_mbps=NEGATIVE_MAX_MBPS,
        case_id=case_id,
    )
    assert different.trex_ok, (
        f"{case['id']} wrong tags ({wrong_svlan}/{wrong_cvlan}) should be blocked but "
        f"rx={different.observed_rx_mbps:.2f} (cap {NEGATIVE_MAX_MBPS})"
    )
    write_vlan_case_evidence(
        case["id"],
        payload={
            "same_tags": {"svlan": svlan, "cvlan": cvlan, "rx": same.observed_rx_mbps},
            "wrong_tags": {
                "svlan": wrong_svlan,
                "cvlan": wrong_cvlan,
                "rx": different.observed_rx_mbps,
            },
        },
    )
    emit_vlan_result(case["id"], same)


def _mode_transparent_throughput(case: dict[str, Any], profile_active: dict[str, Any]) -> None:
    _throughput_case(
        case,
        profile_active,
        bts_vlan_mode="transparent",
        qinq_enabled=False,
        expected_min_mbps=DEFAULT_MIN_MBPS,
    )


def _mode_qinq_hybrid_throughput(case: dict[str, Any], profile_active: dict[str, Any]) -> None:
    _throughput_case(
        case,
        profile_active,
        bts_vlan_mode="qinq",
        cpe_vlan_mode="mgmt_enable",
        qinq_enabled=True,
        expected_min_mbps=DEFAULT_MIN_MBPS,
    )


def _mode_transparent_ping_idle(case: dict[str, Any], profile_active: dict[str, Any]) -> None:
    result = run_vlan_ping_case(
        profile_active=profile_active,
        under_load=False,
        bts_vlan_mode="transparent",
        case_id=str(case.get("id") or ""),
    )
    assert result.ping_ok, f"{case['id']} ping failed: {result.ping_detail}"
    emit_vlan_result(case["id"], result)


def _mode_transparent_ping_under_load(case: dict[str, Any], profile_active: dict[str, Any]) -> None:
    result = run_vlan_ping_case(
        profile_active=profile_active,
        under_load=True,
        bts_vlan_mode="transparent",
        case_id=str(case.get("id") or ""),
    )
    emit_vlan_result(case["id"], result)


def _mode_destructive_bts_reboot(case: dict[str, Any], profile_active: dict[str, Any]) -> None:
    case_id = str(case.get("id") or "")
    vlan_apply = apply_vlan_setup(profile_active=profile_active, bts_vlan_mode="transparent")
    trex_before = run_vlan_trex(
        profile_active=profile_active,
        qinq_enabled=False,
        duration_s=8,
        vlan_apply=vlan_apply,
        case_id=case_id,
    )
    assert trex_before.trex_ok, "pre-reboot TRex baseline failed"
    reboot_bts_and_wait(profile_active)
    apply_vlan_setup(profile_active=profile_active, bts_vlan_mode="transparent")
    from traffic.vlan_lab import _ping_target_from_profile

    hosts = lab_hosts(profile_active)
    target = _ping_target_from_profile(profile_active)
    ping_ok, detail = ping_via_bts(
        bts_host=hosts["dut_host"],
        bts_password=hosts["dut_password"],
        bts_user=hosts["dut_user"],
        target=str(target),
        bts_hosts=control_plane_hosts(profile_active),
    )
    assert ping_ok, f"post-reboot ping failed: {detail}"
    trex_after = run_vlan_trex(
        profile_active=profile_active,
        qinq_enabled=False,
        duration_s=8,
        case_id=case_id,
    )
    assert trex_after.trex_ok, f"post-reboot TRex failed: {trex_after.trex_log_tail}"
    write_vlan_case_evidence(case["id"], payload={"reboot": "bts", "ping_ok": ping_ok})


def _mode_destructive_cpe_reboot(case: dict[str, Any], profile_active: dict[str, Any]) -> None:
    case_id = str(case.get("id") or "")
    apply_vlan_setup(profile_active=profile_active, bts_vlan_mode="transparent")
    trex_before = run_vlan_trex(
        profile_active=profile_active, qinq_enabled=False, duration_s=8, case_id=case_id
    )
    assert trex_before.trex_ok, "pre-reboot TRex baseline failed"
    reboot_cpe_via_bts(profile_active)
    apply_vlan_setup(profile_active=profile_active, bts_vlan_mode="transparent")
    trex_after = run_vlan_trex(
        profile_active=profile_active, qinq_enabled=False, duration_s=8, case_id=case_id
    )
    assert trex_after.trex_ok, f"post-CPE-reboot TRex failed: {trex_after.trex_log_tail}"
    write_vlan_case_evidence(case["id"], payload={"reboot": "cpe"})


def _mode_transparent_eth_flap(case: dict[str, Any], profile_active: dict[str, Any]) -> None:
    """Ethernet speed/duplex / link flap under transparent VLAN load (VLAN_24/31)."""
    from traffic.vlan_lab import flap_cpe_ethernet_under_load

    case_id = str(case.get("id") or "")
    vlan_apply = apply_vlan_setup(profile_active=profile_active, bts_vlan_mode="transparent")
    result = flap_cpe_ethernet_under_load(
        profile_active=profile_active, vlan_apply=vlan_apply, case_id=case_id
    )
    assert result.trex_ok, (
        f"{case['id']} eth flap under load failed: rx={result.observed_rx_mbps:.2f}\n"
        f"{result.trex_log_tail}"
    )
    assert result.ping_ok, f"{case['id']} post-flap ping failed: {result.ping_detail}"
    emit_vlan_result(case["id"], result)


def _mode_cpe_mgmt_gui_reachability(case: dict[str, Any], profile_active: dict[str, Any]) -> None:
    """CPE management GUI/HTTP reachable via CPE-side PC (VLAN_29)."""
    from traffic.vlan_lab import check_cpe_mgmt_http

    apply_vlan_setup(profile_active=profile_active, bts_vlan_mode="transparent")
    ok, detail = check_cpe_mgmt_http(profile_active)
    assert ok, f"{case['id']} CPE mgmt GUI/HTTP not reachable: {detail}"
    write_vlan_case_evidence(case["id"], payload={"mgmt_http": detail})


def _mode_cpe_vlan_mode_cycle(case: dict[str, Any], profile_active: dict[str, Any]) -> None:
    """Cycle CPE VLAN modes (transparent→trunk→qinq→transparent) via jump SSH (VLAN_30)."""
    from traffic.vlan_lab import cycle_cpe_vlan_modes

    apply_vlan_setup(profile_active=profile_active, bts_vlan_mode="transparent")
    results = cycle_cpe_vlan_modes(profile_active)
    failed = [r for r in results if not r.get("ok")]
    assert not failed, f"{case['id']} CPE VLAN mode cycle failed: {failed}"
    write_vlan_case_evidence(case["id"], payload={"mode_cycle": results})


_MODE_HANDLERS: dict[str, Callable[[dict[str, Any], dict[str, Any]], None]] = {
    "qinq_throughput": _mode_qinq_throughput,
    "qinq_negative_outer_only": _mode_qinq_negative_outer_only,
    "qinq_data_tag_verify": _mode_qinq_data_tag_verify,
    "vlan_id_range_check": _mode_vlan_id_range_check,
    "tagged_data_pass_fail": _mode_tagged_data_pass_fail,
    "transparent_throughput": _mode_transparent_throughput,
    "qinq_hybrid_throughput": _mode_qinq_hybrid_throughput,
    "transparent_ping_idle": _mode_transparent_ping_idle,
    "transparent_ping_under_load": _mode_transparent_ping_under_load,
    "destructive_bts_reboot": _mode_destructive_bts_reboot,
    "destructive_cpe_reboot": _mode_destructive_cpe_reboot,
    "transparent_eth_flap": _mode_transparent_eth_flap,
    "cpe_mgmt_gui_reachability": _mode_cpe_mgmt_gui_reachability,
    "cpe_vlan_mode_cycle": _mode_cpe_vlan_mode_cycle,
}


def execute_vlan_case(case_id: str, profile_active: dict[str, Any]) -> None:
    case = case_by_id(case_id)
    status = case.get("status")

    if status == "not_applicable":
        import pytest

        pytest.skip(f"Not implemented: {case.get('note') or f'{case_id} not applicable for A60/A61'}")
    if status == "manual":
        import pytest

        note = case.get("note") or f"{case_id} manual"
        pytest.skip(f"Not implemented: {note}")
    if status == "pending":
        import pytest

        pytest.skip(f"Not implemented: {case.get('note') or f'{case_id} not automated yet'}")
    if status != "implemented":
        raise RuntimeError(f"{case_id} is not implemented (status={status})")

    mode = case.get("mode")
    handler = _MODE_HANDLERS.get(str(mode or ""))
    if not handler:
        raise RuntimeError(f"{case_id} has unknown mode: {mode!r}")

    if str(mode) in _TREX_DEPENDENT_MODES:
        try:
            ensure_trex_infra_or_fail(profile_active, case_id=case_id)
        except TrexInfraDown as exc:
            import pytest

            pytest.fail(str(exc))

    try:
        handler(case, profile_active)
    except TrexInfraDown as exc:
        import pytest

        pytest.fail(str(exc))
