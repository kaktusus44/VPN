"""Exercise the real interactive controller with piped bootstrap stdin and a PTY."""
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'posix', 'Linux terminal required')
class RotationTerminalTests(unittest.TestCase):
    def test_pipe_questions_and_independent_address_choices(self):
        import fcntl
        import pty
        import termios

        for choice in ('1', '2', '3'):
            with self.subTest(choice=choice), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                repo = root/'VPN'
                shutil.copytree(ROOT/'scripts', repo/'scripts')
                (repo/'.git').mkdir()
                binaries = root/'bin'
                binaries.mkdir()
                venv = repo/'.venv/bin'
                venv.mkdir(parents=True)
                (venv/'python').symlink_to(sys.executable)
                key = root/'.ssh/vpn_ansible' if choice == '2' else root/'key with spaces'
                key.parent.mkdir(parents=True, exist_ok=True)
                key.write_text('test-placeholder')
                expected_port = 22 if choice == '2' else 2222
                if choice == '1':
                    inventory = repo/'inventories/managed/8.8.8.8/hosts.yml'
                    inventory.parent.mkdir(parents=True)
                    inventory.write_text(json.dumps({'all': {'children': {'vpn_servers': {'hosts': {
                        'vpn-main': {'ansible_host': '8.8.8.8', 'vpn_server_role': 'main',
                                     'ansible_port': expected_port, 'ansible_ssh_private_key_file': str(key)}
                    }}}}}))
                log = root/'commands'
                result_file = root/'result.json'
                scripts = {
                    binaries/'sudo': '#!/bin/bash\nexit 97\n',
                    binaries/'git': '#!/bin/bash\nexit 0\n',
                    binaries/'ssh-keygen': '#!/bin/bash\nexit 0\n',
                    binaries/'ssh': '''#!/bin/bash
echo "$*" >> "$TEST_LOG"
if [[ "$*" == *inspect* ]]; then
echo '{"entry":"8.8.8.8","exit":"1.1.1.1","addresses":["8.8.8.8","1.1.1.1","9.9.9.9"]}'
else echo 'vless://test-export'; fi
''',
                    venv/'ansible-playbook': '#!'+sys.executable+'''
import json, os, pathlib, sys
args = sys.argv
variables = json.loads(pathlib.Path(args[args.index('-e')+1][1:]).read_text())
inventory = json.loads(pathlib.Path(args[args.index('-i')+1]).read_text())
pathlib.Path(os.environ['TEST_RESULT']).write_text(json.dumps([variables, inventory]))
''',
                }
                for path, source in scripts.items():
                    path.write_text(source)
                    path.chmod(0o755)
                env = {k:v for k,v in os.environ.items() if not k.startswith('VPN_')}
                env.update(HOME=str(root), VPN_DIR=str(repo), PATH=str(binaries)+':'+os.environ['PATH'],
                           TEST_LOG=str(log), TEST_RESULT=str(result_file), PYTHONUNBUFFERED='1')
                if choice == '3':
                    env.update(VPN_SSH_PORT=str(expected_port), VPN_SSH_KEY=str(key))
                master, slave = pty.openpty()

                def terminal_session():
                    os.setsid()
                    fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

                process = subprocess.Popen(['bash','-s','--','--rotate'], stdin=subprocess.PIPE,
                                           stdout=slave, stderr=slave, env=env, preexec_fn=terminal_session)
                os.close(slave)
                process.stdin.write((ROOT/'scripts/bootstrap-ubuntu-control').read_bytes())
                process.stdin.close()
                questions = [
                    ('Роль сервера', 'main'),
                    ('IPv4 сервера, на котором', '8.8.8.8'),
                    ('Прежний IPv4', ''),
                    ('Пользователь SSH', 'operator'),
                    ('Выберите 1/2/3', choice),
                ]
                if choice in ('1','3'):
                    questions.append(('Новый IPv4 для подключения', '9.9.9.9'))
                if choice in ('2','3'):
                    questions.append(('Новый IPv4 для выхода', '2.2.2.2'))
                    questions.append(('Он уже прикреплён у хостера', 'y'))
                output = b''
                next_question = 0
                deadline = time.monotonic()+20
                try:
                    while time.monotonic() < deadline:
                        if select.select([master], [], [], 0.1)[0]:
                            try:
                                chunk = os.read(master, 65536)
                            except OSError:
                                break
                            if not chunk:
                                break
                            output += chunk
                            if next_question < len(questions):
                                prompt, answer = questions[next_question]
                                if prompt in output.decode(errors='replace'):
                                    os.write(master, (answer+'\n').encode())
                                    next_question += 1
                        if process.poll() is not None:
                            break
                    process.wait(timeout=2)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
                    os.close(master)
                text = output.decode(errors='replace')
                self.assertEqual(process.returncode, 0, text)
                self.assertEqual(next_question, len(questions), text)
                variables, inventory = json.loads(result_file.read_text())
                rotation = variables['vpn_rotation']
                self.assertEqual(rotation['entry'], '9.9.9.9' if choice in ('1','3') else '8.8.8.8')
                self.assertEqual(rotation['exit'], '2.2.2.2' if choice in ('2','3') else '1.1.1.1')
                self.assertEqual(rotation['add_addresses'], ['2.2.2.2'] if choice in ('2','3') else [])
                host = inventory['all']['children']['vpn_servers']['hosts']['vpn-main']
                self.assertEqual(host['ansible_user'], 'operator')
                self.assertEqual(host['ansible_port'], expected_port)
                self.assertEqual(host['ansible_ssh_private_key_file'], str(key))
                self.assertIn('operator@8.8.8.8 sudo -n python3 - inspect', log.read_text())
                self.assertNotIn('СКОПИРУЙТЕ', text)
                self.assertNotIn('Порт SSH [', text)
                self.assertNotIn('Путь к существующему', text)
