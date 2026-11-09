"""Exercise the installed Ansible UFW command builder without touching a firewall."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

import yaml


MODULE = Path.home() / '.ansible/collections/ansible_collections/community/general/plugins/modules/ufw.py'
ROOT = Path(__file__).resolve().parents[1]


class ModuleFinished(Exception):
    pass


@unittest.skipUnless(MODULE.exists() and importlib.util.find_spec('ansible'), 'Installed Ansible collection required')
class UfwInsertionTests(unittest.TestCase):
    def test_ipv6_position_with_real_ansible_module(self):
        spec = importlib.util.spec_from_file_location('tested_ufw', MODULE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        tasks = yaml.safe_load((ROOT / 'roles/firewall/tasks/main.yml').read_text())
        task = next(task['community.general.ufw'] for task in tasks
                    if task['name'].startswith('Block tunneled IPv6'))
        base = task | {'interface_in':'awg0', 'delete':False}
        fixtures = [
            ('', 'route deny'),
            ('[ 1] 22/tcp ALLOW IN Anywhere\n[ 2] 443/udp ALLOW IN Anywhere', 'route deny'),
            ('[ 1] 22/tcp (v6) ALLOW IN Anywhere (v6)', 'route insert 1 deny'),
            ('[ 1] 22/tcp ALLOW IN Anywhere\n[ 2] 443/udp ALLOW IN Anywhere\n'
             '[ 3] Anywhere (v6) ALLOW FWD fd00:8::/64', 'route insert 3 deny'),
        ]
        for numbered, expected in fixtures:
            for delete in [False, True]:
                with self.subTest(numbered=numbered, delete=delete):
                    commands = []

                    class FakeModule:
                        check_mode = False

                        def __init__(self, argument_spec, **kwargs):
                            self.params = {key: value.get('default') for key, value in argument_spec.items()}
                            self.params.update(base | {'delete':delete})

                        def boolean(self, value):
                            return bool(value)

                        def get_bin_path(self, name, required=False):
                            return '/usr/sbin/ufw' if name == 'ufw' else '/usr/bin/grep'

                        def run_command(self, command, **kwargs):
                            text = command if isinstance(command, str) else ' '.join(command)
                            if text.endswith('status numbered'):
                                return 0, numbered, ''
                            if text.endswith('--version'):
                                return 0, 'ufw 0.36.2\n', ''
                            if ' route ' in text:
                                commands.append(text)
                                # Invalid insertion must fail as it does in UFW, not be ignored.
                                suffix = 'route delete deny' if delete else expected
                                self.assert_command(text, suffix)
                            return 0, 'Status: active\n' if 'status' in text else '', ''

                        @staticmethod
                        def assert_command(text, suffix):
                            expected_command = '/usr/sbin/ufw ' + suffix + ' in on awg0 from ::/0 to ::/0'
                            if text != expected_command:
                                raise AssertionError(f'{text!r} != {expected_command!r}')

                        def fail_json(self, **kwargs):
                            raise AssertionError(kwargs)

                        def exit_json(self, **kwargs):
                            raise ModuleFinished()

                    with patch.object(module, 'AnsibleModule', FakeModule), self.assertRaises(ModuleFinished):
                        module.main()
                    self.assertEqual(len(commands), 1)
