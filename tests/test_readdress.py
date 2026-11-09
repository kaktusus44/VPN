import ast
import base64
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

from test_deployment import ROOT, render, variables


class ReaddressTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        root = Path(self.folder.name)
        spec = importlib.util.spec_from_file_location('readdress_test', ROOT/'scripts/vpn-readdress.py')
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        for name, relative in [('STATE','state'),('AWG','awg0.conf'),('XRAY','xray.json'),
                               ('GENERATOR','generator.py'),('UNBOUND','unbound.conf'),
                               ('GRAFANA','grafana.ini'),('NGINX','nginx'),('PROMETHEUS','prometheus.yml')]:
            setattr(self.module, name, root/relative)
        m = self.module
        m.STATE.mkdir()
        m.NGINX.mkdir()
        m.AWG.write_text('[Interface]\nPrivateKey = server-secret\nPostUp = iptables -t nat -A POSTROUTING -s 10.8.0.0/24 -o eth0 -j SNAT --to-source 1.1.1.1\nPostDown = iptables -t nat -D POSTROUTING -s 10.8.0.0/24 -o eth0 -j SNAT --to-source 1.1.1.1\n[Peer]\nPublicKey = unchanged\n')
        data = variables() | {'vpn_exit_ipv4':'1.1.1.1'}
        m.XRAY.write_text(render('roles/xray_reality/templates/config.json.j2', data))
        m.GENERATOR.write_text(render('roles/amneziawg/templates/generate-amnezia-clients.py.j2', data))
        m.UNBOUND.write_text('server:\n  outgoing-interface: 1.1.1.1\n  do-ip6: no\n')
        state = dict(vpn_public_endpoint='8.8.8.8', vpn_exit_ipv4='1.1.1.1', vpn_client_count=2,
                     vpn_disable_ipv6=True, install_mtproto=True, install_socks5=False)
        (m.STATE/'deployment.json').write_text(json.dumps(state))
        for name in ['awg0.server.key','awg0.obfuscation.json','client-001.xray.uuid','xray-reality.keys',
                     'xray-reality.shortid','client-001.key','client-002.key','mtproto-secret']:
            (m.STATE/name).write_text('secret-'+name)
        self.secrets = {path:path.read_bytes() for path in m.STATE.iterdir() if path.name != 'deployment.json'}
        client = '[Interface]\nPrivateKey = client-secret\n[Peer]\nEndpoint = 8.8.8.8:443\n'
        for name in ['client-001.awg.conf','client-002.awg.conf','amneziawg-client-configs.txt']:
            (m.STATE/name).write_text(client)
        last = {'config':client,'hostName':'8.8.8.8','client_priv_key':'client-secret','HeaderProtectionKey':'unchanged'}
        payload = {'hostName':'8.8.8.8','containers':[{'awg':{'last_config':json.dumps(last)}}]}
        raw = json.dumps(payload).encode()
        link = 'vpn://'+base64.urlsafe_b64encode(len(raw).to_bytes(4,'big')+zlib.compress(raw)).decode().rstrip('=')
        (m.STATE/'amnezia-vpn-links.txt').write_text('client-001 main amnezia_vpn '+link+'\n')
        for name in ['client-links.txt','xray-client-links.txt','xray-experimental-client-links.txt']:
            (m.STATE/name).write_text('vless://preserved-uuid@8.8.8.8:8444?security=reality\n')
        (m.STATE/'xray-tun-client.json').write_text(json.dumps({'outbounds':[{'type':'vless','server':'8.8.8.8','uuid':'preserved-uuid'}]}))
        (m.STATE/'telegram-proxy-links.txt').write_text('https://t.me/proxy?server=8.8.8.8&port=9444&secret=unchanged\n')
        m.GRAFANA.write_text('[server]\ndomain = 8.8.8.8\nroot_url = https://8.8.8.8:443/\n')
        m.GRAFANA.chmod(0o640)
        (m.NGINX/'grafana.conf').write_text('server {\n server_name 8.8.8.8;\n}\n')
        m.PROMETHEUS.write_text('scrape_configs:\n  - job_name: node\n    static_configs:\n      - targets: [8.8.8.8:9100, 18.8.8.8:9100]\n')
        self.commands = []
        self.run_patch = patch.object(m,'run',side_effect=self.fake_run)
        self.run_patch.start()
        self.addCleanup(self.run_patch.stop)

    def fake_run(self, *args):
        self.commands.append(args)
        if args[:4] == ('ip','-j','-4','address'):
            return json.dumps([{'ifname':'eth0','addr_info':[{'local':ip} for ip in ['8.8.8.8','1.1.1.1','9.9.9.9']]}])
        return 'active\n'

    def assert_secrets(self):
        for path, value in self.secrets.items():
            self.assertEqual(path.read_bytes(), value)

    def test_entry_only_preserves_services_keys_and_encoded_profiles(self):
        m = self.module
        changes = m.plan('9.9.9.9','1.1.1.1')
        self.assertNotIn(m.AWG, changes)
        self.assertNotIn(m.XRAY, changes)
        self.assertNotIn(m.UNBOUND, changes)
        result = m.apply(changes)
        self.assertEqual(result['restarted'], [])
        self.assertFalse(any(command[0]=='systemctl' for command in self.commands))
        link = (m.STATE/'amnezia-vpn-links.txt').read_text().split()[-1][6:]
        decoded = json.loads(zlib.decompress(base64.urlsafe_b64decode(link+'='*(-len(link)%4))[4:]))
        last = json.loads(decoded['containers'][0]['awg']['last_config'])
        self.assertEqual(decoded['hostName'],'9.9.9.9')
        self.assertEqual(last['client_priv_key'],'client-secret')
        self.assertIn('Endpoint = 9.9.9.9:443', last['config'])
        self.assertIn('preserved-uuid@9.9.9.9:8444', (m.STATE/'xray-client-links.txt').read_text())
        self.assertIn('server=9.9.9.9', (m.STATE/'telegram-proxy-links.txt').read_text())
        constants = {node.targets[0].id:node.value.value for node in ast.walk(ast.parse(m.GENERATOR.read_text()))
                     if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name) and isinstance(node.value,ast.Constant)}
        self.assertEqual(constants['ENDPOINT'],'9.9.9.9:443')
        self.assertEqual(constants['CLIENT_COUNT'],2)
        self.assertEqual(m.plan('9.9.9.9','1.1.1.1'), {})
        self.assert_secrets()

    def test_exit_only_restarts_changed_services_and_keeps_exports(self):
        m = self.module
        original = (m.STATE/'amnezia-vpn-links.txt').read_bytes()
        changes = m.plan('8.8.8.8','9.9.9.9')
        self.assertNotIn(m.STATE/'amnezia-vpn-links.txt',changes)
        self.assertIn(m.AWG,changes)
        result = m.apply(changes)
        self.assertEqual(result['restarted'],['awg-quick@awg0','xray','unbound'])
        self.assertIn('-j SNAT --to-source 9.9.9.9',m.AWG.read_text())
        config = json.loads(m.XRAY.read_text())
        self.assertEqual(config['outbounds'][0]['sendThrough'],'9.9.9.9')
        self.assertEqual(config['inbounds'][0]['settings']['clients'][0]['id'],'test-uuid')
        self.assertIn('outgoing-interface: 9.9.9.9',m.UNBOUND.read_text())
        self.assertEqual((m.STATE/'amnezia-vpn-links.txt').read_bytes(),original)
        self.assertIn(' -j SNAT --to-source 9.9.9.9', m.GENERATOR.read_text())
        self.assertEqual(m.plan('8.8.8.8','9.9.9.9'),{})
        self.assert_secrets()

    def test_auto_exit_removes_source_binding(self):
        m = self.module
        m.apply(m.plan('8.8.8.8',''))
        self.assertIn('-j MASQUERADE',m.AWG.read_text())
        self.assertNotIn('sendThrough',json.loads(m.XRAY.read_text())['outbounds'][0])
        self.assertNotIn('outgoing-interface',m.UNBOUND.read_text())
        self.assertEqual(m.plan('8.8.8.8',''),{})

    def test_invalid_address_or_config_never_restarts_services(self):
        m = self.module
        with self.assertRaises(ValueError): m.plan('2.2.2.2','1.1.1.1')
        changes = m.plan('8.8.8.8','9.9.9.9')
        originals = {path:path.read_bytes() for path in changes}
        def fail_validation(*args):
            self.commands.append(args)
            raise RuntimeError('invalid candidate')
        with patch.object(m,'run',side_effect=fail_validation), self.assertRaises(RuntimeError): m.apply(changes)
        self.assertFalse(any(command[0]=='systemctl' for command in self.commands))
        self.assertTrue(all(path.read_bytes()==value for path,value in originals.items()))

    def test_failed_restart_rolls_back_all_configurations(self):
        m = self.module
        changes = m.plan('9.9.9.9','9.9.9.9')
        originals = {path:path.read_bytes() for path in changes}
        failed = []
        def fail_once(*args):
            if args == ('systemctl','stop','awg-quick@awg0') and not failed:
                self.assertIn('SNAT --to-source 1.1.1.1',m.AWG.read_text())
            if args == ('systemctl','restart','xray') and not failed:
                failed.append(True)
                raise RuntimeError('startup failure')
            return self.fake_run(*args)
        with patch.object(m,'run',side_effect=fail_once), patch('subprocess.run'), self.assertRaises(RuntimeError):
            m.apply(changes)
        self.assertTrue(all(path.read_bytes()==value for path,value in originals.items()))
        self.assert_secrets()

    def test_management_change_updates_only_managed_address_references(self):
        m = self.module
        changes = m.plan('8.8.8.8','1.1.1.1','8.8.8.8','9.9.9.9')
        self.assertNotIn(m.XRAY,changes)
        original_group = m.GRAFANA.stat().st_gid
        m.apply(changes)
        self.assertIn('https://9.9.9.9:443',m.GRAFANA.read_text())
        self.assertIn('server_name 9.9.9.9;', (m.NGINX/'grafana.conf').read_text())
        self.assertIn('9.9.9.9:9100',m.PROMETHEUS.read_text())
        self.assertIn('18.8.8.8:9100',m.PROMETHEUS.read_text())
        self.assertEqual(m.GRAFANA.stat().st_gid,original_group)
        peer = m.plan('','','9.9.9.9','1.1.1.1',True)
        self.assertEqual(set(peer),{m.PROMETHEUS})


@unittest.skipUnless(os.name == 'posix' and shutil.which('bash'), 'Linux Bash required')
class ReaddressControllerTests(unittest.TestCase):
    def test_controller_preserves_peer_inventory_and_quiet_exports(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shutil.copytree(ROOT/'scripts',root/'scripts')
            tools = root/'bin'; tools.mkdir()
            venv = root/'.venv/bin'; venv.mkdir(parents=True)
            log = root/'log'
            ssh = '''#!/bin/bash
echo "$*" >> "$TEST_LOG"
if [[ "$*" == *'inspect'* ]]; then
echo '{"entry":"8.8.8.8","exit":"1.1.1.1","addresses":["8.8.8.8","9.9.9.9","1.1.1.1"]}'
elif [[ "$*" == *'amnezia-vpn-links.txt'* ]]; then echo 'AMNEZIA-SECRET'
else echo 'vless://test-link'; fi
'''
            for path,text in [(tools/'ssh',ssh),(tools/'ssh-keygen','#!/bin/bash\nexit 0\n'),
                              (venv/'ansible-playbook','#!/bin/bash\necho "ansible $*" >> "$TEST_LOG"\n')]:
                path.write_text(text); path.chmod(0o755)
            inventory = root/'inventories/managed/8.8.8.8/hosts.yml'
            inventory.parent.mkdir(parents=True)
            inventory.write_text(json.dumps({'all':{'children':{'vpn_servers':{'hosts':{
                'vpn-main':{'ansible_host':'8.8.8.8','vpn_server_role':'main'},
                'vpn-reserve':{'ansible_host':'1.1.1.1','vpn_server_role':'reserve'}}}}}}))
            env = os.environ | {'VPN_NONINTERACTIVE':'1','VPN_OLD_SSH_IPV4':'8.8.8.8',
                                'VPN_NEW_ENTRY_IPV4':'9.9.9.9','TEST_LOG':str(log),
                                'PATH':str(tools)+':'+os.environ['PATH']}
            result = subprocess.run([sys.executable,str(root/'scripts/rotate-endpoint.py'),'main','9.9.9.9'],
                                    env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertNotIn('AMNEZIA-SECRET',result.stdout)
            self.assertIn('vless://test-link',result.stdout)
            saved = json.loads((root/'inventories/managed/9.9.9.9/hosts.yml').read_text())
            hosts = saved['all']['children']['vpn_servers']['hosts']
            self.assertEqual(hosts['vpn-main']['ansible_host'],'9.9.9.9')
            self.assertEqual(hosts['vpn-reserve']['ansible_host'],'1.1.1.1')
            self.assertIn('--tags vpn-address',log.read_text())
            self.assertIn('--tags peers',log.read_text())
