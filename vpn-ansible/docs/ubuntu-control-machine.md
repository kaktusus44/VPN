# Ubuntu Control Machine Quick Start

This prepares a fresh Ubuntu machine to run the VPN Ansible automation.
It installs local tools, clones this repository into `~/VPN`, installs Ansible
collections, and prints the safe next commands.

## One Command

Run this on the Ubuntu control machine:

```bash
wget -qO- https://raw.githubusercontent.com/kaktusus44/VPN/main/scripts/bootstrap-ubuntu-control | bash
```

If `wget` is missing but `curl` exists:

```bash
curl -fsSL https://raw.githubusercontent.com/kaktusus44/VPN/main/scripts/bootstrap-ubuntu-control | bash
```

## What It Installs

- `ansible`
- `git`
- `openssh-client`
- `sshpass`
- `python3`
- Ansible collections from `requirements.yml`

## What It Does Not Do

The bootstrap does not change any VPN server. It only prepares the local
machine that will run Ansible.

After it finishes, use the printed commands to run:

```bash
cd ~/VPN
ansible -i inventories/test/hosts.yml all -m ping --ask-pass
ansible-playbook -i inventories/test/hosts.yml playbooks/preflight.yml --ask-pass
```

Do not run `playbooks/vpn-core.yml` until the change window is approved.

## Optional Settings

Clone to another directory:

```bash
VPN_DIR=/opt/VPN wget -qO- https://raw.githubusercontent.com/kaktusus44/VPN/main/scripts/bootstrap-ubuntu-control | bash
```

Use another repository URL:

```bash
VPN_REPO_URL=https://github.com/example/VPN.git wget -qO- https://raw.githubusercontent.com/kaktusus44/VPN/main/scripts/bootstrap-ubuntu-control | bash
```
