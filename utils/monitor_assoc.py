"""BTS-side helpers: resolve radio association index for a linked peer IP."""

from __future__ import annotations

from pages.commands import RootCommands
from utils.net_utils import ips_equal, normalize_ip
from utils.parsers import clean_ssh_output, ssh_scalar


async def find_assoc_index_for_cpe(root_ssh, cpe_ip: str, radio_idx: int = 1) -> int:
    """Return sua/sub index on BTS for the CPE matching link or management IP."""
    target = normalize_ip(cpe_ip)

    async def _ssh(command: str) -> str:
        response = await root_ssh.send_command(command)
        return clean_ssh_output(response.result)

    for idx in range(1, 33):
        assoc = ssh_scalar(await _ssh(RootCommands.get_link_stat_associd(radio_idx, idx)))
        if assoc in {"", "0"}:
            continue
        ipv4 = ssh_scalar(await _ssh(RootCommands.get_link_stat_field(radio_idx, idx, "ip")))
        ipv6 = ssh_scalar(await _ssh(RootCommands.get_link_stat_field(radio_idx, idx, "ipv6")))
        if ips_equal(target, ipv4) or ips_equal(target, ipv6) or target in ipv4 or target in ipv6:
            return idx
    return 1
