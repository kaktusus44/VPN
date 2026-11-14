"""Failed helpers must retain stderr instead of failing JSON parsing."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('ansible-playbook'), 'Ansible required')
class NetworkTaskErrors(unittest.TestCase):
    def test_failed_helper_preserves_original_error(self):
        tasks = yaml.safe_load((ROOT / 'roles/vpn_network/tasks/main.yml').read_text())
        task = next(t for t in tasks if t.get('register') == 'vpn_extra_ip_result')
        task.pop('when')
        task['ansible.builtin.command'] = {
            'argv': ['/bin/sh', '-c', 'echo provider-address-error >&2; exit 1']}
        play = [{'hosts': 'localhost', 'gather_facts': False, 'become': False,
                 'tasks': [task]}]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'failure.yml'
            path.write_text(json.dumps(play))
            result = subprocess.run(
                ['ansible-playbook', '-i', 'localhost,', '-c', 'local', str(path)],
                text=True, capture_output=True)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 2, output)
        self.assertIn('provider-address-error', output)
        self.assertNotIn('JSONDecodeError', output)
        self.assertNotIn('Expecting value', output)
