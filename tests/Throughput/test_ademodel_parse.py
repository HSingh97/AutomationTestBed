from utils.regression_device_info import _parse_ademodel


def test_parse_ademodel_uses_scalar_output():
    assert _parse_ademodel("UBR630-C23\n") == "UBR630-C23"


def test_parse_ademodel_falls_back_to_token_extraction():
    assert _parse_ademodel("model: UBR630 extra noise") == "UBR630"
