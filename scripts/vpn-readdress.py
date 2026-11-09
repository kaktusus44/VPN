#!/usr/bin/env python3
"""Change addresses of an existing managed VPN without installing or rotating keys."""
import argparse
import ast
import base64
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import zlib

STATE = Path('/var/lib/vpn')
AWG = Path('/etc/amnezia/amneziawg/awg0.conf')
XRAY = Path('/usr/local/etc/xray/config.json')
GENERATOR = Path('/usr/local/sbin/vpn-generate-amnezia-clients')
UNBOUND = Path('/etc/unbound/unbound.conf.d/vpn.conf')
GRAFANA = Path('/etc/grafana/grafana.ini')
NGINX = Path('/etc/nginx/sites-available')
PROMETHEUS = Path('/etc/prometheus/prometheus.yml')


def run(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT, timeout=30)


def public_ip(value):
    address = ipaddress.IPv4Address(value)
    if not address.is_global or address.is_multicast:
        raise ValueError('Expected a public unicast IPv4: ' + value)
    return str(address)


def inspect():
    state = json.loads((STATE/'deployment.json').read_text()) if (STATE/'deployment.json').exists() else {}
    client = (STATE/'client-001.awg.conf').read_text()
    entry = re.search(r'^Endpoint\s*=\s*([^:\s]+):', client, re.M).group(1)
    config = json.loads(XRAY.read_text())
    direct = next(item for item in config['outbounds'] if item.get('tag') == 'direct' and item['protocol'] == 'freedom')
    if not state:
        constants = {node.targets[0].id:node.value.value for node in ast.walk(ast.parse(GENERATOR.read_text()))
                     if isinstance(node, ast.Assign) and len(node.targets) == 1
                     and isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.Constant)}
        state = {'vpn_client_count':constants['CLIENT_COUNT'],
                 'vpn_disable_ipv6':constants.get('BLOCK_IPV6', False),
                 'install_mtproto':(STATE/'mtproto-secret').exists(),
                 'install_socks5':(STATE/'socks5-password').exists()}
    addresses = sorted({item['local'] for link in json.loads(run('ip', '-j', '-4', 'address', 'show'))
                        if link['ifname'] != 'lo' for item in link.get('addr_info', [])
                        if ipaddress.IPv4Address(item['local']).is_global
                        and not ipaddress.IPv4Address(item['local']).is_multicast})
    return {'entry':entry, 'exit':direct.get('sendThrough', ''), 'addresses':addresses,
            'state':state}


def endpoint(text, entry):
    return re.sub(r'^(Endpoint[ \t]*=[ \t]*)[^:\s]+(:\d+)[ \t]*$',
                  lambda match: match[1] + entry + match[2], text, flags=re.M)


def vpn_link(match, entry):
    token = match.group()[6:]
    packed = base64.urlsafe_b64decode(token + '=' * (-len(token) % 4))
    data = json.loads(zlib.decompress(packed[4:]))
    data['hostName'] = entry
    for container in data.get('containers', []):
        awg = container.get('awg', {})
        if 'last_config' in awg:
            last = json.loads(awg['last_config'])
            last['hostName'] = entry
            last['config'] = endpoint(last['config'], entry)
            awg['last_config'] = json.dumps(last, separators=(',', ':'))
    raw = json.dumps(data, separators=(',', ':')).encode()
    packed = len(raw).to_bytes(4, 'big') + zlib.compress(raw)
    return 'vpn://' + base64.urlsafe_b64encode(packed).decode().rstrip('=')


def nat(text, exit_ip):
    target = 'SNAT --to-source ' + exit_ip if exit_ip else 'MASQUERADE'
    return re.sub(r'-j (?:MASQUERADE|SNAT --to-source [^\s;"\']+)', '-j ' + target, text)


def generator(source, entry, exit_ip):
    """Edit only baked addresses/NAT strings; preserve counts, flags, ports and paths."""
    tree = ast.parse(source)
    lines = source.encode().splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    edits = []
    names = set()
    for node in ast.walk(tree):
        value = None
        replacement = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in ['VPN_PUBLIC_ENDPOINT', 'ENDPOINT']:
                value = node.value
                if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                    raise ValueError('Unsupported generator address expression: ' + name)
                replacement = entry if name == 'VPN_PUBLIC_ENDPOINT' else entry + ':' + value.value.rsplit(':', 1)[1]
                names.add(name)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith(' -j '):
            updated = nat(node.value, exit_ip)
            if updated != node.value:
                value, replacement = node, updated
        if value is not None and replacement is not None and replacement != value.value:
            edits.append((offsets[value.lineno-1]+value.col_offset,
                          offsets[value.end_lineno-1]+value.end_col_offset, repr(replacement).encode()))
    if names != {'VPN_PUBLIC_ENDPOINT', 'ENDPOINT'}:
        raise ValueError('Unsupported Amnezia generator; cannot safely update its endpoint')
    result = source.encode()
    for start, end, replacement in sorted(edits, reverse=True):
        result = result[:start] + replacement + result[end:]
    compile(result, str(GENERATOR), 'exec')
    return result.decode()


def atomic(path, content, mode=None):
    mode = mode if mode is not None else (path.stat().st_mode & 0o777)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            temporary.chmod(mode)
            if path.exists() and hasattr(os, 'chown'):
                metadata = path.stat()
                os.chown(temporary, metadata.st_uid, metadata.st_gid)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def plan(entry, exit_ip, old_management='', new_management='', monitor_only=False):
    changes = {}

    def update(path, content):
        if path.exists() and content != path.read_text():
            changes[path] = content.encode()

    if not monitor_only:
        current = inspect()
        public_ip(entry)
        if entry not in current['addresses'] or (exit_ip and public_ip(exit_ip) not in current['addresses']):
            raise ValueError('Selected IPv4 is not configured in the OS')
        if exit_ip:
            run('ip', '-4', 'route', 'get', '1.1.1.1', 'from', exit_ip)
        # Refuse incomplete installations instead of creating new identities.
        for path in [AWG, XRAY, GENERATOR, STATE/'awg0.server.key', STATE/'awg0.obfuscation.json',
                     STATE/'client-001.xray.uuid', STATE/'xray-reality.keys', STATE/'xray-reality.shortid']:
            if not path.is_file():
                raise ValueError('Existing VPN artifact is missing: ' + str(path))
        update(GENERATOR, generator(GENERATOR.read_text(), entry, exit_ip))
        if exit_ip != current['exit']:
            server = AWG.read_text()
            if len(re.findall(r'^Post(?:Up|Down) = iptables .*POSTROUTING.*-j (?:MASQUERADE|SNAT)', server, re.M)) != 2:
                raise ValueError('Unsupported AWG NAT hooks; refusing to replace custom routing')
            update(AWG, '\n'.join(nat(line, exit_ip) if line.startswith(('PostUp = ', 'PostDown = ')) else line
                                  for line in server.split('\n')))
            config = json.loads(XRAY.read_text())
            direct = next(item for item in config['outbounds'] if item.get('tag') == 'direct' and item['protocol'] == 'freedom')
            if exit_ip:
                direct['sendThrough'] = exit_ip
            else:
                direct.pop('sendThrough', None)
            update(XRAY, json.dumps(config, indent=2) + '\n')
            if UNBOUND.exists():
                dns = re.sub(r'^[ \t]*outgoing-interface:.*\n?', '', UNBOUND.read_text(), flags=re.M)
                if exit_ip:
                    dns = dns.replace('server:\n', 'server:\n  outgoing-interface: ' + exit_ip + '\n', 1)
                update(UNBOUND, dns)
        if entry != current['entry']:
            for path in [*STATE.glob('client-*.awg.conf'), STATE/'amneziawg-client-configs.txt']:
                if path.exists():
                    update(path, endpoint(path.read_text(), entry))
            links = STATE/'amnezia-vpn-links.txt'
            if links.exists():
                update(links, re.sub(r'vpn://\S+', lambda match: vpn_link(match, entry), links.read_text()))
            for path in [STATE/'client-links.txt', STATE/'xray-client-links.txt', STATE/'xray-experimental-client-links.txt']:
                if path.exists():
                    update(path, re.sub(r'(vless://[^@\s]+@)[^:\s]+(:\d+)',
                                        lambda match: match[1] + entry + match[2], path.read_text()))
            tun = STATE/'xray-tun-client.json'
            if tun.exists():
                config = json.loads(tun.read_text())
                for outbound in config.get('outbounds', []):
                    if outbound.get('type') == 'vless':
                        outbound['server'] = entry
                update(tun, json.dumps(config, indent=2) + '\n')
            telegram = STATE/'telegram-proxy-links.txt'
            if telegram.exists():
                update(telegram, re.sub(r'(?<=server=)[^&\s]+', entry, telegram.read_text()))
        saved = current['state'] | {'vpn_public_endpoint':entry, 'vpn_exit_ipv4':exit_ip}
        path = STATE/'deployment.json'
        encoded = (json.dumps(saved, indent=2) + '\n').encode()
        if not path.exists() or json.loads(path.read_text()) != saved:
            changes[path] = encoded
    if old_management and new_management and old_management != new_management:
        public_ip(old_management)
        public_ip(new_management)
        if not monitor_only:
            for path in [GRAFANA, *[NGINX/name for name in ['grafana.conf','alertmanager.conf','prometheus.conf']]]:
                if path.exists():
                    # Only address-bearing directives in our managed files.
                    text = re.sub(r'^(\s*(?:domain\s*=|root_url\s*=|server_name\s+).*)$',
                                  lambda match: re.sub(r'(?<![\d.])' + re.escape(old_management) + r'(?![\d.])',
                                                       new_management, match[0]), path.read_text(), flags=re.M)
                    update(path, text)
        path = PROMETHEUS
        if path.exists():
            text = re.sub(r'(?<![\d.])' + re.escape(old_management) + r'(?=:(?:9100|9187|9188)\b)',
                          new_management, path.read_text())
            update(path, text)
    return changes


def apply(changes):
    if not changes:
        return {'changed':False, 'files':[], 'restarted':[]}
    backup = STATE/'ip-change-backups'/str(time.time_ns())
    backup.mkdir(parents=True, mode=0o700)
    originals = {path:(path.read_bytes() if path.exists() else None,
                       path.stat().st_mode & 0o777 if path.exists() else 0o600) for path in changes}
    for path, (content, mode) in originals.items():
        if content is not None:
            dest = backup/path.as_posix().lstrip('/')
            dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            dest.write_bytes(content)
            dest.chmod(0o600)
    services = []
    if AWG in changes:
        services.append('awg-quick@awg0')
    if XRAY in changes:
        services.append('xray')
    if UNBOUND in changes:
        services.append('unbound')
    if GRAFANA in changes:
        services.append('grafana-server')
    if any(path.parent == NGINX for path in changes):
        services.append('nginx')
    if PROMETHEUS in changes:
        services.append('prometheus')
    awg_stopped = False
    touched = False
    try:
        # Validate candidate files before touching running services or their files.
        for path, command in [(XRAY, ['/usr/local/bin/xray','run','-test','-config']),
                              (UNBOUND, ['/usr/sbin/unbound-checkconf']),
                              (PROMETHEUS, ['/usr/bin/promtool','check','config'])]:
            if path in changes:
                with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(changes[path])
                try:
                    run(*command, str(temporary))
                finally:
                    temporary.unlink(missing_ok=True)
        if AWG in changes:
            run('systemctl','stop','awg-quick@awg0')
            awg_stopped = True
            touched = True
        for path, content in changes.items():
            atomic(path, content, originals[path][1])
            touched = True
        if 'nginx' in services:
            run('/usr/sbin/nginx','-t')
        for service in services:
            operation = 'start' if service == 'awg-quick@awg0' else ('reload' if service in ['nginx','prometheus'] else 'restart')
            run('systemctl', operation, service)
            run('systemctl', 'is-active', service)
    except Exception:
        if not touched:
            raise
        if awg_stopped:
            subprocess.run(['systemctl','stop','awg-quick@awg0'], check=False, capture_output=True)
        for path, (content, mode) in originals.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                atomic(path, content, mode)
        for service in services:
            try:
                run('systemctl','reset-failed',service)
                run('systemctl','start' if service == 'awg-quick@awg0' else 'restart',service)
            except Exception as error:
                print('Rollback service recovery failed for ' + service + ': ' + str(error), file=__import__('sys').stderr)
        raise
    return {'changed':True, 'files':[str(path) for path in changes], 'restarted':services, 'backup':str(backup)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['inspect','apply','monitor'])
    parser.add_argument('--entry', default='')
    parser.add_argument('--exit', default='')
    parser.add_argument('--old-management', default='')
    parser.add_argument('--new-management', default='')
    args = parser.parse_args()
    if args.action == 'inspect':
        result = inspect()
    else:
        result = apply(plan(args.entry, args.exit, args.old_management, args.new_management, args.action == 'monitor'))
    print(json.dumps(result))
