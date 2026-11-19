#!/usr/bin/env python3
"""Interactive controller for the vpn_readdress role."""
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile

import yaml
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('readdress', ROOT/'scripts/vpn-readdress.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def ask(prompt, default=''):
    if os.environ.get('VPN_NONINTERACTIVE') == '1':
        return default
    try:
        # TTY streams are not seekable: r+ can fail before displaying a prompt.
        with open('/dev/tty', 'w') as terminal, open('/dev/tty', 'r') as answers:
            terminal.write(prompt + (f' [{default}]' if default else '') + ': ')
            terminal.flush()
            answer = answers.readline()
            if not answer:
                raise RuntimeError('Ввод прерван. Изменения не применены.')
            return answer.strip() or default
    except OSError as error:
        raise RuntimeError('Нет интерактивного терминала. Запустите команду в терминале или задайте VPN_NONINTERACTIVE=1 и параметры через переменные окружения.') from error


def choose_addresses(snapshot):
    default = 'both'
    if os.environ.get('VPN_NEW_ENTRY_IPV4') and not os.environ.get('VPN_NEW_EXIT_IPV4'):
        default = 'entry'
    elif os.environ.get('VPN_NEW_EXIT_IPV4') and not os.environ.get('VPN_NEW_ENTRY_IPV4'):
        default = 'exit'
    print('\nЧто меняем?\n  1 — адрес подключения к VPN (входной)\n  2 — адрес, с которым VPN выходит в интернет (выходной)\n  3 — оба адреса', flush=True)
    selected = os.environ.get('VPN_ROTATE_WHAT') or ask('Выберите 1/2/3', {'entry':'1','exit':'2','both':'3'}[default])
    mode = {'1':'entry','2':'exit','3':'both','entry':'entry','exit':'exit','both':'both'}.get(selected)
    if mode is None:
        raise ValueError('Выберите 1 (входной), 2 (выходной) или 3 (оба)')
    entry, exit_ip = snapshot['entry'], snapshot['exit']
    if mode in ['entry','both']:
        entry = helper.public_ip(os.environ.get('VPN_NEW_ENTRY_IPV4') or ask('Новый IPv4 для подключения к VPN', entry))
    if mode in ['exit','both']:
        exit_ip = os.environ.get('VPN_NEW_EXIT_IPV4') or ask('Новый IPv4 для выхода в интернет (auto — выбор системой)', exit_ip or 'auto')
        exit_ip = '' if exit_ip == 'auto' else helper.public_ip(exit_ip)
    return entry, exit_ip


def main():
    args = sys.argv[1:]
    role = args[0] if args and args[0] in ['main','reserve'] else ''
    address = args[1] if role and len(args) > 1 else (args[0] if args and not role else '')
    role = role or ask('Роль сервера main/reserve', 'main')
    if role not in ['main','reserve']:
        raise ValueError('Роль должна быть main или reserve')
    address = address or os.environ.get('VPN_SSH_HOST') or ask('IPv4 сервера, на котором меняем VPN (доступный по SSH)')
    if not address:
        raise ValueError('Не указан IPv4 сервера для SSH')
    address = helper.public_ip(address)
    old_management = helper.public_ip(os.environ.get('VPN_OLD_SSH_IPV4') or ask('Прежний IPv4 для SSH/мониторинга (если не менялся, Enter)', address))
    inventory_path = ROOT/'inventories/managed'/old_management/'hosts.yml'
    inventory = yaml.safe_load(inventory_path.read_text()) if inventory_path.exists() else {
        'all':{'children':{'vpn_servers':{'hosts':{}}}}}
    entries = inventory['all']['children']['vpn_servers']['hosts']
    matches = [name for name, values in entries.items() if values.get('ansible_host') == old_management and values.get('vpn_server_role') == role]
    target = matches[0] if len(matches) == 1 else 'vpn-' + role
    if target in entries and entries[target].get('ansible_host') not in [old_management,address]:
        raise ValueError('Inventory содержит другой сервер с этим именем; укажите правильный прежний SSH IP')
    saved = entries.get(target, {})
    user = os.environ.get('VPN_SSH_USER') or ask('Пользователь SSH (для не-root нужен sudo без пароля)', saved.get('ansible_user', 'root'))
    if not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_.-]*\$?', user):
        raise ValueError('Некорректное имя пользователя SSH')
    port = int(os.environ.get('VPN_SSH_PORT') or ask('Порт SSH', str(saved.get('ansible_port', 22))))
    if not 1 <= port <= 65535:
        raise ValueError('Порт SSH должен быть от 1 до 65535')
    key = os.environ.get('VPN_SSH_KEY') or ask('Путь к существующему приватному SSH-ключу', saved.get('ansible_ssh_private_key_file', str(Path.home()/'.ssh/vpn_ansible')))
    key = str(Path(key).expanduser().resolve())
    if not Path(key).is_file():
        raise ValueError('SSH-ключ не найден: ' + key)
    ssh = ['ssh','-i',key,'-p',str(port),'-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=10']
    remote = user + '@' + address
    elevate = '' if user == 'root' else 'sudo -n '
    entries.setdefault(target, {}).update(ansible_host=address, ansible_user=user, ansible_port=port,
                                         ansible_ssh_private_key_file=key,
                                         vpn_server_role=role, vpn_public_endpoint=address)
    # If no saved peer is known, allow the same topology variables as deploy-server.
    for peer_role, env_name in [('main','VPN_MAIN_IP'),('reserve','VPN_RESERVE_IP')]:
        if peer_role != role and os.environ.get(env_name):
            peer = helper.public_ip(os.environ[env_name])
            entries['vpn-'+peer_role] = dict(ansible_host=peer, ansible_user='root', ansible_port=22,
                                             vpn_server_role=peer_role, vpn_public_endpoint=peer)

    def known_host(host, ssh_port=22):
        lookup = host if ssh_port == 22 else f'[{host}]:{ssh_port}'
        if subprocess.run(['ssh-keygen','-F',lookup], stdout=subprocess.DEVNULL).returncode != 0:
            directory = Path.home()/'.ssh'
            directory.mkdir(mode=0o700, exist_ok=True)
            scan = subprocess.check_output(['ssh-keyscan','-T','5','-p',str(ssh_port),'-H',host])
            with (directory/'known_hosts').open('ab') as stream:
                stream.write(scan)

    print(f'Подключаюсь к {remote}, SSH-порт {port}…', flush=True)
    known_host(address, port)
    snapshot = json.loads(subprocess.check_output(ssh + [remote,elevate+'python3 - inspect'],
                                                  input=(ROOT/'scripts/vpn-readdress.py').read_bytes()))
    print('Текущий входной IP:', snapshot['entry'])
    print('Текущий выходной IP:', snapshot['exit'] or 'auto (выбор системой)')
    print('Публичные IPv4 в ОС:', ', '.join(snapshot['addresses']))
    entry, exit_ip = choose_addresses(snapshot)
    additions = []
    explicit = {helper.public_ip(value.strip()) for value in os.environ.get('VPN_ADDITIONAL_IPV4', '').split(',') if value.strip()}
    for value in dict.fromkeys([entry, exit_ip]):
        if not value or value in snapshot['addresses']:
            continue
        if value not in explicit and ask(f'{value} не настроен в ОС. Он уже прикреплён у хостера и его нужно добавить как /32? y/N', 'n').lower() not in ['y','yes','да']:
            raise ValueError('Сначала прикрепите/настройте IPv4 или подтвердите его добавление: ' + value)
        additions.append(value)
    rotation = dict(target=target, target_role=role, entry=entry, exit=exit_ip,
                    old_management=old_management, add_addresses=additions)
    print(f'Вход: {snapshot["entry"]} → {entry}; выход: {snapshot["exit"] or "auto"} → {exit_ip or "auto"}')
    environment = os.environ | {'ANSIBLE_PRIVATE_KEY_FILE':key, 'ANSIBLE_CONFIG':str(ROOT/'ansible.cfg')}
    with tempfile.TemporaryDirectory(prefix='vpn-readdress-') as folder:
        inventory_file, variables_file = Path(folder)/'hosts.yml', Path(folder)/'vars.json'
        inventory_file.write_text(json.dumps(inventory))
        variables_file.write_text(json.dumps({'vpn_rotation':rotation}))
        inventory_file.chmod(0o600)
        variables_file.chmod(0o600)
        command = [str(ROOT/'.venv/bin/ansible-playbook'),'-i',str(inventory_file),
                   str(ROOT/'playbooks/rotate-endpoint.yml'),'-e','@'+str(variables_file)]
        subprocess.run(command + ['--tags','vpn-address'], env=environment, check=True, cwd=ROOT)
        destination = ROOT/'inventories/managed'/address/'hosts.yml'
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(inventory, indent=2)+'\n')
        destination.chmod(0o600)
        exports = ROOT/'exports'/address
        exports.mkdir(parents=True, exist_ok=True, mode=0o700)
        exports.chmod(0o700)
        names = ['amnezia-vpn-links.txt','amneziawg-client-configs.txt','client-links.txt',
                 'xray-client-links.txt','xray-experimental-client-links.txt','xray-tun-client.json',
                 'telegram-proxy-links.txt']
        for name in names:
            # Older installations may not have the optional TUN export yet.
            result = subprocess.run(ssh+[remote,elevate+'cat /var/lib/vpn/'+name], capture_output=True)
            if result.returncode:
                if name in ['xray-tun-client.json', 'telegram-proxy-links.txt']:
                    continue
                raise RuntimeError('Не удалось скопировать клиентский файл: '+name)
            with tempfile.NamedTemporaryFile(dir=exports, delete=False) as stream:
                temporary = Path(stream.name)
                temporary.chmod(0o600)
                stream.write(result.stdout)
            os.replace(temporary, exports/name)
        print('Ссылки AmneziaVPN:', exports/'amnezia-vpn-links.txt')
        print('Конфигурации AmneziaWG:', exports/'amneziawg-client-configs.txt')
        print('На VPS: /var/lib/vpn/amnezia-vpn-links.txt и /var/lib/vpn/amneziawg-client-configs.txt')
        for name in ['xray-client-links.txt','xray-experimental-client-links.txt']:
            print((exports/name).read_text().strip())
        if entry != snapshot['entry']:
            print('Входной IP изменён: импортируйте обновлённые профили/ссылки на клиентах.')
        if old_management != address:
            for name, values in entries.items():
                if name != target:
                    known_host(values['ansible_host'], int(values.get('ansible_port', 22)))
            subprocess.run(command+['--tags','peers'], env=environment, check=True, cwd=ROOT)
            if len(entries) == 1:
                print('В inventory нет второй ноды; при её наличии повторите команду с VPN_MAIN_IP или VPN_RESERVE_IP для обновления мониторинга.')
    print('Адреса обновлены. Ключи и UUID сохранены.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print('Смена IP не завершена: '+str(error), file=sys.stderr)
        sys.exit(1)
