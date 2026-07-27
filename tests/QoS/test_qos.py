"""QoS suite — synced to ``Senao UBR P2MP Test Plan_Jul23.xlsx`` sheet ``QoS``.

Each test function is marked with the plan TESTCASE-ID (``QoS_01`` … ``QoS_40``).

Requires live TRex + DUT for implemented cases. Gate with::

  pytest tests/QoS -m QoS --allow-qos-lab -q
  pytest tests/QoS -m QoS_01 --allow-qos-lab -q
"""

from __future__ import annotations

import pytest

from config.qos_test_cases import case_by_id
from utils.qos_flows import execute_qos_case

pytestmark = [pytest.mark.QoS]


@pytest.fixture(autouse=True)
def _require_qos_lab(request):
    """Gate live lab traffic; not_available / manual / pending cases do not need TRex."""
    name = request.node.name
    # test_qos_27 etc.
    import re

    m = re.search(r"test_qos_(\d+)", name)
    if m:
        from config.qos_test_cases import case_by_id

        case = case_by_id(f"QoS_{int(m.group(1)):02d}")
        if case.get("status") in ("not_available", "pending", "manual"):
            return
        if case.get("mode") == "setup_covered":
            return
    if not request.config.getoption("--allow-qos-lab"):
        pytest.skip("QoS lab cases require --allow-qos-lab (live TRex + DUT queue_stats)")


def _run(case_id: str, request=None) -> None:
    case = case_by_id(case_id)
    status = case.get("status")
    note = case.get("note") or case.get("title", "")[:80]

    if status == "not_available":
        # Intentionally skipped — feature/lab not applicable (see case note).
        pytest.skip(note)
    if status == "manual":
        pytest.skip(note if note.lower().startswith("to be tested manually") else f"To be tested manually: {note}")
    if status == "pending":
        pytest.skip(f"{case_id} not automated yet — {note}")
    if status != "implemented":
        pytest.skip(f"{case_id} not automated yet — {note}")

    if case.get("mode") == "reboot_retention":
        cfg = request.config if request is not None else None
        if cfg is None or not cfg.getoption("--allow-qos-destructive"):
            pytest.skip(
                f"{case_id} reboots the BTS — pass --allow-qos-destructive with --allow-qos-lab"
            )

    execute_qos_case(case_id)


@pytest.mark.QoS_01
@pytest.mark.Functional
def test_qos_01():
    """Validate that QoS ensures voice packets are prioritized while maintaining acceptable performance for data traffic."""
    _run("QoS_01")


@pytest.mark.QoS_02
@pytest.mark.Functional
def test_qos_02():
    """Confirm video streams are prioritized to guarantee smooth playback, with non‑video traffic handled at lower priority."""
    _run("QoS_02")


@pytest.mark.QoS_03
@pytest.mark.Functional
def test_qos_03():
    """Verify that gaming traffic is prioritized to minimize latency and packet loss for uninterrupted gameplay."""
    _run("QoS_03")


@pytest.mark.QoS_04
@pytest.mark.Functional
def test_qos_04():
    """Validate that QoS policies enforce correct prioritization hierarchy across traffic classes."""
    _run("QoS_04")


@pytest.mark.QoS_05
@pytest.mark.Functional
def test_qos_05():
    """Confirm bandwidth caps are applied to low‑priority flows to prevent congestion."""
    _run("QoS_05")


@pytest.mark.QoS_06
@pytest.mark.Functional
def test_qos_06():
    """Verify that critical applications receive guaranteed performance under QoS enforcement."""
    _run("QoS_06")


@pytest.mark.QoS_07
@pytest.mark.Functional
def test_qos_07():
    """Establish baseline performance without QoS to compare improvements when enabled."""
    _run("QoS_07")


@pytest.mark.QoS_08
@pytest.mark.Functional
def test_qos_08():
    """Validate DSCP‑based classification accuracy and enforcement across the network."""
    _run("QoS_08")


@pytest.mark.QoS_09
@pytest.mark.Functional
def test_qos_09():
    """Verify simultaneous prioritization of voice and data traffic without performance loss."""
    _run("QoS_09")


@pytest.mark.QoS_10
@pytest.mark.Functional
def test_qos_10():
    """Ensure rate limits are consistently applied to prevent bandwidth abuse."""
    _run("QoS_10")


@pytest.mark.QoS_11
@pytest.mark.Functional
def test_qos_11():
    """Confirm scheduling ensures high‑priority traffic is forwarded before lower classes."""
    _run("QoS_11")


@pytest.mark.QoS_12
@pytest.mark.Functional
def test_qos_12():
    """Verify QoS enforcement persists across Customer Premises Equipment under stress."""
    _run("QoS_12")


@pytest.mark.QoS_13
@pytest.mark.Functional
def test_qos_13():
    """Confirm QoS policies enforce correct allow/deny behavior."""
    _run("QoS_13")


@pytest.mark.QoS_14
@pytest.mark.Functional
def test_qos_14():
    """Ensure QoS differentiates real‑time vs. non‑real‑time traffic correctly."""
    _run("QoS_14")


@pytest.mark.QoS_15
@pytest.mark.Functional
def test_qos_15():
    """Validate logging captures QoS actions for audit and troubleshooting."""
    _run("QoS_15")


@pytest.mark.QoS_16
@pytest.mark.Functional
def test_qos_16():
    """Confirm dynamic QoS adjustments apply seamlessly without impacting service."""
    _run("QoS_16")


@pytest.mark.QoS_17
@pytest.mark.Functional
def test_qos_17():
    """Verify QoS handles encrypted traffic streams per configured rules."""
    _run("QoS_17")


@pytest.mark.QoS_18
@pytest.mark.Functional
def test_qos_18():
    """Ensure peer‑to‑peer traffic is controlled to prevent congestion."""
    _run("QoS_18")


@pytest.mark.QoS_19
@pytest.mark.Functional
def test_qos_19():
    """Validate resilience of QoS prioritization under heavy load."""
    _run("QoS_19")


@pytest.mark.QoS_20
@pytest.mark.Functional
def test_qos_20():
    """Confirm backup flows are deprioritized to preserve critical services."""
    _run("QoS_20")


@pytest.mark.QoS_21
@pytest.mark.Negative
def test_qos_21(request):
    """Verify QoS configuration retention across device reboots."""
    _run("QoS_21", request)


@pytest.mark.QoS_22
@pytest.mark.Functional
def test_qos_22():
    """Pass by default: wired TRex/PC→BTS + wireless BTS→CPE already covered by the lab suite."""
    _run("QoS_22")


@pytest.mark.QoS_23
@pytest.mark.Functional
def test_qos_23():
    """Verify reserved bandwidth is honored and not exceeded."""
    _run("QoS_23")


@pytest.mark.QoS_24
@pytest.mark.Stress
def test_qos_24():
    """Validate burst traffic is controlled to maintain stable throughput."""
    _run("QoS_24")


@pytest.mark.QoS_25
@pytest.mark.Functional
def test_qos_25():
    """Ensure QoS policies scale effectively under different load conditions."""
    _run("QoS_25")


@pytest.mark.QoS_26
@pytest.mark.Functional
def test_qos_26():
    """Validate mission‑critical apps are prioritized consistently."""
    _run("QoS_26")


@pytest.mark.QoS_27
@pytest.mark.Validation
def test_qos_27():
    """Confirm monitoring dashboards reflect real‑time QoS enforcement.

    Pass only: GUI dashboard shows sua queue_stats (same source already validated).
    """
    _run("QoS_27")


@pytest.mark.QoS_28
@pytest.mark.Negative
def test_qos_28():
    """To be tested manually: QoS under fluctuating wireless signal quality."""
    _run("QoS_28")


@pytest.mark.QoS_29
@pytest.mark.Functional
def test_qos_29():
    """Ensure user group‑based QoS policies are enforced correctly.

    Pass only: QoS is not user-group specific on this platform (SFC/DSCP Profile1).
    """
    _run("QoS_29")


@pytest.mark.QoS_30
@pytest.mark.Functional
def test_qos_30():
    """Confirm QoS handles encapsulated (QinQ + DSCP) traffic streams per policy."""
    _run("QoS_30")


@pytest.mark.QoS_31
@pytest.mark.Functional
def test_qos_31():
    """To be tested manually: dynamic bandwidth adjustment under QoS."""
    _run("QoS_31")


@pytest.mark.QoS_32
@pytest.mark.Functional
def test_qos_32():
    """Ensure classification and enforcement match configured QoS rules."""
    _run("QoS_32")


@pytest.mark.QoS_33
@pytest.mark.Functional
def test_qos_33():
    """To be tested manually: multi-device QoS enforcement consistency."""
    _run("QoS_33")


@pytest.mark.QoS_34
@pytest.mark.Functional
def test_qos_34():
    """Confirm policy‑based prioritization accuracy."""
    _run("QoS_34")


@pytest.mark.QoS_35
@pytest.mark.Functional
def test_qos_35():
    """To be tested manually: QoS stability across firmware upgrades."""
    _run("QoS_35")


@pytest.mark.QoS_36
@pytest.mark.Negative
def test_qos_36():
    """Ensure alerts are generated for violations to support monitoring.

    Not available: QoS violation alerts are not yet implemented on the platform.
    """
    _run("QoS_36")


@pytest.mark.QoS_37
@pytest.mark.Functional
def test_qos_37():
    """Validate QoS effectiveness across different packet sizes."""
    _run("QoS_37")


@pytest.mark.QoS_38
@pytest.mark.Functional
def test_qos_38():
    """Confirm AR/VR and new app traffic is prioritized correctly."""
    _run("QoS_38")


@pytest.mark.QoS_39
@pytest.mark.Functional
def test_qos_39():
    """Verify QoS enforcement consistency across multiple interfaces.

    Pass only: only one QoS-relevant interface is available in this lab setup.
    """
    _run("QoS_39")


@pytest.mark.QoS_40
@pytest.mark.Stress
def test_qos_40():
    """Validate QoS performance metrics are consistent under high load."""
    _run("QoS_40")
