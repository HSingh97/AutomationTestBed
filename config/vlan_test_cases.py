"""VLAN catalog synced to ``Senao UBR P2MP Test Plan_Aug26_Alpha_2.xlsx`` sheet ``VLAN``.

Regenerate: ``PYTHONPATH=. python3 scripts/sync_vlan_catalog.py``

``status``:
  - implemented — TRex / ping automation (QinQ, transparent, trunk-related data path)
  - manual — needs switch, GUI, or physical link ops
  - not_applicable — N/A for A60/A61 alpha (mgmt/access VLAN modes outside scope)
  - pending — not classified yet

Alpha scope: QinQ + transparent + trunk data path only; mgmt/MVLAN config cases marked N/A.
"""

from __future__ import annotations

from typing import Any

VLAN_TEST_CASES: list[dict[str, Any]] = [
    {
        "id": "VLAN_01",
        "title": "VLAN Enable/Disable",
        "steps": "Verify whether enable/disable VLAN feature is working fine.",
        "expected": "VLAN should be enabled/disabled based on the option selected.",
        "dut": "BTS & CPE",
        "type": "Functional",
        "plan_result": "Not Available",
        "status": "not_applicable",
        "mode": None,
        "note": "Not applicable for A60/A61 alpha \u2014 outside QinQ/transparent/trunk VLAN scope."
    },
    {
        "id": "VLAN_02",
        "title": "Verify NMS traffic VLAN assignment",
        "steps": "NMS VLAN configured (e.g., VLAN 100)\nSend NMS traffic from BTS to NMS server",
        "expected": "Traffic tagged with VLAN 100 only;\nreaches NMS server",
        "dut": "BTS",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "not_applicable",
        "mode": None,
        "note": "Not applicable for A60/A61 alpha \u2014 outside QinQ/transparent/trunk VLAN scope."
    },
    {
        "id": "VLAN_03",
        "title": "Verify data traffic double-tagging",
        "steps": "Outer VLAN (e.g., 200), Inner VLAN (e.g., 10) configured\nSend user data traffic from CPE through BTS",
        "expected": "Frames carry double tags (200/10)",
        "dut": "BTS",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "implemented",
        "mode": "qinq_throughput",
        "note": "TRex QinQ double-tag; verify end-to-end throughput."
    },
    {
        "id": "VLAN_04",
        "title": "Negative test \u2013 NMS traffic with wrong VLAN",
        "steps": "Misconfigure NMS VLAN on BTS\nSend NMS traffic",
        "expected": "Traffic dropped; no delivery to NMS server",
        "dut": "BTS & CPE",
        "type": "Negative",
        "plan_result": "Pass",
        "status": "not_applicable",
        "mode": None,
        "note": "Not applicable for A60/A61 alpha \u2014 outside QinQ/transparent/trunk VLAN scope."
    },
    {
        "id": "VLAN_05",
        "title": "Negative test \u2013 Data traffic missing inner VLAN",
        "steps": "Configure only outer VLAN\nSend user traffic",
        "expected": "Traffic dropped or rejected; no double-tagging",
        "dut": "BTS",
        "type": "Negative",
        "plan_result": "Pass",
        "status": "implemented",
        "mode": "qinq_negative_outer_only",
        "note": "TRex outer-tag only; traffic must not pass (missing inner VLAN)."
    },
    {
        "id": "VLAN_06",
        "title": "Verify VLAN separation between NMS and data",
        "steps": "Configure NMS VLAN 100, Data VLAN 200/10\nSend both traffic types simultaneously",
        "expected": "NMS traffic isolated; data traffic double-tagged; no cross-leak",
        "dut": "BTS & CPE",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "not_applicable",
        "mode": None,
        "note": "Not applicable for A60/A61 alpha \u2014 outside QinQ/transparent/trunk VLAN scope."
    },
    {
        "id": "VLAN_07",
        "title": "Verify QoS prioritization for NMS VLAN",
        "steps": "QoS rules applied to VLAN 100\nGenerate high load traffic",
        "expected": "NMS traffic prioritized; no packet loss",
        "dut": "BTS & CPE",
        "type": "Functional",
        "plan_result": "Not Available",
        "status": "not_applicable",
        "mode": None,
        "note": "Not applicable for A60/A61 alpha \u2014 outside QinQ/transparent/trunk VLAN scope."
    },
    {
        "id": "VLAN_08",
        "title": "Verify VLAN tagging at ingress",
        "steps": "BTS configured for VLAN tagging\nCapture ingress packets",
        "expected": "NMS packets tagged VLAN 100;\ndata packets double-tagged",
        "dut": "BTS & CPE",
        "type": "Functional",
        "plan_result": "Not Available",
        "status": "implemented",
        "mode": "qinq_data_tag_verify",
        "note": "Data-path only (NMS skipped): QinQ UCI svlan/cvlan + TRex double-tag throughput."
    },
    {
        "id": "VLAN_09",
        "title": "Verify VLAN tagging at egress",
        "steps": "BTS configured for VLAN tagging\nCapture egress packets towards core",
        "expected": "NMS packets VLAN 100;\ndata packets double-tagged correctly",
        "dut": "BTS & CPE",
        "type": "Functional",
        "plan_result": "Not Available",
        "status": "implemented",
        "mode": "qinq_data_tag_verify",
        "note": "Data-path only (NMS skipped): QinQ UCI svlan/cvlan + TRex double-tag throughput."
    },
    {
        "id": "VLAN_10",
        "title": "Verify interoperability with switch",
        "steps": "Switch configured for VLAN trunk\nForward traffic through switch",
        "expected": "NMS VLAN recognized;\ndouble-tagged data traffic passed correctly",
        "dut": "BTS & CPE",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "manual",
        "mode": None,
        "note": "To be tested manually: requires managed switch configured as VLAN trunk."
    },
    {
        "id": "VLAN_11",
        "title": "Verify management isolation",
        "steps": "Attempt to access NMS server via data VLAN\nSend traffic from CPE to NMS IP",
        "expected": "Access denied;\nNMS reachable only via VLAN 100",
        "dut": "BTS & CPE",
        "type": "Functional",
        "plan_result": "Not Available",
        "status": "not_applicable",
        "mode": None,
        "note": "Not applicable for A60/A61 alpha \u2014 outside QinQ/transparent/trunk VLAN scope."
    },
    {
        "id": "VLAN_12",
        "title": "Test case to verify same VLAN configuration between two interface.",
        "steps": "1.Configure VLAN 10 in laptop/end_device \n2.Configure VLAN 10 in DUT. \n3.Configure same netmask in both end.\n4.Do ping test between both the interface.\n5.Reboot the board and do ping test again.",
        "expected": "1.Both interface should ping while configure same VLAN.\n2.Both interface should ping even after reboot the board/DUT.",
        "dut": "BTS & CPE",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "not_applicable",
        "mode": None,
        "note": "Not applicable for A60/A61 alpha \u2014 outside QinQ/transparent/trunk VLAN scope."
    },
    {
        "id": "VLAN_13",
        "title": "Test case to verify Different VLAN configuration between two interface.",
        "steps": "1.Configure VLAN 10 in laptop/end_device.\n2.Configure VLAN 20 in DUT. \n3.Configure same netmask in both end.\n4.Do ping test between both the interface.\n5.Reboot the board and do ping test again.",
        "expected": "1.Both interface should not ping while configure different VLAN.\n2.Both interface should not ping even after reboot the board/DUT.",
        "dut": "BTS & CPE",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "not_applicable",
        "mode": None,
        "note": "Not applicable for A60/A61 alpha \u2014 outside QinQ/transparent/trunk VLAN scope."
    },
    {
        "id": "VLAN_14",
        "title": "Test case to verify VLAN configurable range",
        "steps": "1.Try to configure VLAN within the range (1-4094).\n2.Try to configure VLAN out of the range lesser than 1, greater than 4094, excluded range (1002 - 1005), decimal value and alphapets",
        "expected": "Able to configure VLAN only within the range 1-4094.\nNot able to configure VLAN out of the range, excluded address, decimal value and alphapets.",
        "dut": "BTS & CPE",
        "type": "Functional",
        "plan_result": "Fail",
        "status": "implemented",
        "mode": "vlan_id_range_check",
        "note": "ucidyn set mgmtvlan accept 1-4094; reject <1, >4094, 1002-1005, decimals, alphabets."
    },
    {
        "id": "VLAN_15",
        "title": "Test case to verify Different VLAN between DUT and DHCP server",
        "steps": "1.Configure DHCP server and Keep server interface in default VLAN 10.\n2.Configure default VLAN 10 DUT interface.\n3.Check whether DUT able to get Ip address from DHCP server.",
        "expected": "DUT not able to get IP address from DCHP server.",
        "dut": "BTS & CPE",
        "type": "Negative",
        "plan_result": "Not Applicable",
        "status": "not_applicable",
        "mode": None,
        "note": "Not applicable for A60/A61 alpha \u2014 outside QinQ/transparent/trunk VLAN scope."
    },
    {
        "id": "VLAN_16",
        "title": "Test case to verify Different VLAN between DUT and DHCP server",
        "steps": "1.Configure DHCP server and Keep server interface in default VLAN 10.\n2.Configure default VLAN 20 DUT interface.\n3.Check whether DUT able to get Ip address from DHCP server.",
        "expected": "DUT not able to get IP address from DCHP server.",
        "dut": "BTS & CPE",
        "type": "Negative",
        "plan_result": "Not Applicable",
        "status": "not_applicable",
        "mode": None,
        "note": "Not applicable for A60/A61 alpha \u2014 outside QinQ/transparent/trunk VLAN scope."
    },
    {
        "id": "VLAN_17",
        "title": "Data VLAN Enable/Disable",
        "steps": "Verify whether enable/disable Data VLAN feature is working fine.",
        "expected": "Data VLAN should be enabled/disabled based on the option selected",
        "dut": "BTS",
        "type": "Functional",
        "plan_result": "Not Available",
        "status": "not_applicable",
        "mode": None,
        "note": "Not applicable for A60/A61 alpha \u2014 outside QinQ/transparent/trunk VLAN scope."
    },
    {
        "id": "VLAN_18",
        "title": "Test case to verify Data VLAN configurable range",
        "steps": "1. Try to configure data VLAN within the range (2-4094).\n2. Try to configure data VLAN out of the range lesser than 2, greater than 4094, excluded range (1002 - 1005), decimal value and alphabets",
        "expected": "Able to configure VLAN only within the range 2-4094.\nshould not able to configure VLAN out of the range, excluded address, decimal value and alphapets.",
        "dut": "BTS",
        "type": "Functional",
        "plan_result": "Fail",
        "status": "not_applicable",
        "mode": None,
        "note": "Not applicable for A60/A61 alpha \u2014 outside QinQ/transparent/trunk VLAN scope."
    },
    {
        "id": "VLAN_19",
        "title": "Test case to verify the traffic.",
        "steps": "1. Verify by sending data from Tester/iperf with same VLAN tag as of data VLAN. Traffic should Pass\n2. Verify by sending data from Tester/iperf with different VLAN tag as of data VLAN. Traffic should not Pass",
        "expected": "1. With same VLAN tag as of data VLAN, traffic should Pass\n2. With different VLAN tag as of data VLAN, traffic should not Pass",
        "dut": "BTS",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "implemented",
        "mode": "tagged_data_pass_fail",
        "note": "QinQ same svlan/cvlan must pass; wrong tags must not pass (TRex)."
    },
    {
        "id": "VLAN_20",
        "title": "Transparent bridge mode",
        "steps": "CE to PE ping response without traffic using https requests of each tabs of CPE from BTS side and initiate normal ping from CE vlan ip to PE vlan ip",
        "expected": "No drops in end to end vlan response",
        "dut": "CPE",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "implemented",
        "mode": "transparent_ping_idle",
        "note": "Transparent bridge ping ce to pe without background traffic."
    },
    {
        "id": "VLAN_21",
        "title": "Transparent bridge mode",
        "steps": "CE to PE ping response with traffic using https requests of each tabs of CPE from BTS side and initiate normal ping from CE vlan ip to PE vlan ip",
        "expected": "No drops in end to end vlan response",
        "dut": "CPE",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "implemented",
        "mode": "transparent_ping_under_load",
        "note": "Transparent bridge ping ce to pe with TRex background load."
    },
    {
        "id": "VLAN_22",
        "title": "Transparent bridge mode",
        "steps": "PE to CE ping response without traffic using https requests of each tabs of CPE from BTS side and initiate normal ping from CE vlan ip to PE vlan ip",
        "expected": "No drops in end to end vlan response",
        "dut": "CPE",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "implemented",
        "mode": "transparent_ping_idle",
        "note": "Transparent bridge ping pe to ce without background traffic."
    },
    {
        "id": "VLAN_23",
        "title": "Transparent bridge mode",
        "steps": "PE to CE ping response with traffic using https requests of each tabs of CPE from BTS side and initiate normal ping from CE vlan ip to PE vlan ip",
        "expected": "No drops in end to end vlan response",
        "dut": "CPE",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "implemented",
        "mode": "transparent_ping_under_load",
        "note": "Transparent bridge ping pe to ce with TRex background load."
    },
    {
        "id": "VLAN_24",
        "title": "Transparent bridge mode",
        "steps": "Ethernet change in traffic loaded condition and checking end to end response ( auto and 100 full duplex mode)",
        "expected": "Fine. Ethernet driver is not going to hung mode",
        "dut": "CPE",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "implemented",
        "mode": "transparent_eth_flap",
        "note": "Ethernet speed/duplex + carrier flap under transparent VLAN load via CPE jump SSH."
    },
    {
        "id": "VLAN_25",
        "title": "Transparent bridge mode",
        "steps": "BTS reboot in traffic loaded condition and check end to end vlan reachability is fine after reboot.",
        "expected": "End to end reachability is fine",
        "dut": "CPE",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "implemented",
        "mode": "destructive_bts_reboot",
        "note": "BTS reboot under VLAN load \u2014 requires --allow-vlan-destructive."
    },
    {
        "id": "VLAN_26",
        "title": "Transparent bridge mode",
        "steps": "BTS wireless parameter changes including ip setting and check end to end vlan reachability is fine after reboot.",
        "expected": "Fine No crash in wireless driver",
        "dut": "CPE",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "implemented",
        "mode": "destructive_bts_reboot",
        "note": "BTS reboot under VLAN load \u2014 requires --allow-vlan-destructive."
    },
    {
        "id": "VLAN_27",
        "title": "Transparent bridge mode",
        "steps": "CPE reboot in traffic loaded condition and check end to end vlan reachability is fine after reboot.",
        "expected": "End to end reachability is fine",
        "dut": "CPE",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "implemented",
        "mode": "destructive_cpe_reboot",
        "note": "CPE reboot under VLAN load \u2014 requires --allow-vlan-destructive."
    },
    {
        "id": "VLAN_28",
        "title": "Transparent bridge mode",
        "steps": "CPE wireless parameter changes including ip setting and check end to end vlan reachability is fine after reboot.",
        "expected": "Fine No crash in wireless driver",
        "dut": "CPE",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "implemented",
        "mode": "destructive_cpe_reboot",
        "note": "CPE reboot under VLAN load \u2014 requires --allow-vlan-destructive."
    },
    {
        "id": "VLAN_29",
        "title": "Transparent bridge mode",
        "steps": "Basic reachability of CPE via default ethernet ip (169.254.254.1) and management ip.",
        "expected": "Locally reachable via default ethernet ip.",
        "dut": "CPE",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "implemented",
        "mode": "cpe_mgmt_gui_reachability",
        "note": "CPE mgmt HTTP/ping reachability via CPE-side PC (GUI path)."
    },
    {
        "id": "VLAN_30",
        "title": "Transparent bridge mode",
        "steps": "change vlan modes in CPE from transparent to trunk/QnQ/Mangemnets and viceversa and crossverify gateway route is not going missing from route -n command in CPE",
        "expected": "No missing in gateway route of CPE",
        "dut": "CPE",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "implemented",
        "mode": "cpe_vlan_mode_cycle",
        "note": "CPE VLAN mode cycle transparent/trunk/QinQ via jump SSH (GUI-equivalent UCI)."
    },
    {
        "id": "VLAN_31",
        "title": "Transparent bridge mode",
        "steps": "Ethernet down / up. Make the  ethernet of CPE down / up  and check whether the end to end reachability is fine or not.",
        "expected": "Fine. Ethernet driver is not going to hung mode",
        "dut": "CPE",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "implemented",
        "mode": "transparent_eth_flap",
        "note": "CPE ethernet down/up under transparent VLAN load via jump SSH."
    },
    {
        "id": "VLAN_32",
        "title": "Transparent VLAN with MVLAN Disable (Default 1) in BTS Transparent VLAN & MVLAN Disable (Default 1) in CPE",
        "steps": "CE to PE ping response without traffic using https requests of each tabs of CPE from BTS side and initiate  normal ping from CE vlan ip to PE vlan ip",
        "expected": "No drops in end to end vlan response",
        "dut": "BTS & CPE",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "implemented",
        "mode": "transparent_throughput",
        "note": "BTS transparent + MVLAN disable; TRex untagged path throughput."
    },
    {
        "id": "VLAN_33",
        "title": "Transparent VLAN with MVLAN Disable (Default 1) in BTS Transparent VLAN & MVLAN Disable (Default 1) in CPE",
        "steps": "CE to PE ping response with traffic using https requests of each tabs of CPE from BTS side and initiate  normal ping from CE vlan ip to PE vlan ip",
        "expected": "No drops in end to end vlan response",
        "dut": "BTS & CPE",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "implemented",
        "mode": "transparent_throughput",
        "note": "BTS transparent + MVLAN disable; TRex untagged path throughput."
    },
    {
        "id": "VLAN_34",
        "title": "Transparent VLAN with MVLAN Disable (Default 1) in BTS Transparent VLAN & MVLAN Disable (Default 1) in CPE",
        "steps": "PE to CE ping response without traffic using https requests of each tabs of CPE from BTS side and initiate  normal ping from CE vlan ip to PE vlan ip",
        "expected": "No drops in end to end vlan response",
        "dut": "BTS & CPE",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "implemented",
        "mode": "transparent_throughput",
        "note": "BTS transparent + MVLAN disable; TRex untagged path throughput."
    },
    {
        "id": "VLAN_35",
        "title": "Transparent VLAN with MVLAN Disable (Default 1) in BTS Transparent VLAN & MVLAN Disable (Default 1) in CPE",
        "steps": "PE to CE ping response with traffic using https requests of each tabs of CPE from BTS side and initiate  normal ping from CE vlan ip to PE vlan ip",
        "expected": "No drops in end to end vlan response",
        "dut": "BTS & CPE",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "implemented",
        "mode": "transparent_throughput",
        "note": "BTS transparent + MVLAN disable; TRex untagged path throughput."
    },
    {
        "id": "VLAN_36",
        "title": "Transparent VLAN with MVLAN Disable (Default 1) in BTS Transparent VLAN & MVLAN Disable (Default 1) in CPE",
        "steps": "Ethernet change in traffic loaded condition and checking end to end response ( auto and 100 full duplex mode)",
        "expected": "Fine. Ethernet driver is not going to hung mode",
        "dut": "BTS & CPE",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "implemented",
        "mode": "transparent_throughput",
        "note": "BTS transparent + MVLAN disable; TRex untagged path throughput."
    },
    {
        "id": "VLAN_37",
        "title": "Transparent VLAN with MVLAN Disable (Default 1) in BTS Transparent VLAN & MVLAN Disable (Default 1) in CPE",
        "steps": "BTS reboot in traffic loaded condition and check end to end vlan reachability is fine after reboot.",
        "expected": "End to end reachability is fine",
        "dut": "BTS & CPE",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "implemented",
        "mode": "transparent_throughput",
        "note": "BTS transparent + MVLAN disable; TRex untagged path throughput."
    },
    {
        "id": "VLAN_38",
        "title": "Transparent VLAN with MVLAN Disable (Default 1) in BTS Transparent VLAN & MVLAN Disable (Default 1) in CPE",
        "steps": "BTS wireless parameter changes including ip setting and check end to end vlan reachability is fine after reboot.",
        "expected": "Fine No crash in wireless driver",
        "dut": "BTS & CPE",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "implemented",
        "mode": "transparent_throughput",
        "note": "BTS transparent + MVLAN disable; TRex untagged path throughput."
    },
    {
        "id": "VLAN_39",
        "title": "Transparent VLAN with MVLAN Disable (Default 1) in BTS Transparent VLAN & MVLAN Disable (Default 1) in CPE",
        "steps": "CPE  reboot in traffic loaded condition and check end to end vlan reachability is fine after reboot.",
        "expected": "End to end reachability is fine",
        "dut": "BTS & CPE",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "implemented",
        "mode": "transparent_throughput",
        "note": "BTS transparent + MVLAN disable; TRex untagged path throughput."
    },
    {
        "id": "VLAN_40",
        "title": "Transparent VLAN with MVLAN Disable (Default 1) in BTS Transparent VLAN & MVLAN Disable (Default 1) in CPE",
        "steps": "CPE wireless parameter changes including ip setting and check end to end vlan reachability is fine after reboot.",
        "expected": "Fine No crash in wireless driver",
        "dut": "BTS & CPE",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "implemented",
        "mode": "transparent_throughput",
        "note": "BTS transparent + MVLAN disable; TRex untagged path throughput."
    },
    {
        "id": "VLAN_41",
        "title": "Transparent VLAN with MVLAN Disable (Default 1) in BTS Transparent VLAN & MVLAN Disable (Default 1) in CPE",
        "steps": "Basic reachability of CPE via default ethernet ip (169.254.254.1).",
        "expected": "Locally reachable via default ethernet ip.",
        "dut": "BTS & CPE",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "implemented",
        "mode": "transparent_throughput",
        "note": "BTS transparent + MVLAN disable; TRex untagged path throughput."
    },
    {
        "id": "VLAN_42",
        "title": "Transparent VLAN with MVLAN Disable (Default 1) in BTS Transparent VLAN & MVLAN Disable (Default 1) in CPE",
        "steps": "change vlan modes in CPE from transparent to trunk/QnQ/Mangemnets and viceversa and crossverify gateway route is not going missing from route -n command in CPE",
        "expected": "No missing in gateway route of CPE",
        "dut": "BTS & CPE",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "implemented",
        "mode": "transparent_throughput",
        "note": "BTS transparent + MVLAN disable; TRex untagged path throughput."
    },
    {
        "id": "VLAN_43",
        "title": "Transparent VLAN with MVLAN Disable (Default 1) in BTS Transparent VLAN & MVLAN Disable (Default 1) in CPE",
        "steps": "Ethernet down / up. Make the  ethernet of CPE down / up  and check whether the end to end reachability is fine or not.",
        "expected": "Fine. Ethernet driver is not going to hung mode",
        "dut": "BTS & CPE",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "implemented",
        "mode": "transparent_throughput",
        "note": "BTS transparent + MVLAN disable; TRex untagged path throughput."
    },
    {
        "id": "VLAN_44",
        "title": "Transparent VLAN with MVLAN Disable (Default 1) in BTS Transparent VLAN & MVLAN Disable (Default 1) in CPE",
        "steps": "Configure end to end vlan other than mentioned in the list and ensure only vlan traffic mentioned in the list is only passing.",
        "expected": "Only traffic of vlans mentioned in the List is passing",
        "dut": "BTS & CPE",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "implemented",
        "mode": "transparent_throughput",
        "note": "BTS transparent + MVLAN disable; TRex untagged path throughput."
    },
    {
        "id": "VLAN_45",
        "title": "QnQ VLAN with MVLAN Enable in BTS Transparent VLAN & MVLAN Enable in CPE",
        "steps": "Basic management reachability of BTS with management vlan configured in router",
        "expected": "Management reachability to BTS is fine.",
        "dut": "BTS",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "implemented",
        "mode": "qinq_hybrid_throughput",
        "note": "BTS QinQ + CPE transparent with MVLAN enable; TRex QinQ throughput."
    },
    {
        "id": "VLAN_46",
        "title": "QnQ VLAN with MVLAN Enable in BTS Transparent VLAN & MVLAN Enable in CPE",
        "steps": "CE to PE ping response without traffic using https requests of each tabs of CPE from BTS side and initiate normal ping from CE vlan ip to PE vlan ip",
        "expected": "No drops in end to end vlan response",
        "dut": "BTS",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "implemented",
        "mode": "qinq_hybrid_throughput",
        "note": "BTS QinQ + CPE transparent with MVLAN enable; TRex QinQ throughput."
    },
    {
        "id": "VLAN_47",
        "title": "QnQ VLAN with MVLAN Enable in BTS Transparent VLAN & MVLAN Enable in CPE",
        "steps": "CE to PE ping response with traffic using https requests of each tabs of CPE from BTS side and initiate normal ping from CE vlan ip to PE vlan ip",
        "expected": "No drops in end to end vlan response",
        "dut": "BTS",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "implemented",
        "mode": "qinq_hybrid_throughput",
        "note": "BTS QinQ + CPE transparent with MVLAN enable; TRex QinQ throughput."
    },
    {
        "id": "VLAN_48",
        "title": "QnQ VLAN with MVLAN Enable in BTS Transparent VLAN & MVLAN Enable in CPE",
        "steps": "PE to CE ping response without traffic using https requests of each tabs of CPE from BTS side and initiate normal ping from CE vlan ip to PE vlan ip",
        "expected": "No drops in end to end vlan response",
        "dut": "BTS",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "implemented",
        "mode": "qinq_hybrid_throughput",
        "note": "BTS QinQ + CPE transparent with MVLAN enable; TRex QinQ throughput."
    },
    {
        "id": "VLAN_49",
        "title": "QnQ VLAN with MVLAN Enable in BTS Transparent VLAN & MVLAN Enable in CPE",
        "steps": "PE to CE ping response with traffic using https requests of each tabs of CPE from BTS side and initiate normal ping from CE vlan ip to PE vlan ip",
        "expected": "No drops in end to end vlan response",
        "dut": "BTS",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "implemented",
        "mode": "qinq_hybrid_throughput",
        "note": "BTS QinQ + CPE transparent with MVLAN enable; TRex QinQ throughput."
    },
    {
        "id": "VLAN_50",
        "title": "QnQ VLAN with MVLAN Enable in BTS Transparent VLAN & MVLAN Enable in CPE",
        "steps": "Ethernet change in traffic loaded condition and checking end to end response ( auto and 100 full duplex mode)",
        "expected": "Fine. Ethernet driver is not going to hung mode",
        "dut": "BTS",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "implemented",
        "mode": "qinq_hybrid_throughput",
        "note": "BTS QinQ + CPE transparent with MVLAN enable; TRex QinQ throughput."
    },
    {
        "id": "VLAN_51",
        "title": "QnQ VLAN with MVLAN Enable in BTS Transparent VLAN & MVLAN Enable in CPE",
        "steps": "BTS reboot in traffic loaded condition and check end to end vlan reachability is fine after reboot.",
        "expected": "End to end reachability is fine",
        "dut": "BTS",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "implemented",
        "mode": "qinq_hybrid_throughput",
        "note": "BTS QinQ + CPE transparent with MVLAN enable; TRex QinQ throughput."
    },
    {
        "id": "VLAN_52",
        "title": "QnQ VLAN with MVLAN Enable in BTS Transparent VLAN & MVLAN Enable in CPE",
        "steps": "BTS wireless parameter changes including ip setting and check end to end vlan reachability is fine after reboot.",
        "expected": "Fine No crash in wireless driver",
        "dut": "BTS",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "implemented",
        "mode": "qinq_hybrid_throughput",
        "note": "BTS QinQ + CPE transparent with MVLAN enable; TRex QinQ throughput."
    },
    {
        "id": "VLAN_53",
        "title": "QnQ VLAN with MVLAN Enable in BTS Transparent VLAN & MVLAN Enable in CPE",
        "steps": "CPE reboot in traffic loaded condition and check end to end vlan reachability is fine after reboot.",
        "expected": "End to end reachability is fine",
        "dut": "BTS",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "implemented",
        "mode": "qinq_hybrid_throughput",
        "note": "BTS QinQ + CPE transparent with MVLAN enable; TRex QinQ throughput."
    },
    {
        "id": "VLAN_54",
        "title": "QnQ VLAN with MVLAN Enable in BTS Transparent VLAN & MVLAN Enable in CPE",
        "steps": "CPE wireless parameter changes including ip setting and check end to end vlan reachability is fine after reboot.",
        "expected": "Fine No crash in wireless driver",
        "dut": "BTS",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "implemented",
        "mode": "qinq_hybrid_throughput",
        "note": "BTS QinQ + CPE transparent with MVLAN enable; TRex QinQ throughput."
    },
    {
        "id": "VLAN_55",
        "title": "QnQ VLAN with MVLAN Enable in BTS Transparent VLAN & MVLAN Enable in CPE",
        "steps": "Basic reachability of CPE via default ethernet ip (10.0.0.1).",
        "expected": "Locally reachable via default ethernet ip.",
        "dut": "BTS",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "implemented",
        "mode": "qinq_hybrid_throughput",
        "note": "BTS QinQ + CPE transparent with MVLAN enable; TRex QinQ throughput."
    },
    {
        "id": "VLAN_56",
        "title": "QnQ VLAN with MVLAN Enable in BTS Transparent VLAN & MVLAN Enable in CPE",
        "steps": "Change vlan modes in CPE from transparent to trunk/QnQ/Mangemnets and viceversa and crossverify gateway route is not going missing from route -n command in CPE",
        "expected": "No missing in gateway route of CPE",
        "dut": "BTS",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "implemented",
        "mode": "qinq_hybrid_throughput",
        "note": "BTS QinQ + CPE transparent with MVLAN enable; TRex QinQ throughput."
    },
    {
        "id": "VLAN_57",
        "title": "QnQ VLAN with MVLAN Enable in BTS Transparent VLAN & MVLAN Enable in CPE",
        "steps": "Ethernet down / up. Make the ethernet of CPE down / up and check whether the end to end reachability is fine or not.",
        "expected": "Fine. Ethernet driver is not going to hung mode",
        "dut": "BTS",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "implemented",
        "mode": "qinq_hybrid_throughput",
        "note": "BTS QinQ + CPE transparent with MVLAN enable; TRex QinQ throughput."
    },
    {
        "id": "VLAN_58",
        "title": "QnQ VLAN with MVLAN Enable in BTS Transparent VLAN & MVLAN Enable in CPE",
        "steps": "Configure end to end vlan other than mentioned in the list and ensure only vlan traffic mentioned in the list is only passing.",
        "expected": "Only traffic of vlans mentioned in the List is passing",
        "dut": "BTS",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "implemented",
        "mode": "qinq_hybrid_throughput",
        "note": "BTS QinQ + CPE transparent with MVLAN enable; TRex QinQ throughput."
    },
    {
        "id": "VLAN_59",
        "title": "QnQ VLAN with MVLAN Enable in BTS Transparent VLAN & MVLAN Enable in CPE",
        "steps": "Basic management reachability of BTS to CPE  from management vlan configured in router / BTS (BTS Backend)",
        "expected": "Management reachability to BTS & CPE should be fine.",
        "dut": "BTS",
        "type": "Functional",
        "plan_result": "Pass",
        "status": "implemented",
        "mode": "qinq_hybrid_throughput",
        "note": "BTS QinQ + CPE transparent with MVLAN enable; TRex QinQ throughput."
    }
]


def all_case_ids() -> list[str]:
    return [c["id"] for c in VLAN_TEST_CASES]


def case_by_id(case_id: str) -> dict[str, Any]:
    for case in VLAN_TEST_CASES:
        if case["id"] == case_id:
            return case
    raise KeyError(case_id)


def implemented_case_ids() -> list[str]:
    return [c["id"] for c in VLAN_TEST_CASES if c.get("status") == "implemented"]


def manual_case_ids() -> list[str]:
    return [c["id"] for c in VLAN_TEST_CASES if c.get("status") == "manual"]


def not_applicable_case_ids() -> list[str]:
    return [c["id"] for c in VLAN_TEST_CASES if c.get("status") == "not_applicable"]


def lab_case_ids() -> list[str]:
    """Cases that may hit live TRex/DUT when executed."""
    return [c["id"] for c in VLAN_TEST_CASES if c.get("status") == "implemented"]
