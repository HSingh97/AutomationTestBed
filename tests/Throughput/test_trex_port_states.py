from traffic.trex_runner import parse_trex_server_port_states, ports_for_su_count


def test_parse_trex_server_port_states_from_interactive_table():
    sample = """
      ports |         0         |         1         |
      ----- | ----------------- | ----------------- |
            |     (link UP) 0   |     (link UP) 1   |
    """
    states = parse_trex_server_port_states(sample)
    assert states[0] == "UP"
    assert states[1] == "UP"


def test_parse_trex_server_port_states_marks_down_ports():
    sample = """
      ports |         0         |         1         |
            |   (link DOWN) 0   |     (link UP) 1   |
    """
    states = parse_trex_server_port_states(sample)
    assert states[0] == "DOWN"
    assert states[1] == "UP"


def test_parse_trex_server_port_states_from_startup_banner():
    sample = """
port : 0
------------
link         :  link : Link Up - speed 1000 Mbps - full-duplex
port : 1
------------
link         :  link : Link Up - speed 1000 Mbps - full-duplex
port : 2
------------
link         :  Link Down
"""
    states = parse_trex_server_port_states(sample)
    assert states[0] == "UP"
    assert states[1] == "UP"
    assert states[2] == "DOWN"


def test_ports_for_su_count_uses_bts_plus_one_port_per_su():
    assert ports_for_su_count("0,1,2,3", 1) == "0,1"
    assert ports_for_su_count("0,1,2,3", 2) == "0,1,2"
    assert ports_for_su_count("0,1,2,3", 3) == "0,1,2,3"
    assert ports_for_su_count("0,1,2,3", 6) == "0,1,2,3"
