#!/usr/bin/env python3
"""Discover public IPv4s and add a provider-attached /32 without changing routes."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import time
import yaml


def run(*args):
    return subprocess.check_output(args, text=True)


def public_ipv4(value):
    ip = ipaddress.IPv4Address(value)
    if not ip.is_global or ip.is_multicast:
        raise ValueError(f"Not a public unicast IPv4: {value}")
    return str(ip)


def default_routes():
    routes = json.loads(run('ip', '-j', '-4', 'route', 'show', 'default'))
    # DHCP lifetime counters and output order are not routing changes.
    return sorted(({key: value for key, value in route.items() if key != 'expires'}
                   for route in routes), key=lambda value: json.dumps(value, sort_keys=True))


def discover():
    routes = json.loads(run("ip", "-j", "-4", "route", "show", "default"))
    if not routes or not routes[0].get("dev"):
        raise RuntimeError("No primary IPv4 default route")
    interface = routes[0]["dev"]
    addresses = []
    for link in json.loads(run("ip", "-j", "-4", "address", "show")):
        if link["ifname"] == "lo":
            continue
        for addr in link.get("addr_info", []):
            try:
                addresses.append(public_ipv4(addr["local"]))
            except ValueError:
                pass
    state = Path("/var/lib/vpn/deployment.json")
    saved = json.loads(state.read_text()) if state.exists() else {}
    # Upgrade existing deployments without silently removing peers or optional proxies.
    awg = Path('/etc/amnezia/amneziawg/awg0.conf')
    if not saved and awg.exists():
        text = awg.read_text()
        saved = {'vpn_client_count': text.count('[Peer]'),
                 'install_mtproto': Path('/var/lib/vpn/mtproto-secret').exists(),
                 'install_socks5': Path('/var/lib/vpn/socks5-password').exists()}
        client = Path('/var/lib/vpn/client-001.awg.conf')
        if client.exists():
            for line in client.read_text().splitlines():
                if line.startswith('Endpoint = '):
                    saved['vpn_public_endpoint'] = line.split('=', 1)[1].strip().rsplit(':', 1)[0]
    return {"interface": interface, "addresses": sorted(set(addresses)),
            "state": saved}


def add_address(address):
    address = public_ipv4(address)
    before = discover()
    if address in before["addresses"]:
        return False
    interface = before["interface"]
    # Use the existing Netplan ID (which may differ from the kernel interface name).
    config = yaml.safe_load(run("netplan", "get"))
    ethernets = config.get("network", {}).get("ethernets", {})
    candidates = [key for key, val in ethernets.items()
                  if key == interface or val.get("set-name") == interface]
    if len(candidates) != 1:
        raise RuntimeError("Cannot identify primary Netplan ethernet ID safely; configure the IP manually")
    netplan_id = candidates[0]
    path = Path("/etc/netplan/99-vpn-extra-ip.yaml")
    original = path.read_bytes() if path.exists() else None
    routes = default_routes()
    # JSON is valid YAML; Netplan merges address sequences with existing files.
    extra = {"network": {"version": 2, "ethernets": {
        netplan_id: {"addresses": [address + "/32"]}}}}
    if original:
        extra = json.loads(original)
        entries = extra["network"]["ethernets"][netplan_id]["addresses"]
        if address + "/32" not in entries:
            entries.append(address + "/32")
    try:
        path.touch(mode=0o600, exist_ok=True)
        path.chmod(0o600)
        path.write_text(json.dumps(extra, indent=2) + "\n")
        run("netplan", "generate")
        # Reconfigure only the affected networkd link, avoiding a global netplan apply.
        renderer = ethernets[netplan_id].get("renderer", config["network"].get("renderer", "networkd"))
        if renderer != "networkd":
            raise RuntimeError("Automatic extra IP requires Netplan/networkd")
        run("networkctl", "reload")
        run("networkctl", "reconfigure", interface)
        for _ in range(10):
            if address in discover()["addresses"]:
                break
            time.sleep(0.5)
        if address not in discover()["addresses"]:
            raise RuntimeError("Extra address did not appear")
        if routes != default_routes():
            raise RuntimeError("Default route changed")
        run("ip", "-4", "route", "get", "1.1.1.1", "from", address)
    except Exception:
        if original is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(original)
        run("netplan", "generate")
        run("networkctl", "reload")
        run("networkctl", "reconfigure", interface)
        raise
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["discover", "add"])
    parser.add_argument("address", nargs="?")
    args = parser.parse_args()
    if args.action == "add":
        print(json.dumps({"changed": add_address(args.address)}))
    else:
        print(json.dumps(discover()))
