#!/usr/bin/env python3
"""Return existing monitoring settings over the authenticated SSH channel."""
import configparser
import json
from pathlib import Path
import yaml

state = Path('/var/lib/vpn/monitoring-settings.json')
data = json.loads(state.read_text()) if state.exists() else {}
if not data:
    grafana = configparser.ConfigParser(interpolation=None)
    grafana.read('/etc/grafana/grafana.ini')
    password = grafana.get('security', 'admin_password', fallback='')
    if password:
        data['monitoring_grafana_admin_password'] = password
    alerts = Path('/etc/prometheus/alertmanager.yml')
    if alerts.exists():
        config = yaml.safe_load(alerts.read_text()) or {}
        data['monitoring_alertmanager_telegram_enabled'] = False
        for receiver in config.get('receivers', []):
            for telegram in receiver.get('telegram_configs', []):
                data.update(monitoring_alertmanager_telegram_enabled=True,
                            monitoring_alertmanager_telegram_bot_token=telegram.get('bot_token', ''),
                            monitoring_alertmanager_telegram_chat_id=str(telegram.get('chat_id', '')))
print(json.dumps(data))
