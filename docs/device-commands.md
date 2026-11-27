# Device Linux / UCI / ucidyn command reference

Commands used across this AutomationTestBed (Sanity, Functional, IP, API, Regression, ARP/Bridge, Throughput, etc.), plus extras useful on OpenWrt/UBR devices.

## Quick rules

| Layer | Meaning |
|---|---|
| `uci get/set` | **Saved** config — persist with `uci commit <pkg>` |
| `ucidyn get/set` | **Live** path — almost always ends with `ucidyn apply` |
| Radio iface | Usually `ath1` / `ath{radio_idx}` |
| Bridge | Usually `br-lan` |

**Cheat sheet**

| Action | Pattern |
|---|---|
| Read saved | `uci get <pkg>.<section>.<option>` |
| Read quiet | `uci -q get …` (empty if missing) |
| Write saved | `uci set …` → `uci commit <pkg>` → often `/etc/init.d/<svc> reload` |
| Read live | `ucidyn get …` |
| Write live | `ucidyn set …` → **`ucidyn apply`** |
| Show package | `uci show wireless` / `network` / `vlan` / `txparam` |
| Delete key | `uci delete …` or `uci -q delete …` |

Primary code catalogs: `pages/commands.py` (`RootCommands`), `utils/sanity_commands.py`, `utils/vlan_uci.py`, `utils/link_formation.py`.

---

## A) System / identity

| Command | One-line work |
|---|---|
| `cat /etc/version` | Firmware / SW version |
| `cat /etc/ademodel` | Device model string |
| `cat /etc/hwver` | Hardware version |
| `cat /etc/blver` | Bootloader version |
| `fw_printenv -n dsn` | Serial number |
| `hostname` | Current hostname |
| `uci get system.@system[0].hostname` | Configured hostname |
| `uci -q get system.@system[0].cusname` | Location / customer name |
| `uci get system.@system[0].timezone` | Timezone |
| `date` / `date +%s` | Wall clock / epoch |
| `date -s @<epoch>` | Set time (NTP tests) |
| `cat /proc/uptime` | Uptime seconds |
| `tmp101` | Board temperature |
| `cat /tmp/cpu_usage` | CPU usage snapshot |
| `cat /tmp/mem_usage` | Memory usage snapshot |
| `ubus call service list` | Procd service status |
| `ubus call system watchdog '{"stop":true}'` | Stop watchdog (forces reboot path) |

**Also useful**

| Command | One-line work |
|---|---|
| `uci show system` | Dump all system settings |
| `free -m` | RAM overview |
| `df -h` | Flash / filesystem usage |
| `cat /proc/cpuinfo` | CPU details |
| `uname -a` | Kernel / arch |

---

## B) Network / IP (UCI + live)

### Read config

| Command | One-line work |
|---|---|
| `uci get network.lan.proto` | LAN mode (static/dhcp) |
| `uci get network.lan.ipaddr` | Configured IPv4 |
| `uci get network.lan.netmask` | Netmask |
| `uci get network.lan.gateway` | IPv4 gateway |
| `uci get network.lan.ip6addr` | Configured IPv6 CIDR |
| `uci get network.lan.ip6gw` | IPv6 gateway |
| `uci -q get network.lan.ip6proto` | IPv6 proto |
| `uci get fallback.lan.ipaddr` | Fallback IPv4 |
| `uci get dhcp.lan.ignore` | DHCP server off/on |
| `uci get network.lan24.ipaddr` | 2.4 GHz mgmt LAN IP |
| `uci get dhcp.lan24.ignore` | lan24 DHCP enable |

### Live (ucidyn / ip / ifconfig)

| Command | One-line work |
|---|---|
| `ucidyn get network.lan.ipaddr` | Runtime IPv4 |
| `ucidyn get network.lan.ip6addr` | Runtime IPv6 |
| `ucidyn get network.lan.gateway` | Runtime IPv4 GW |
| `ucidyn get network.lan.ip6gw` | Runtime IPv6 GW |
| `ifconfig br-lan` | Bridge IP/MAC/MTU |
| `ip -6 addr show dev br-lan` | Live IPv6 on bridge |
| `ip neigh show` | Neighbor/ARP table |
| `cat /proc/net/arp` | Classic ARP table |
| `ip -6 neigh show` | IPv6 neighbors |
| `ping -c 4 <host>` | IPv4 reachability |
| `ping -6 -c 4 <host>` / `ping6 …` | IPv6 reachability |
| `traceroute` / `traceroute6` | Path check |

### Set / apply

| Command | One-line work |
|---|---|
| `ucidyn set network.lan.proto static` | Set LAN static |
| `ucidyn set network.lan.ipaddr <ip>` | Set IPv4 |
| `ucidyn set network.lan.netmask <mask>` | Set netmask |
| `ucidyn set network.lan.gateway <gw>` | Set IPv4 GW |
| `ucidyn set network.lan.ip6addr '<cidr>'` | Set IPv6 |
| `ucidyn set network.lan.ip6gw '<gw>'` | Set IPv6 GW |
| `ucidyn set network.lan.mtu <n>` | Set LAN MTU |
| `ucidyn apply` | Apply live changes (may drop SSH) |
| `uci commit network` | Persist network UCI |
| `/etc/init.d/network reload` | Reload network stack |

**Also useful**

| Command | One-line work |
|---|---|
| `uci show network` | Full network config |
| `ip addr` / `ip route` / `ip -6 route` | Live addressing & routes |
| `ip link` | Interface list / state |
| `ethtool eth0` | PHY link speed/duplex (if present) |

---

## C) VLAN (ath1)

| Command | One-line work |
|---|---|
| `uci get vlan.ath1.mode` | VLAN mode (transparent/qinq/…) |
| `uci get vlan.ath1.mgmtvlan` | Management VLAN ID |
| `uci get vlan.ath1.svlan` | QinQ S-VLAN |
| `uci get vlan.ath1.cvlan` | QinQ C-VLAN |
| `uci set vlan.ath1.mode='transparent'` | Set mode |
| `uci set vlan.ath1.mgmtvlan='101'` | Set mgmt VLAN |
| `uci commit vlan` | Persist VLAN |
| `ucidyn set vlan.ath1.mgmtvlan <id>` | Live mgmt VLAN change |
| `ucidyn set vlan.ath1.mode 0` | Numeric mode (factory-style) |

**Also useful**

| Command | One-line work |
|---|---|
| `uci show vlan` | Full VLAN package |
| `bridge vlan show` | Kernel VLAN view (if supported) |

---

## D) Wireless / RF link

### Config reads

| Command | One-line work |
|---|---|
| `uci get wireless.@wifi-iface[1].ssid` | SSID |
| `uci get wireless.@wifi-iface[1].key` | PSK / key |
| `uci get wireless.@wifi-iface[1].encryption` | Encryption mode |
| `uci get wireless.@wifi-iface[1].mode` | ap / sta |
| `uci get wireless.@wifi-iface[1].disabled` | Radio disabled flag |
| `uci get wireless.wifi1.linktype` | Link type |
| `uci get wireless.wifi1.nwksecret` | Network secret |
| `uci get advwireless.ath1.htmode` | Channel width (HT20/40/80) |
| `uci get wireless.wifi1.distance` | Distance |
| `uci get wireless.@wifi-iface[1].maxsta` | Max stations |
| `uci get advwireless.ath1.channel` | Configured channel |
| `uci get txparam.ath1.atpcpower` | Tx power |
| `uci get txparam.ath1.atpcstatus` | ATPC on/off |
| `uci get txparam.ath1.ddrsstatus` | DDRS auto/manual |
| `uci get txparam.ath1.maxeirp` | Max EIRP |
| `uci -q get txparam.ath1.ddrsrate` | Manual MCS rate |
| `uci -q get txparam.ath1.spatialstream` | Spatial streams |
| `uci get ath1qos.qoscfg.dlulratio` | DL:UL ratio |
| `uci -q get advwireless.ath1.dcsstatus` | DCS status |
| `uci -q get advwireless.ath1.dcsthrld` | DCS threshold |

### Live RF tools / stats

| Command | One-line work |
|---|---|
| `ifconfig ath1` | VAP MAC / state |
| `iwconfig ath1` | Active channel / Tx power |
| `iw dev ath1 info` | nl80211 iface info |
| `cfg80211tool ath1 get_mode` | Runtime HT mode |
| `cfg80211tool ath1 g_kwnlinks` | Active RF link count |
| `cfg80211tool ath1 g_kwn_tput_test` | Link-test running? |
| `cfg80211tool wifi1 g_chanutil` | Channel utilization |
| `wlanconfig ath1 list` / `list sta` | Station table |
| `iw dev ath1 station dump` | Station dump (fallback) |
| `wlanconfig ath1 kickmac <mac>` | Kick one CPE |
| `cat /sys/class/kwn/wifi1/statistics/tx_tput` | Radio Tx throughput |
| `cat /sys/class/kwn/wifi1/statistics/rx_tput` | Radio Rx throughput |
| `cat /sys/class/kwn/wifi1/statistics/links` | Linked partners count |
| `cat /sys/class/kwn/wifi1/statistics/avg_rtx` | Avg retransmit % |
| `cat /sys/class/kwn/sua1/statistics/<field>` | Per-SU stats (mac, ip, rates, …) |
| `cat /tmp/kwn-wifi1-events.log` | Wi‑Fi event log (link up/down, Power Off, …) |
| `/usr/sbin/remote_exec.sh 1 "<cmd>"` | Run command on CPE via RF from BTS |
| `wifi reload` | Reload wireless stack |

### Typical sets

| Command | One-line work |
|---|---|
| `ucidyn set wireless.@wifi-iface[1].ssid <ssid>` | Set SSID |
| `ucidyn set wireless.@wifi-iface[1].key <key>` | Set key |
| `ucidyn set advwireless.ath1.htmode HT80` | Set channel width |
| `ucidyn set txparam.ath1.atpcpower 1` | Set Tx power |
| `ucidyn set txparam.ath1.ddrsstatus 0` | Manual DDRS |
| `ucidyn set txparam.ath1.ddrsrate 23` | Set MCS index |
| `ucidyn set ath1qos.qoscfg.dlulratio 50` | Set DL:UL |
| `uci commit wireless` / `txparam` / `advwireless` | Persist packages |

**Also useful**

| Command | One-line work |
|---|---|
| `uci show wireless` | Full wireless config |
| `uci show txparam` | Full Tx/DDRS params |
| `iwlist ath1 scan` | Scan (if supported) |
| `iwinfo ath1 info` | OpenWrt iwinfo summary |

---

## E) Bridge / ARP / learn table

| Command | One-line work |
|---|---|
| `brctl show br-lan` | Bridge ports |
| `brctl showmacs br-lan` | MAC learn / FDB table |
| `brctl setageing br-lan <sec>` | Set ageing time |
| `cat /sys/class/net/br-lan/bridge/ageing_time` | Read ageing |
| `ip neigh show` | ARP/ND neighbors |
| `cat /proc/net/arp` | IPv4 ARP proc table |
| `arping -U -c 3 -I br-lan <ip>` | Gratuitous ARP (populate tables) |

**Also useful**

| Command | One-line work |
|---|---|
| `bridge fdb show` | Modern FDB view |
| `ip -s link` | Interface counters |

---

## F) Logs / diagnostics

| Command | One-line work |
|---|---|
| `logread` | Full device syslog |
| `logread \| tail -n 80` | Recent logs |
| `logread -e 'uci:'` | Config-change lines |
| `logread -e 'temp'` | Temperature-related lines |
| `logread \| grep -Ei 'reboot\|boot'` | Boot/reboot evidence |
| `dmesg \| tail -n 50` | Kernel messages |
| `logger "MARKER"` | Inject a syslog marker |
| `cat /tmp/kwn-dying-gasp-remote.log` | Remote Dying Gasp detail log (BTS) |
| `cat /tmp/kwn-wifi1-events.log` | Wireless events incl. Power Off |

**Also useful**

| Command | One-line work |
|---|---|
| `logread -f` | Follow logs live |
| `ls -la /tmp /var/log` | Temp/log files |
| `ps w` / `top` | Processes / CPU |

---

## G) Services / daemons

| Command | One-line work |
|---|---|
| `/etc/init.d/network reload` | Reload networking |
| `/etc/init.d/system reload` | Reload system (syslog/NMS) |
| `/etc/init.d/lldpd restart` / `stop` | LLDP on/off |
| `pidof lldpd` | LLDP running? |
| `/usr/sbin/lldpcli show neighbors` | LLDP neighbor table |
| `/etc/init.d/dnsmasq restart` | Restart DHCP/DNS |
| `/etc/init.d/ntpd restart` | Restart NTP |
| `ntpdate -b -u <server>` | One-shot time sync |
| `uci show ntpd` | NTP server config |
| `/etc/init.d/<svc> restart` | Restart named service |

Common processes: `uhttpd`, `sshd`, `netifd`, `dnsmasq`, `odhcpd`, `ntpd`, `logd`, `rpcd`, `snmpd`, `senao-openapi-server`, `netlinkevents`, …

---

## H) Firmware / reboot / factory / backup

| Command | One-line work |
|---|---|
| `reboot` / `reboot -f` | Soft reboot |
| `sync; reboot` | Flush + reboot |
| `firstboot -y && reboot` | Factory wipe + reboot |
| `/usr/sbin/factory_reset.sh` | Vendor factory reset |
| `ucidyn set tftp.retip.retainip <mask>` | Retain-IP bits before factory |
| `sysupgrade -b /tmp/backup.tar.gz` | Backup config |
| `sysupgrade -r /tmp/backup.tar.gz` | Restore config |
| `find /tmp /var/tmp -name '*sysupgrade*'` | Flash staging leftovers |

---

## I) Capture / tools / misc

| Command | One-line work |
|---|---|
| `tcpdump -i ath1 -nn -c 50 -w /tmp/x.pcap ether proto 0x88cc` | Capture LLDP |
| `tcpdump -r /tmp/x.pcap -nn` | Read pcap |
| `uci get tool.ath1.bw` / `.dur` / `.vlanid` / `.dir` / `.iplist` | Link Test Tool UCI |
| `cfg80211tool ath1 g_kwn_tput_test` | Link test active |
| `cat /sys/class/kwn/sua1/statistics/tool_txtput` | Link-test UL tput |
| `cat /sys/class/kwn/sua1/statistics/tool_rxtput` | Link-test DL tput |
| `test -x /usr/sbin/remote_exec.sh && echo yes` | remote_exec available? |
| `cat /sys/class/net/eth0/address` | eth0 MAC |
| `cat /tmp/kwneth0/speed` | LAN PHY speed |
| `uci set system.@system[0].log_ip='<nms>'` | NMS/syslog target |

**Also useful**

| Command | One-line work |
|---|---|
| `uci show` | Dump **all** UCI packages |
| `uci changes` | Pending uncommitted changes |
| `uci revert <pkg>` | Discard pending for package |
| `ubus list` | Available ubus objects |
| `ubus call system board` | Board/model via ubus (if present) |
| `ls /usr/sbin/netlink_events/` | Event scripts (DGASP, LINK_UP, DCS, …) |
| `cat /usr/sbin/snmptrap.sh` | SNMP trap sender (NMS path) |

---

## Where defined in this repo

| Area | Path |
|---|---|
| Main catalog | `pages/commands.py` (`RootCommands`) |
| Sanity extras | `utils/sanity_commands.py` |
| VLAN builders | `utils/vlan_uci.py` |
| Link formation | `utils/link_formation.py` |
| ARP/Bridge | `utils/arp_bridge_table_flows.py` |
| Process monitor | removed — not on `Senao UBR Test Plan.xlsx` |
| 2.4G / API | `utils/radio24_scan_flows.py`, `utils/cpe_api_24_*.py` |
