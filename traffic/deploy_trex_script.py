"""Deploy the bundled TRex throughput client script to a remote TRex host."""

from __future__ import annotations

import argparse

from config.defaults import TRAFFIC_DEFAULTS
from traffic.trex_runner import bundled_client_script_path, deploy_trex_client_script


def main() -> None:
    trex_defaults = TRAFFIC_DEFAULTS["trex"]
    parser = argparse.ArgumentParser(description="Deploy TRex throughput client script to remote host")
    parser.add_argument("--trex-server", default=trex_defaults["host"])
    parser.add_argument("--trex-user", default=trex_defaults["user"])
    parser.add_argument("--trex-password", default=trex_defaults["password"])
    parser.add_argument(
        "--local-script",
        default=bundled_client_script_path(),
        help="Local script path (defaults to traffic/scripts/master_script_extended_16SU.py)",
    )
    parser.add_argument(
        "--remote-script",
        default=trex_defaults["client_script"],
        help="Remote destination path (defaults to ~/master_script_extended_16SU.py)",
    )
    args = parser.parse_args()

    remote_path = deploy_trex_client_script(
        trex_server=args.trex_server,
        trex_user=args.trex_user,
        trex_password=args.trex_password,
        local_script=args.local_script,
        remote_script=args.remote_script,
    )
    print(f"Deployed TRex client script to {args.trex_server}:{remote_path}")


if __name__ == "__main__":
    main()
