from traffic.trex_runner import parse_trex_server_port_states


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
