from traffic.dut_radio_config import (
    _cpe_ssh_relay_command,
    _ip_matches,
    _parse_uci_get_output,
    _remote_exec_bts_command,
)


def test_cpe_ssh_relay_uses_unbracketed_ipv6_and_single_quoted_inner():
    cmd = _cpe_ssh_relay_command(
        "2401:4900:d0:40d4::17b8:0:331",
        "pass",
        "uci get txparam.ath1.ddrsrate",
    )
    assert "-6 " in cmd
    assert "root@2401:4900:d0:40d4::17b8:0:331" in cmd
    assert "[" not in cmd
    assert "'uci get txparam.ath1.ddrsrate'" in cmd


def test_remote_exec_bts_command_single_quotes_inner():
    cmd = _remote_exec_bts_command(2, "uci get txparam.ath1.ddrsrate")
    assert cmd == "/usr/sbin/remote_exec.sh 2 'uci get txparam.ath1.ddrsrate'"


def test_parse_uci_get_output_extracts_scalar_from_noise():
    raw = "noise\n23\n"
    assert _parse_uci_get_output(raw) == "23"


def test_ip_matches_ipv6_with_compression():
    assert _ip_matches(
        "2401:4900:d0:40d4::17b8:0:331",
        "2401:4900:d0:40d4:0:17b8:0:331",
    )
