from unittest.mock import patch

from traffic.su_link_ping import build_qinq_link_up_commands, wait_for_su_links


def test_build_qinq_link_up_commands():
    cmd = build_qinq_link_up_commands("enp3s0", 200, 201)
    assert "enp3s0.200" in cmd
    assert "enp3s0.200.201" in cmd
    assert "id 200" in cmd
    assert "id 201" in cmd


def test_wait_for_su_links_uses_bts_ping(monkeypatch):
    monkeypatch.setattr("traffic.su_link_ping._ensure_local_qinq_iface", lambda tb: None)
    calls: list[str] = []

    def fake_bts_ping(bts_ip, user, password, host, *, count=2):
        calls.append(host)
        return host.endswith(":331")

    with patch("traffic.su_link_ping._bts_ping_ok", side_effect=fake_bts_ping):
        result = wait_for_su_links(
            cpe_hosts=["2401:4900:d0:40d4::17b8:0:331"],
            profile_tb={},
            bts_ip="10.0.0.1",
            bts_user="root",
            bts_password="pw",
            timeout_s=1,
            poll_s=0.1,
        )

    assert result["ok"] is True
    assert result["method"] == "bts-ssh"
    assert calls
