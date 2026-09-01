# Monitoring Architecture

Monitoring is a separate deployment layer.

## Server-Side Agents

Each VPN server should run:

- `node_exporter` for host metrics.
- `amneziawg-exporter` or a compatible AWG exporter for peer handshake/traffic metrics.
- `vpn-custom-exporter.service` for project-specific VPN health logic.

The custom exporter replaces the current ad hoc CSV sampler + Telegram watchdog shape as the long-term interface. It exposes Prometheus metrics, and alert delivery moves to Prometheus + Alertmanager.

The first implementation may run as root because `awg show` generally needs elevated network privileges. Keep it constrained with systemd hardening. Later, reduce privileges with a narrow sudoers rule or capabilities after the command path is proven.

## Legacy Custom Monitoring Contract

The legacy source server had three project-specific monitoring units:

- `vpn-monitor.service`
- `vpn-tg.service`
- `vpn-asn-update.timer`

`vpn-monitor` sampled AmneziaWG every 30 seconds and wrote `/var/log/vpn-monitor/awg.csv` with this shape:

```csv
ts,peer,operator,hs_age,rx,tx,drx,dtx
```

The new exporter must preserve the useful signal from that pipeline:

- `peer`: internal peer identity, exported only as a short non-reversible fingerprint unless full names are explicitly approved.
- `operator`: provider/mobile operator/ASN mapping used by the customer to understand which providers use the VPN.
- `hs_age`: peer handshake age, used for online/offline and stall logic.
- `rx`/`tx`: peer traffic counters.
- `drx`/`dtx`: traffic deltas between samples, used to detect activity and freezes.

`vpn-asn-update.timer` maintained a local IP-to-ASN table from iptoasn.com. The sampler used the peer endpoint IP only in memory and wrote only the provider/operator label to CSV, so client IPs did not remain in monitoring logs. In the Ansible version this stays as a local table:

- `/var/lib/vpn-asn/ip2asn.tsv.gz`

The exporter should also support an optional static override map for special cases:

- `/etc/vpn-monitor/peer-provider-map.csv`
- `/var/lib/vpn-custom-exporter/peer-provider-map.json`

The exporter must expose provider-level aggregates without leaking private peer metadata or endpoint IPs.

## Custom Exporter Metrics

Planned metric groups:

- `vpn_awg_active_peers`
- `vpn_awg_total_peers`
- `vpn_awg_peer_handshake_age_seconds`
- `vpn_awg_peer_rx_bytes_total`
- `vpn_awg_peer_tx_bytes_total`
- `vpn_awg_peer_rx_delta_bytes`
- `vpn_awg_peer_tx_delta_bytes`
- `vpn_operator_active_peers`
- `vpn_operator_total_peers`
- `vpn_operator_rx_bytes_total`
- `vpn_operator_tx_bytes_total`
- `vpn_operator_rx_delta_bytes`
- `vpn_operator_tx_delta_bytes`
- `vpn_operator_drop_ratio`
- `vpn_stall_detected`
- `vpn_sampler_last_success_timestamp_seconds`
- `vpn_provider_mapping_last_success_timestamp_seconds`
- `vpn_provider_mapping_entries`
- `vpn_xray_established_connections`

Labels should be coarse and privacy-aware:

- `server`
- `role`
- `operator` / `provider`
- `protocol`
- short peer fingerprint only, when needed

Do not expose client IPs, private keys, preshared keys, UUIDs, or full client names unless explicitly approved.

## Alerting

Prometheus evaluates alert rules.
Alertmanager routes notifications.

Current rules:

- `VpnServerDown`
- `AmneziaWGDown`
- `UnboundDown`
- `XrayDown`
- `VpnExporterDown`
- `NoActivePeers`
- `PeerCountCollapse`
- `OperatorDrop`
- `VpnStallDetected`
- `VpnExporterStale`
- `ProviderMappingStale`
- `ProviderMappingEmpty`
- `HostDiskHigh`
- `HostMemoryPressure`

The main server also exposes Alertmanager through nginx on
`https://<server-ip>:9443/` with Basic Auth. Prometheus remains bound to
localhost.

In a `main` + `reserve` deployment, Prometheus runs only on `main`. The reserve
server runs `prometheus-node-exporter` and `vpn-custom-exporter`; UFW permits
scraping ports `9100` and `9187` only from the main server IP.

Existing `vpn-tg` thresholds are the starting point:

- live peer: handshake age under 180 seconds
- blunt drop: online below 5 for 10 minutes after a previous baseline of at least 15
- sensitive drop: below 40% of recent median for 5 minutes
- operator/provider drop: provider-level online count falls sharply from its recent baseline
- stall/freeze: active handshakes exist but traffic deltas stop moving across a meaningful portion of peers
- sampler death: no samples for 300 seconds
- provider mapping death: mapping update fails or produces zero mapped peers
- repeat suppression: Alertmanager repeat intervals, not custom Telegram state
