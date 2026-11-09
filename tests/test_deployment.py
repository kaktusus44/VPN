import contextlib
import gzip
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import jinja2
import yaml

ROOT = Path(__file__).resolve().parents[1]
ENV = jinja2.Environment(undefined=jinja2.StrictUndefined)
ENV.filters.update(bool=lambda value: value if isinstance(value, bool) else str(value).lower() in ('true', 'yes', '1'), to_json=json.dumps)


def variables():
    data = {}
    for file in ['group_vars/all.yml', 'group_vars/vpn_servers.yml']:
        data.update(yaml.safe_load((ROOT / file).read_text()))
    data.update(vpn_server_role='main', vpn_public_endpoint='8.8.8.8', vpn_exit_ipv4='',
                vpn_disable_ipv6=True, vpn_client_count=2, ansible_default_ipv4={'interface': 'eth0'},
                xray_client_uuid='test-uuid', xray_reality_private_key='private', xray_reality_short_id='abcd',
                vpn_xray_client_uuid='test-uuid', vpn_xray_reality_public_key='public', vpn_xray_reality_short_id='abcd')
    return data


def render(file, values):
    source = (ROOT / file).read_text()
    if source.startswith('#jinja2:'):
        source = source.split('\n', 1)[1]
    return ENV.from_string(source).render(**values)


class ConfigTests(unittest.TestCase):
    def test_xray_ipv6_and_exit_variants(self):
        for disabled in [False, True]:
            for exit_ip in ['', '1.1.1.1']:
                data = variables() | {'vpn_disable_ipv6': disabled, 'vpn_exit_ipv4': exit_ip}
                config = json.loads(render('roles/xray_reality/templates/config.json.j2', data))
                direct = config['outbounds'][0]
                self.assertEqual(direct.get('sendThrough', ''), exit_ip)
                self.assertEqual(direct['settings']['domainStrategy'], 'ForceIPv4' if disabled else 'UseIPv4')
                self.assertEqual('routing' in config, disabled)
                if disabled:
                    self.assertEqual(config['outbounds'][1]['tag'], 'block-ipv6')
                self.assertEqual(len(config['inbounds']), 2)
                self.assertTrue(all(item['settings']['clients'][0]['id'] == 'test-uuid' for item in config['inbounds']))

    def test_tun_config_captures_both_families(self):
        config = json.loads(render('roles/vpn_clients/templates/xray-tun-client.json.j2', variables()))
        self.assertTrue(config['inbounds'][0]['strict_route'])
        self.assertEqual(len(config['inbounds'][0]['address']), 2)
        self.assertEqual(config['route']['final'], 'vpn')
        self.assertEqual(config['outbounds'][0]['server'], '8.8.8.8')

    def test_generator_repeat_and_ip_migration(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / 'state'
            state.mkdir()
            (state / 'awg0.server.key').write_text('server-secret')
            config = Path(folder) / 'awg0.conf'
            stops = []
            fail_start = []

            def fake_run(args, **kwargs):
                if args[:2] == ['systemctl', 'start'] and fail_start:
                    fail_start.pop()
                    raise RuntimeError('simulated startup failure')
                if args[:2] == ['systemctl', 'stop']:
                    stops.append(config.read_text())
                    return types.SimpleNamespace(stdout='')
                return types.SimpleNamespace(stdout=('pub-' + kwargs.get('input', '').strip()) if args[1] == 'pubkey' else 'generated-key')

            def generate(extra=None):
                data = variables() | {'awg_config_dir': Path(folder).as_posix(), 'vpn_enable_ipv6': False}
                data.update(extra or {})
                source = render('roles/amneziawg/templates/generate-amnezia-clients.py.j2', data)
                source = source.replace('/var/lib/vpn', state.as_posix())
                output = io.StringIO()
                with patch('subprocess.run', side_effect=fake_run), contextlib.redirect_stdout(output):
                    exec(compile(source, '<generator>', 'exec'), {'__name__': '__main__'})
                return output.getvalue()

            self.assertIn('server_changed=true', generate())
            self.assertIn('AllowedIPs = 0.0.0.0/0, ::/0', (state/'client-001.awg.conf').read_text())
            original_key = (state/'client-001.key').read_text()
            self.assertIn('changed=false server_changed=false', generate())
            self.assertEqual(stops, [])
            self.assertIn('server_changed=false', generate({'vpn_public_endpoint': '9.9.9.9'}))
            self.assertEqual(stops, [])
            self.assertIn('server_changed=true', generate({'vpn_exit_ipv4': '1.1.1.1'}))
            self.assertEqual(len(stops), 1)
            self.assertIn('-j MASQUERADE', stops[0])
            self.assertIn('-j SNAT --to-source 1.1.1.1', config.read_text())
            self.assertEqual((state/'client-001.key').read_text(), original_key)
            working_config = config.read_text()
            fail_start.append(True)
            with self.assertRaises(RuntimeError): generate({'vpn_exit_ipv4':'9.9.9.9'})
            self.assertEqual(config.read_text(), working_config)


class NetworkTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('vpn_network', ROOT/'scripts/vpn-network.py')
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_reject_non_public_addresses(self):
        for value in ['127.0.0.1', '10.0.0.1', '172.16.0.1', '192.168.0.1', '100.64.0.1', '224.0.0.1', '999.2.3.4']:
            with self.assertRaises(ValueError):
                self.module.public_ipv4(value)
        self.assertEqual(self.module.public_ipv4('8.8.8.8'), '8.8.8.8')

    def test_netplan_add_and_rollback(self):
        for fail in [False, True]:
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                (root/'etc/netplan').mkdir(parents=True)
                file = root/'etc/netplan/99-vpn-extra-ip.yaml'
                applied = []
                calls = []

                def fake_run(*args):
                    calls.append(args)
                    if args[:4] == ('ip', '-j', '-4', 'route'):
                        return json.dumps([{'dev': 'eth0', 'gateway': '8.8.8.1'}])
                    if args[:4] == ('ip', '-j', '-4', 'address'):
                        return json.dumps([{'ifname':'lo', 'addr_info':[{'local':'127.0.0.1'}]}, {'ifname':'eth0', 'addr_info':[{'local':ip} for ip in ['8.8.8.8','10.0.0.1'] + applied]}])
                    if args == ('netplan', 'get'):
                        return json.dumps({'network':{'version':2, 'ethernets': {'primary':{'set-name':'eth0'}}}})
                    if args[:2] == ('networkctl', 'reconfigure'):
                        applied[:] = ['1.1.1.1'] if file.exists() else []
                        if fail and file.exists():
                            raise RuntimeError('simulated failure')
                    return ''

                with patch.object(self.module, 'Path', side_effect=lambda value: root / value.lstrip('/')), patch.object(self.module, 'run', side_effect=fake_run):
                    if fail:
                        with self.assertRaises(RuntimeError): self.module.add_address('1.1.1.1')
                        self.assertFalse(file.exists())
                        self.assertEqual(applied, [])
                    else:
                        self.assertTrue(self.module.add_address('1.1.1.1'))
                        saved = json.loads(file.read_text())
                        self.assertEqual(saved['network']['ethernets']['primary'], {'addresses':['1.1.1.1/32']})
                        self.assertFalse(self.module.add_address('1.1.1.1'))
                    self.assertFalse(any('default' in args and 'add' in args for args in calls))


class ExporterTests(unittest.TestCase):
    def test_only_xray_inbound_connections_and_local_asn(self):
        data = dict(xray_exporter_asn_path='/unused', xray_exporter_interval=15,
                    xray_exporter_max_series=10000, xray_exporter_port=9188,
                    xray_exporter_listen='127.0.0.1')
        source = render('roles/xray_exporter/templates/xray-exporter.py.j2', data)
        # The test exercises observation and ASN lookup independently of the HTTP library.
        prometheus = types.ModuleType('prometheus_client')
        prometheus.start_http_server = lambda *a, **k: None
        prometheus.REGISTRY = None
        core = types.ModuleType('prometheus_client.core')
        core.GaugeMetricFamily = object
        namespace = {'__name__': 'observer_test'}
        with patch.dict(sys.modules, {'prometheus_client':prometheus, 'prometheus_client.core':core}):
            exec(compile(source, '<exporter>', 'exec'), namespace)
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder)/'asn.gz'
            with gzip.open(database, 'wt') as stream:
                stream.write('134744064\t134744319\t15169\tUS\tGoogle LLC\n')
            config = Path(folder)/'config.json'
            config.write_text(json.dumps({'inbounds':[{'protocol':'vless','port':8444}]}))
            namespace['ASN_PATH'], namespace['CONFIG'] = database, config
            observer = namespace['Observer']()
            sockets = '\n'.join([
                '0 0 1.1.1.1:8444 8.8.8.8:12345 users:(("xray",pid=123,fd=4))',
                '0 0 1.1.1.1:8444 8.8.8.8:12346 users:(("xray",pid=123,fd=5))',
                '0 0 1.1.1.1:55000 9.9.9.9:443 users:(("xray",pid=123,fd=6))',
                '0 0 1.1.1.1:8444 9.9.9.9:12345 users:(("other",pid=456,fd=7))',
                '0 0 [::ffff:1.1.1.1]:8444 [::ffff:8.8.8.8]:12347 users:(("xray",pid=123,fd=8))'])
            def output(args, **kwargs):
                return '123\n' if args[0]=='systemctl' else sockets
            with patch('subprocess.check_output', side_effect=output):
                counts, total, dropped = observer.sample()
            self.assertEqual(total, 3)
            self.assertEqual(dropped, 0)
            self.assertEqual(counts, {('8.8.8.8','15169','Google LLC','8444'):3})
            self.assertEqual(observer.provider('9.9.9.9'), ('unknown','unknown'))
            self.assertEqual(observer.provider('2001:4860::1'), ('unknown','unknown'))


@unittest.skipUnless(os.name == 'posix' and shutil.which('bash'), 'Linux Bash required')
class WrapperTests(unittest.TestCase):
    def test_phase_isolation_and_successful_decline(self):
        for phase in ['all', 'core', 'monitoring', 'exporter']:
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                shutil.copytree(ROOT/'scripts', root/'scripts')
                bin_dir = root/'bin'; bin_dir.mkdir()
                venv = root/'.venv/bin'; venv.mkdir(parents=True)
                log = root/'commands.log'
                scripts = {
                    bin_dir/'ssh-keygen': '#!/bin/bash\nexit 0\n',
                    bin_dir/'ssh': '''#!/bin/bash
echo "ssh $*" >> "$TEST_LOG"
if [[ "$*" == *'discover'* ]]; then
  echo '{"interface":"eth0","addresses":["8.8.8.8"],"state":{"vpn_client_count":2,"install_mtproto":false,"install_socks5":false,"vpn_disable_ipv6":true}}'
elif [[ "$*" == *'monitoring-settings'* ]]; then echo '{}'
elif [[ "$*" == *'/amnezia-vpn-links.txt'* ]]; then echo 'AMNEZIA-SECRET-MUST-STAY-IN-FILE'
else echo 'client-artifact'; fi
''',
                    venv/'ansible': '#!/bin/bash\nexit 0\n',
                    venv/'ansible-playbook': '#!/bin/bash\necho "ansible $*" >> "$TEST_LOG"\n',
                }
                for path, text in scripts.items():
                    path.write_text(text); path.chmod(0o755)
                env = os.environ | {'VPN_NONINTERACTIVE':'1', 'PATH':str(bin_dir)+os.pathsep+os.environ['PATH'], 'TEST_LOG':str(log),
                    'VPN_INSTALL_MONITORING':'no', 'VPN_DISABLE_SSH_PASSWORD':'no', 'VPN_MAIN_IP':'9.9.9.9',
                    'VPN_TELEGRAM_ALERTS':'false', 'VPN_GRAFANA_ADMIN_PASSWORD':'test-password'}
                result = subprocess.run(['bash', str(root/'scripts/deploy-server'), '--phase', phase, 'main', '8.8.8.8'], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                commands = log.read_text()
                self.assertEqual('playbooks/vpn-core.yml' in commands, phase in ('core','all'))
                self.assertEqual('playbooks/monitoring.yml' in commands, phase == 'monitoring')
                self.assertEqual('playbooks/xray-exporter.yml' in commands, phase == 'exporter')
                if phase in ('core','all'):
                    self.assertTrue((root/'exports/8.8.8.8/amnezia-vpn-links.txt').exists())
                    self.assertIn('client-artifact', result.stdout)
                    self.assertNotIn('AMNEZIA-SECRET-MUST-STAY-IN-FILE', result.stdout)
                    self.assertIn(str(root/'exports/8.8.8.8/amnezia-vpn-links.txt'), result.stdout)
                    self.assertIn(str(root/'exports/8.8.8.8/amneziawg-client-configs.txt'), result.stdout)
                    self.assertEqual((root/'exports/8.8.8.8/amnezia-vpn-links.txt').read_text().strip(), 'AMNEZIA-SECRET-MUST-STAY-IN-FILE')
                else:
                    self.assertFalse((root/'exports').exists())


if __name__ == '__main__': unittest.main()
