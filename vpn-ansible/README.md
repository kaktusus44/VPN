# VPN Ansible

Repeatable deployment for active `main` and `reserve` VPN servers.

Primary VPN: AmneziaWG.
Reserve anti-blocking channel: Xray / VLESS / Reality.
DNS: Unbound inside the VPN only.
Monitoring is a separate layer.

## Ubuntu Control Machine Quick Start

On a fresh Ubuntu machine, run one command to install the local tools, clone
this repository to `~/VPN`, and install Ansible collections:

```bash
wget -qO- https://raw.githubusercontent.com/kaktusus44/VPN/main/scripts/bootstrap-ubuntu-control | bash
```

Then run only the safe checks printed by the script. Do not run
`playbooks/vpn-core.yml` until the change window is approved.

See [docs/ubuntu-control-machine.md](docs/ubuntu-control-machine.md) for the
step-by-step version.

## First Test Server

`108.165.33.37` is the first clean test host.

Preflight on 2026-08-30:

- Ubuntu 24.04.4 LTS
- kernel `6.8.0-111-generic`
- only public service found: SSH on `22/tcp`
- UFW installed but inactive
- no AmneziaWG, Xray, Unbound, fail2ban, nginx, or monitoring stack detected

Do not store SSH passwords in this repo. Use `--ask-pass` or a temporary local environment for test access, then move to key-only SSH.

## Repository Shape

```text
inventories/
  test/hosts.yml
  prod/hosts.yml
group_vars/
  all.yml
  vpn_servers.yml
host_vars/
  vpn-test-1.yml
playbooks/
  preflight.yml
  vpn-core.yml
  monitoring-agents.yml
  validate.yml
  roles/
  base/
  firewall/
  fail2ban/
  amneziawg/
  unbound_dns/
  xray_reality/
  vpn_clients/
  monitoring_agent/
  alerting/
  validation/
scripts/
  export-client-links
  compare-server-configs
  rotate-endpoint
docs/
  runbook.md
  export-file-contract.md
```

## Export Format

Primary export is a plain line-based file. One line is one ready-to-copy client artifact:

```text
client-001 main amneziawg <ready-client-link>
client-001 main vless_xhttp vless://...
client-001 main vless_tcp vless://...
```

Field order:

```text
client_id server_role protocol ready_link
```

## Monitoring

Monitoring is a separate layer. The VPN server should expose host metrics, AWG metrics, and project-specific VPN health metrics. Alert delivery should be handled through Prometheus alert rules and Alertmanager, not through direct Telegram logic embedded in the exporter.
