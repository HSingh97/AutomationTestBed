# Testcase Validation Flowcharts

The flowcharts below describe how each automated testcase is validated in the framework.

## Summary Flowcharts

<details>
<summary><code>GUI_01</code> Summary System</summary>

```mermaid
flowchart LR
    A[Open Summary page] --> B[Read system widgets]
    B --> C[Collect model, version, uptime, and temp via SSH]
    C --> D[Normalize values]
    D --> E[Assert GUI equals backend]
```

</details>

<details>
<summary><code>GUI_02</code> Summary Network</summary>

```mermaid
flowchart LR
    A[Open Summary page] --> B[Read network widgets]
    B --> C[Collect IP, gateway, MAC, speed, and cable data via SSH]
    C --> D[Normalize GUI and backend values]
    D --> E[Assert GUI equals backend]
```

</details>

<details>
<summary><code>GUI_03</code> Summary Performance</summary>

```mermaid
flowchart LR
    A[Open Summary page] --> B[Read performance widgets]
    B --> C[Collect TX and RX counters via SSH]
    C --> D[Normalize throughput units]
    D --> E[Assert GUI equals backend]
```

</details>

<details>
<summary><code>GUI_04</code> Summary Wireless</summary>

```mermaid
flowchart LR
    A[Open Summary page] --> B[Read wireless widgets]
    B --> C[Collect radio status, SSID, bandwidth, channel, security, and RTX via SSH]
    C --> D[Normalize fields]
    D --> E[Assert GUI equals backend]
```

</details>

## Top Panel Flowcharts

<details>
<summary><code>GUI_05</code> Top Panel Logo</summary>

```mermaid
flowchart LR
    A[Open live GUI session] --> B[Locate header logo]
    B --> C[Click logo]
    C --> D[Verify redirect stays on expected GUI home]
```

</details>

<details>
<summary><code>GUI_06</code> Top Panel Parameters</summary>

```mermaid
flowchart LR
    A[Open live GUI session] --> B[Read top panel fields]
    B --> C[Collect hostname, description, and uptime via SSH]
    C --> D[Normalize values]
    D --> E[Assert GUI equals backend]
```

</details>

<details>
<summary><code>GUI_07</code> Top Panel Radio Redirect</summary>

```mermaid
flowchart LR
    A[Open live GUI session] --> B[Click radio shortcut in top panel]
    B --> C[Wait for radio page load]
    C --> D[Assert redirect lands on expected radio section]
```

</details>

<details>
<summary><code>GUI_08</code> Home and Apply Buttons</summary>

```mermaid
flowchart LR
    A[Open live GUI session] --> B[Validate Home button navigation]
    B --> C[Validate Apply control is visible and actionable]
    C --> D[Assert no GUI navigation error]
```

</details>

<details>
<summary><code>GUI_09</code> Reboot Device</summary>

```mermaid
flowchart LR
    A[Open live GUI session] --> B[Locate reboot control]
    B --> C[Click reboot button path under test]
    C --> D[Assert reboot control is reachable from GUI]
```

</details>

<details>
<summary><code>GUI_10</code> Logout</summary>

```mermaid
flowchart LR
    A[Open authenticated GUI session] --> B[Click Logout]
    B --> C[Wait for login screen]
    C --> D[Assert session is terminated]
```

</details>

## Wireless Properties Flowcharts

<details>
<summary><code>GUI_17</code> Radio Status</summary>

```mermaid
flowchart LR
    A[Open Wireless > Radio 1] --> B[Capture current radio status]
    B --> C[Toggle status in GUI]
    C --> D[Save and apply]
    D --> E[Verify backend status via SSH]
    E --> F[Reload GUI and verify]
    F --> G[Restore baseline]
```

</details>

<details>
<summary><code>GUI_18</code> SSID</summary>

```mermaid
flowchart LR
    A[Open Wireless > Radio 1] --> B[Capture current SSID]
    B --> C[Update SSID in GUI]
    C --> D[Save and apply]
    D --> E[Verify SSID via SSH]
    E --> F[Reload GUI and verify]
    F --> G[Restore baseline]
```

</details>

<details>
<summary><code>GUI_19</code> Bandwidth</summary>

```mermaid
flowchart LR
    A[Open Wireless > Radio 1] --> B[Capture current bandwidth]
    B --> C[Select new bandwidth]
    C --> D[Save and apply]
    D --> E[Verify backend bandwidth via SSH]
    E --> F[Reload GUI and verify]
    F --> G[Restore baseline]
```

</details>

<details>
<summary><code>GUI_20</code> Channel</summary>

```mermaid
flowchart LR
    A[Open Wireless > Radio 1] --> B[Read configured and active channel]
    B --> C[Collect backend channel state via SSH]
    C --> D[Normalize channel representation]
    D --> E[Assert GUI is consistent with backend]
```

</details>

<details>
<summary><code>GUI_21</code> Encryption</summary>

```mermaid
flowchart LR
    A[Open Wireless > Radio 1] --> B[Capture current encryption values]
    B --> C[Change encryption and key in GUI]
    C --> D[Save and apply]
    D --> E[Verify encryption via SSH]
    E --> F[Reload GUI and verify]
    F --> G[Restore baseline]
```

</details>

<details>
<summary><code>GUI_22</code> Max CPE</summary>

```mermaid
flowchart LR
    A[Open Wireless > Radio 1] --> B[Capture current max CPE value]
    B --> C[Update max CPE in GUI]
    C --> D[Save and apply]
    D --> E[Verify backend value via SSH]
    E --> F[Reload GUI and verify]
    F --> G[Restore baseline]
```

</details>

## Network Flowcharts

<details>
<summary><code>GUI_50</code> Network IP Configuration</summary>

```mermaid
flowchart LR
    A[Open Network > IP] --> B[Read IPv4 and IPv6 settings from GUI]
    B --> C[Collect configured network values via SSH]
    C --> D[Normalize address formats]
    D --> E[Assert GUI equals backend]
```

</details>

<details>
<summary><code>GUI_51</code> Edit IP Configuration</summary>

```mermaid
flowchart LR
    A[Open Network > IP] --> B[Capture current LAN IP]
    B --> C[Update LAN IP in GUI]
    C --> D[Save and apply]
    D --> E[Reconnect to GUI using recovery flow]
    E --> F[Verify new IP through GUI and backend]
    F --> G[Restore baseline]
```

</details>

<details>
<summary><code>GUI_52</code> Edit Netmask Configuration</summary>

```mermaid
flowchart LR
    A[Open Network > IP] --> B[Capture current netmask]
    B --> C[Update netmask in GUI]
    C --> D[Save and apply]
    D --> E[Verify netmask via SSH]
    E --> F[Reload GUI and verify]
    F --> G[Restore baseline]
```

</details>

<details>
<summary><code>GUI_53</code> Edit Gateway Configuration</summary>

```mermaid
flowchart LR
    A[Open Network > IP] --> B[Capture current gateway]
    B --> C[Update gateway in GUI]
    C --> D[Save and apply]
    D --> E[Verify gateway via SSH]
    E --> F[Reload GUI and verify]
    F --> G[Restore baseline]
```

</details>

<details>
<summary><code>GUI_54</code> Edit Fallback IP</summary>

```mermaid
flowchart LR
    A[Open Network > IP] --> B[Capture current fallback IP]
    B --> C[Update fallback IP in GUI]
    C --> D[Save and apply]
    D --> E[Verify fallback IP via SSH]
    E --> F[Reload GUI and verify]
    F --> G[Restore baseline]
```

</details>

<details>
<summary><code>GUI_55</code> Edit Fallback Netmask</summary>

```mermaid
flowchart LR
    A[Open Network > IP] --> B[Capture current fallback netmask]
    B --> C[Update fallback netmask in GUI]
    C --> D[Save and apply]
    D --> E[Verify fallback netmask via SSH]
    E --> F[Reload GUI and verify]
    F --> G[Restore baseline]
```

</details>

<details>
<summary><code>GUI_70</code> Ethernet Speed and Duplex</summary>

```mermaid
flowchart LR
    A[Open Network > Ethernet] --> B[Capture current speed and duplex]
    B --> C[Update speed and duplex in GUI]
    C --> D[Save and apply]
    D --> E[Verify Ethernet backend values via SSH]
    E --> F[Reload GUI and verify]
    F --> G[Restore baseline]
```

</details>

<details>
<summary><code>GUI_71</code> Ethernet MTU</summary>

```mermaid
flowchart LR
    A[Open Network > Ethernet] --> B[Capture current MTU]
    B --> C[Update MTU in GUI]
    C --> D[Save and apply]
    D --> E[Verify backend MTU via SSH]
    E --> F[Reload GUI and verify]
    F --> G[Restore baseline]
```

</details>

<details>
<summary><code>GUI_72</code> DHCP Server Status</summary>

```mermaid
flowchart LR
    A[Open Network > DHCP] --> B[Capture current DHCP status]
    B --> C[Toggle DHCP server state]
    C --> D[Save and apply]
    D --> E[Verify DHCP setting via SSH]
    E --> F[Reload GUI and verify]
    F --> G[Restore baseline]
```

</details>

<details>
<summary><code>GUI_73</code> DHCP Lease Time</summary>

```mermaid
flowchart LR
    A[Open Network > DHCP] --> B[Capture current lease time]
    B --> C[Update lease time in GUI]
    C --> D[Save and apply]
    D --> E[Verify lease time via SSH]
    E --> F[Reload GUI and verify]
    F --> G[Restore baseline]
```

</details>

<details>
<summary><code>GUI_74</code> DHCP 2.4 GHz Radio IP</summary>

```mermaid
flowchart LR
    A[Open Network > DHCP 2.4 GHz tab] --> B[Capture current radio IP]
    B --> C[Update radio IP in GUI]
    C --> D[Save and apply]
    D --> E[Verify backend value via SSH]
    E --> F[Reload GUI and verify]
    F --> G[Restore baseline]
```

</details>

<details>
<summary><code>GUI_75</code> DHCP 2.4 GHz Radio Netmask</summary>

```mermaid
flowchart LR
    A[Open Network > DHCP 2.4 GHz tab] --> B[Capture current radio netmask]
    B --> C[Update radio netmask in GUI]
    C --> D[Save and apply]
    D --> E[Verify backend value via SSH]
    E --> F[Reload GUI and verify]
    F --> G[Restore baseline]
```

</details>

<details>
<summary><code>GUI_76</code> DHCP 2.4 GHz Radio DHCP Status</summary>

```mermaid
flowchart LR
    A[Open Network > DHCP 2.4 GHz tab] --> B[Capture current DHCP state]
    B --> C[Toggle DHCP status]
    C --> D[Save and apply]
    D --> E[Verify backend value via SSH]
    E --> F[Reload GUI and verify]
    F --> G[Restore baseline]
```

</details>

<details>
<summary><code>GUI_77</code> DHCP 2.4 GHz Radio Pool Range</summary>

```mermaid
flowchart LR
    A[Open Network > DHCP 2.4 GHz tab] --> B[Capture pool start and limit]
    B --> C[Update pool range in GUI]
    C --> D[Save and apply]
    D --> E[Verify backend values via SSH]
    E --> F[Reload GUI and verify]
    F --> G[Restore baseline]
```

</details>

<details>
<summary><code>GUI_78</code> DHCP 2.4 GHz Radio Lease Time</summary>

```mermaid
flowchart LR
    A[Open Network > DHCP 2.4 GHz tab] --> B[Capture current lease time]
    B --> C[Update lease time in GUI]
    C --> D[Save and apply]
    D --> E[Verify backend value via SSH]
    E --> F[Reload GUI and verify]
    F --> G[Restore baseline]
```

</details>

## Management Flowcharts

<details>
<summary><code>GUI_88</code> Management Timezone Random Validation</summary>

```mermaid
flowchart LR
    A[Open Management > System] --> B[Capture current timezone]
    B --> C[Pick alternate timezone]
    C --> D[Save and apply]
    D --> E[Verify timezone via SSH]
    E --> F[Reload GUI and verify]
    F --> G[Restore baseline]
```

</details>

<details>
<summary><code>GUI_89</code> Management NTP Full Cycle</summary>

```mermaid
flowchart LR
    A[Open Management > System] --> B[Read current NTP server list]
    B --> C[Add new NTP server]
    C --> D[Save and apply]
    D --> E[Verify via SSH]
    E --> F[Delete added server and reapply]
    F --> G[Verify cleanup]
```

</details>

<details>
<summary><code>GUI_90</code> Sync with Browser Time</summary>

```mermaid
flowchart LR
    A[Open Management > System] --> B[Trigger browser time sync]
    B --> C[Save and apply]
    C --> D[Read device time via SSH]
    D --> E[Compare device time with browser time window]
```

</details>

<details>
<summary><code>GUI_91</code> Management Logging IP and Port</summary>

```mermaid
flowchart LR
    A[Open Management > Logging] --> B[Capture current log IP and port]
    B --> C[Update logging destination]
    C --> D[Save and apply]
    D --> E[Verify backend config via SSH]
    E --> F[Reload GUI and verify]
    F --> G[Restore baseline]
```

</details>

<details>
<summary><code>GUI_92</code> Management Temperature Logging Cycle</summary>

```mermaid
flowchart LR
    A[Open Management > Logging] --> B[Capture temp logging settings]
    B --> C[Enable or update temp logging interval]
    C --> D[Save and apply]
    D --> E[Verify backend config and generated temp logs]
    E --> F[Reload GUI and verify]
    F --> G[Restore baseline]
```

</details>

<details>
<summary><code>GUI_93</code> Management Location Configuration</summary>

```mermaid
flowchart LR
    A[Open Management > Location] --> B[Capture current location fields]
    B --> C[Update location details in GUI]
    C --> D[Save and apply]
    D --> E[Verify backend values via SSH]
    E --> F[Reload GUI and verify]
    F --> G[Restore baseline]
```

</details>

## Monitor Flowcharts

<details>
<summary><code>GUI_105</code> Monitor Learn Table Bridge Table</summary>

```mermaid
flowchart LR
    A[Open Monitor > Learn Table > Bridge] --> B[Read GUI bridge entries for BTS and CPE]
    B --> C[Collect bridge FDB via SSH]
    C --> D[Normalize MAC, interface, and ageing values]
    D --> E[Assert GUI equals backend with no duplicates]
```

</details>

<details>
<summary><code>GUI_106</code> Monitor Learn Table Bridge Refresh and Clear</summary>

```mermaid
flowchart LR
    A[Open Monitor > Learn Table > Bridge] --> B[Capture baseline bridge entries]
    B --> C[Click Refresh and wait for GUI to match backend]
    C --> D[Click Clear]
    D --> E[Wait for relearn behavior]
    E --> F[Assert current active entries repopulate correctly]
```

</details>

<details>
<summary><code>GUI_107</code> Monitor Learn Table ARP Table</summary>

```mermaid
flowchart LR
    A[Open Monitor > Learn Table > ARP] --> B[Read GUI ARP entries for BTS and CPE]
    B --> C[Collect ARP entries via SSH]
    C --> D[Normalize MAC, interface, and IP values]
    D --> E[Assert GUI equals backend with no duplicates]
```

</details>

<details>
<summary><code>GUI_108</code> Monitor Learn Table ARP Refresh and Clear</summary>

```mermaid
flowchart LR
    A[Open Monitor > Learn Table > ARP] --> B[Capture baseline ARP entries]
    B --> C[Click Refresh and wait for GUI to match backend]
    C --> D[Click Clear]
    D --> E[Wait for relearn behavior]
    E --> F[Assert current active entries repopulate correctly]
```

</details>

<details>
<summary><code>GUI_109</code> Monitor System Logs Config Logs and Refresh</summary>

```mermaid
flowchart LR
    A[Open Management > System] --> B[Change timezone as reversible config action]
    B --> C[Save and apply]
    C --> D[Open Monitor > System Logs > Config]
    D --> E[Refresh logs]
    E --> F[Match GUI log with config log backend source]
    F --> G[Restore baseline timezone]
```

</details>

<details>
<summary><code>GUI_110</code> Monitor System Logs Device Logs and Refresh</summary>

```mermaid
flowchart LR
    A[Open Monitor > System Logs > Device] --> B[Read GUI device logs for BTS and CPE]
    B --> C[Collect device log file via SSH]
    C --> D[Refresh logs]
    D --> E[Assert GUI matches backend device log source]
```

</details>

<details>
<summary><code>GUI_111</code> Monitor System Logs Temperature Logs, Refresh, and Clear</summary>

```mermaid
flowchart LR
    A[Open Monitor > System Logs > Temperature] --> B[Read GUI temperature logs]
    B --> C[Collect temp log file via SSH]
    C --> D[Refresh and assert GUI equals backend]
    D --> E[Clear logs]
    E --> F[Refresh and assert log source is empty]
```

</details>

<details>
<summary><code>GUI_112</code> Monitor System Logs System Logs and Refresh</summary>

```mermaid
flowchart LR
    A[Open Monitor > System Logs > System] --> B[Inject safe log marker through SSH]
    B --> C[Refresh GUI logs]
    C --> D[Read logread backend source]
    D --> E[Assert marker appears in backend and GUI]
```

</details>

## Jumbo Frame Flowcharts

<details>
<summary><code>JMB_01</code> Configure Jumbo and disable back to default</summary>

```mermaid
flowchart LR
    A[Open Network > Ethernet] --> B[Set non-default jumbo MTU]
    B --> C[Save and apply]
    C --> D[Verify backend MTU via SSH]
    D --> E[Set MTU back to default]
    E --> F[Verify GUI and backend return to baseline]
```

</details>

<details>
<summary><code>JMB_02</code> Configure MTU 9000</summary>

```mermaid
flowchart LR
    A[Open BTS and CPE Ethernet settings] --> B[Set MTU 9000]
    B --> C[Save and apply]
    C --> D[Verify backend MTU values via SSH]
    D --> E[Assert GUI reflects MTU 9000]
```

</details>

<details>
<summary><code>JMB_03</code> Min and mid MTU cycle with ICMP validation</summary>

```mermaid
flowchart LR
    A[Iterate min and mid MTU values] --> B[Apply same MTU on BTS and CPE]
    B --> C[Verify backend MTU via SSH]
    C --> D[Run device-to-device ICMP payload check]
    D --> E[Assert ping success for each MTU]
```

</details>

<details>
<summary><code>JMB_04</code> Max MTU 9000 with ICMP validation</summary>

```mermaid
flowchart LR
    A[Set BTS and CPE MTU to 9000] --> B[Save and apply]
    B --> C[Verify backend MTU via SSH]
    C --> D[Run device-to-device jumbo ICMP validation]
    D --> E[Assert ping success at MTU 9000]
```

</details>

<details>
<summary><code>JMB_05</code> Jumbo with management VLAN and interface checks</summary>

```mermaid
flowchart LR
    A[Apply jumbo MTU in management path] --> B[Save and apply]
    B --> C[Verify relevant interface and config state via SSH]
    C --> D[Check GUI reflects updated management-side MTU state]
    D --> E[Restore baseline if needed]
```

</details>

<details>
<summary><code>JMB_06</code> Jumbo MTU 9000 with P2MP validation</summary>

```mermaid
flowchart LR
    A[Set jumbo MTU on P2MP path] --> B[Verify BTS and CPE backend MTU]
    B --> C[Run device-to-device ICMP validation]
    C --> D[Assert jumbo traffic passes on active link]
```

</details>

<details>
<summary><code>JMB_07</code> Reboot persistence</summary>

```mermaid
flowchart LR
    A[Set jumbo MTU on target interfaces] --> B[Save and apply]
    B --> C[Trigger reboot]
    C --> D[Recover GUI and SSH session]
    D --> E[Re-read MTU via GUI and SSH]
    E --> F[Assert MTU persisted after reboot]
```

</details>

<details>
<summary><code>JMB_08</code> MTU 1500 validation</summary>

```mermaid
flowchart LR
    A[Set BTS and CPE MTU to 1500] --> B[Save and apply]
    B --> C[Verify backend MTU via SSH]
    C --> D[Assert GUI reflects 1500]
    D --> E[Confirm default MTU behavior is restored]
```

</details>

<details>
<summary><code>JMB_09</code> Boundary and invalid MTU validation</summary>

```mermaid
flowchart LR
    A[Try valid boundary MTU values] --> B[Verify accept path]
    B --> C[Try invalid MTU values]
    C --> D[Verify GUI validation or backend rejection]
    D --> E[Assert device remains on valid state]
```

</details>

<details>
<summary><code>JMB_10</code> Factory reset default MTU</summary>

```mermaid
flowchart LR
    A[Set non-default jumbo MTU] --> B[Save and apply]
    B --> C[Trigger factory reset]
    C --> D[Recover device session and default GUI access]
    D --> E[Read MTU via GUI and SSH]
    E --> F[Assert MTU returned to factory default]
```

</details>
