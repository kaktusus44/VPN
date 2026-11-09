#!/usr/bin/env python3
"""Interactive controller for the vpn_readdress role."""
import json
import os
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
        with open('/dev/tty', 'r+') as terminal:
            terminal.write(prompt + (f' [{default}]' if default else '') + ': ')
            terminal.flush()
            return terminal.readline().strip() or default
    except OSError:
        return default


def main():
    args = sys.argv[1:]
    role = args[0] if args and args[0] in ['main','reserve'] else ''
    address = args[1] if role and len(args) > 1 else (args[0] if args and not role else '')
    role = role or ask('Роль сервера main/reserve', 'main')
    if role not in ['main','reserve']:
        raise ValueError('Роль должна быть main или reserve')
    address = helper.public_ip(address or ask('Текущий доступный IPv4 для SSH'))
    old_management = helper.public_ip(os.environ.get('VPN_OLD_SSH_IPV4') or ask('Прежний IPv4 для SSH/мониторинга (если не менялся, Enter)', address))
    key = os.environ.get('VPN_SSH_KEY', str(Path.home()/'.ssh/vpn_ansible'))
    ssh = ['ssh','-i',key,'-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=10']
    inventory_path = ROOT/'inventories/managed'/old_management/'hosts.yml'
    inventory = yaml.safe_load(inventory_path.read_text()) if inventory_path.exists() else {
        'all':{'children':{'vpn_servers':{'hosts':{}}}}}
    entries = inventory['all']['children']['vpn_servers']['hosts']
    matches = [name for name, values in entries.items() if values.get('ansible_host') == old_management and values.get('vpn_server_role') == role]
    target = matches[0] if len(matches) == 1 else 'vpn-' + role
    if target in entries and entries[target].get('ansible_host') not in [old_management,address]:
        raise ValueError('Inventory содержит другой сервер с этим именем; укажите правильный прежний SSH IP')
    entries.setdefault(target, {}).update(ansible_host=address, ansible_user='root', ansible_port=22,
                                         vpn_server_role=role, vpn_public_endpoint=address)
    # If no saved peer is known, allow the same topology variables as deploy-server.
    for peer_role, env_name in [('main','VPN_MAIN_IP'),('reserve','VPN_RESERVE_IP')]:
        if peer_role != role and os.environ.get(env_name):
            peer = helper.public_ip(os.environ[env_name])
            entries['vpn-'+peer_role] = dict(ansible_host=peer, ansible_user='root', ansible_port=22,
                                             vpn_server_role=peer_role, vpn_public_endpoint=peer)

    def known_host(host):
        if subprocess.run(['ssh-keygen','-F',host], stdout=subprocess.DEVNULL).returncode != 0:
            directory = Path.home()/'.ssh'
            directory.mkdir(mode=0o700, exist_ok=True)
            scan = subprocess.check_output(['ssh-keyscan','-T','5','-H',host])
            with (directory/'known_hosts').open('ab') as stream:
                stream.write(scan)

    known_host(address)
    snapshot = json.loads(subprocess.check_output(ssh + ['root@'+address,'python3 - inspect'],
                                                  input=(ROOT/'scripts/vpn-readdress.py').read_bytes()))
    print('Текущий входной IP:', snapshot['entry'])
    print('Текущий выходной IP:', snapshot['exit'] or 'auto (выбор системой)')
    print('Публичные IPv4 в ОС:', ', '.join(snapshot['addresses']))
    default_entry = snapshot['entry'] if snapshot['entry'] in snapshot['addresses'] else address
    entry = helper.public_ip(os.environ.get('VPN_NEW_ENTRY_IPV4') or ask('Новый входной IPv4', default_entry))
    exit_ip = os.environ.get('VPN_NEW_EXIT_IPV4') or ask('Новый выходной IPv4 (auto — выбор системой)', snapshot['exit'] or 'auto')
    exit_ip = '' if exit_ip == 'auto' else helper.public_ip(exit_ip)
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
            result = subprocess.run(ssh+['root@'+address,'cat /var/lib/vpn/'+name], capture_output=True)
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
                    known_host(values['ansible_host'])
            subprocess.run(command+['--tags','peers'], env=environment, check=True, cwd=ROOT)
            if len(entries) == 1:
                print('В inventory нет второй ноды; при её наличии повторите команду с VPN_MAIN_IP или VPN_RESERVE_IP для обновления мониторинга.')
    print('Адреса обновлены. Ключи и UUID сохранены.')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print('Смена IP не завершена: '+str(error), file=sys.stderr)
        sys.exit(1)
