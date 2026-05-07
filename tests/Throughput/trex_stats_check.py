import argparse
import json

from utils.traffic.trex_runner import run_trex_stats_check


def main():
    parser = argparse.ArgumentParser(description="TRex stats-update sanity runner")
    parser.add_argument("--trex-server", default="127.0.0.1")
    parser.add_argument("--time", type=int, default=15)
    parser.add_argument("--expected-min-mbps", type=float, default=100.0)
    parser.add_argument("--output-json", default="trex_stats_check.json")
    args = parser.parse_args()
    result = run_trex_stats_check(
        trex_server=args.trex_server,
        duration_s=args.time,
        expected_min_mbps=args.expected_min_mbps,
        output_json=args.output_json,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

