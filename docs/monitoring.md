# Monitoring Architecture

Monitoring is a separate deployment layer.

## Server-Side Agents

Each VPN server should run:

- `node_exporter` for host metrics.
- `amneziawg-exporter` or a compatible AWG exporter for peer handshake/traffic metrics.
- `vpn-custom-exporter.service` for project-specific VPN health logic.

The custom exporter replaces the current ad hoc CSV sampler + Telegram watchdog shape as the long-term interface. It exposes Prometheus metrics, and alert delivery moves to Prometheus + Alertmanager.

The first implementation may run as root because `awg show` generally needs elevated network privileges. Keep it constrained with systemd hardening. Later, reduce privileges with a narrow sudoers rule or capabilities after the command path is proven.

## Custom Exporter Metrics

Planned metric groups:

- `vpn_awg_active_peers`
- `vpn_awg_total_peers`
- `vpn_awg_peer_handshake_age_seconds`
- `vpn_awg_peer_rx_bytes_total`
- `vpn_awg_peer_tx_bytes_total`
- `vpn_operator_active_peers`
- `vpn_operator_drop_ratio`
- `vpn_stall_detected`
- `vpn_sampler_last_success_timestamp_seconds`
- `vpn_xray_established_connections`

Labels should be coarse and privacy-aware:

- `server`
- `role`
- `operator`
- `protocol`
- short peer fingerprint only, when needed

Do not expose client IPs, private keys, preshared keys, UUIDs, or full client names unless explicitly approved.

## Alerting

Prometheus evaluates alert rules.
Alertmanager routes notifications.

Initial rules:

- `VpnServerDown`
- `AmneziaWGDown`
- `UnboundDown`
- `XrayDown`
- `NoActivePeers`
- `PeerCountCollapse`
- `OperatorDrop`
- `VpnStallDetected`
- `VpnExporterStale`
- `HostDiskHigh`
- `HostMemoryPressure`

Existing `vpn-tg` thresholds are the starting point:

- live peer: handshake age under 180 seconds
- blunt drop: online below 5 for 10 minutes after a previous baseline of at least 15
- sensitive drop: below 40% of recent median for 5 minutes
- sampler death: no samples for 300 seconds
- repeat suppression: Alertmanager repeat intervals, not custom Telegram state
