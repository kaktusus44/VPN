import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'posix' and shutil.which('bash'), 'Linux Bash required')
class BootstrapRotationTests(unittest.TestCase):
    def test_pipe_bootstrap_dispatches_rotation_and_preserves_exit_status(self):
        for exit_code in (0, 7):
            with self.subTest(exit_code=exit_code), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                repo = root / 'VPN'
                binaries = root / 'bin'
                binaries.mkdir()
                (repo / '.git').mkdir(parents=True)
                (repo / 'scripts').mkdir()
                venv = repo / '.venv/bin'
                venv.mkdir(parents=True)
                key = root / '.ssh/custom-key'
                key.parent.mkdir()
                key.write_text('test-placeholder')
                key.with_suffix('.pub').write_text('public-test-placeholder')
                log = root / 'calls'
                mocks = {
                    binaries / 'sudo': '#!/bin/bash\n[[ "$*" == -v ]] || exit 99\n',
                    binaries / 'dpkg-query': '#!/bin/bash\necho "ok installed"\n',
                    binaries / 'git': '#!/bin/bash\necho "git $*" >> "$TEST_LOG"\n',
                    binaries / 'ssh': '#!/bin/bash\nexit 99\n',
                    binaries / 'apt-get': '#!/bin/bash\nexit 99\n',
                    venv / 'python': '#!/bin/bash\nexit 0\n',
                    venv / 'ansible': '#!/bin/bash\necho "ansible [core 2.17.0]"\n',
                    venv / 'ansible-galaxy': '#!/bin/bash\nprintf "ansible.posix 1.0\\ncommunity.general 1.0\\n"\n',
                    repo / 'scripts/deploy-server': '#!/bin/bash\necho DEPLOY >> "$TEST_LOG"\nexit 99\n',
                    repo / 'scripts/rotate-endpoint': '#!/bin/bash\nprintf "rotate %s key=%s\\n" "$*" "$VPN_SSH_KEY" >> "$TEST_LOG"\nexit "$TEST_EXIT"\n',
                }
                for path, text in mocks.items():
                    path.write_text(text)
                    path.chmod(0o755)
                env = os.environ | {
                    'HOME': str(root), 'VPN_DIR': str(repo), 'VPN_SSH_KEY': str(key),
                    'PATH': str(binaries) + os.pathsep + os.environ['PATH'],
                    'TEST_LOG': str(log), 'TEST_EXIT': str(exit_code),
                }
                # Feed the downloaded script via stdin exactly as wget | bash -s.
                result = subprocess.run(
                    ['bash', '-s', '--', '--rotate', 'main', '8.8.8.8'],
                    input=(ROOT / 'scripts/bootstrap-ubuntu-control').read_text(),
                    env=env, capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, exit_code, result.stderr)
                calls = log.read_text()
                self.assertIn('pull --ff-only', calls)
                self.assertIn(f'rotate main 8.8.8.8 key={key}', calls)
                self.assertNotIn('DEPLOY', calls)

    def test_unknown_option_fails_before_preparing_machine(self):
        result = subprocess.run(
            ['bash', str(ROOT / 'scripts/bootstrap-ubuntu-control'), '--unknown'],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn('Использование', result.stderr)
