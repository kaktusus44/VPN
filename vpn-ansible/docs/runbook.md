# Runbook

## First Test Flow

1. Run preflight against the clean test host.
2. Apply base hardening.
3. Apply firewall and fail2ban.
4. Deploy AmneziaWG.
5. Deploy Unbound DNS.
6. Deploy Xray / Reality.
7. Generate one test client.
8. Export ready-to-copy links.
9. Compare the resulting server with the audited source host.

## Guardrails

- Keep password access temporary.
- Move to key-only SSH before production.
- Validate config files before restarting services.
- Do not expose app/test ports publicly.
- Do not put secrets in process command lines.

