import argparse
import json

from config.defaults import TRAFFIC_DEFAULTS
from traffic.trex_runner import run_trex_stats_check


def main():
    trex_defaults = TRAFFIC_DEFAULTS["trex"]
    parser = argparse.ArgumentParser(description="TRex stats-update sanity runner")
    parser.add_argument("--trex-server", default=trex_defaults["host"])
    parser.add_argument("--trex-user", default=trex_defaults["user"])
    parser.add_argument("--trex-password", default=trex_defaults["password"])
    parser.add_argument("--trex-dir", default=trex_defaults["directory"])
    parser.add_argument("--trex-pythonpath", default=trex_defaults["pythonpath"])
    parser.add_argument("--trex-client-script", default=trex_defaults["client_script"])
    parser.add_argument("--trex-ports", default=trex_defaults["ports"])
    parser.add_argument("--trex-server-su", default="")
    parser.add_argument("--trex-server-su2", default="")
    parser.add_argument("--trex-server-su3", default="")
    parser.add_argument("--trex-server-su4", default="")
    parser.add_argument("--trex-run-mode", choices=["max_throughput", "counter_check"], default="max_throughput")
    parser.add_argument("--trex-direction", choices=["bidi", "uplink", "downlink", "all"], default="bidi")
    parser.add_argument("--trex-proto", choices=["udp", "tcp", "both"], default="udp")
    parser.add_argument("--trex-dl-bw", default="400M")
    parser.add_argument("--trex-ul-bw", default="400M")
    parser.add_argument("--trex-subw", default="")
    parser.add_argument("--trex-vlan", type=int, default=-1)
    parser.add_argument("--trex-graph", action="store_true")
    parser.add_argument("--trex-packet-size", type=int, default=1500)
    parser.add_argument("--trex-su-count", type=int, default=1)
    parser.add_argument("--time", type=int, default=15)
    parser.add_argument("--expected-min-mbps", type=float, default=100.0)
    parser.add_argument("--output-json", default="trex_stats_check.json")
    args = parser.parse_args()
    result = run_trex_stats_check(
        trex_server=args.trex_server,
        trex_user=args.trex_user,
        trex_password=args.trex_password,
        trex_dir=args.trex_dir,
        trex_pythonpath=args.trex_pythonpath,
        trex_client_script=args.trex_client_script,
        trex_ports=args.trex_ports,
        trex_server_su=args.trex_server_su or None,
        trex_server_su2=args.trex_server_su2 or None,
        trex_server_su3=args.trex_server_su3 or None,
        trex_server_su4=args.trex_server_su4 or None,
        run_mode=args.trex_run_mode,
        trex_direction=args.trex_direction,
        trex_protocol=args.trex_proto,
        trex_dl_bw=args.trex_dl_bw,
        trex_ul_bw=args.trex_ul_bw,
        trex_subw=args.trex_subw or None,
        trex_vlan=args.trex_vlan if args.trex_vlan >= 0 else None,
        trex_enable_graph=args.trex_graph,
        trex_packet_size=args.trex_packet_size,
        trex_su_count=args.trex_su_count,
        duration_s=args.time,
        expected_min_mbps=args.expected_min_mbps,
        output_json=args.output_json,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
