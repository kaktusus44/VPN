# Ubuntu Control Machine Quick Start

This prepares a fresh Ubuntu machine to run the VPN Ansible automation.
It installs local tools, creates an SSH key for Ansible, clones this repository
into `~/VPN`, installs Ansible collections, and prints the safe next commands.

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
- `python3`
- Ansible collections from `requirements.yml`

It also creates this local SSH key when it does not already exist:

```text
~/.ssh/vpn_ansible
```

Use the printed public key when creating the VPS in the provider control panel.
The server should have that public key installed for `root`, so Ansible can
connect without an SSH password.

## What It Does Not Do

The bootstrap does not change any VPN server. It only prepares the local
machine that will run Ansible.

After it finishes, use the printed commands to run:

```bash
cd ~/VPN
ansible -i inventories/test/hosts.yml all -m ping
ansible-playbook -i inventories/test/hosts.yml playbooks/preflight.yml
```

Do not run `playbooks/vpn-core.yml` until the change window is approved.

## Optional Settings

Clone to another directory:

```bash
wget -qO- https://raw.githubusercontent.com/kaktusus44/VPN/main/scripts/bootstrap-ubuntu-control | VPN_DIR=/opt/VPN bash
```

Use another repository URL:

```bash
wget -qO- https://raw.githubusercontent.com/kaktusus44/VPN/main/scripts/bootstrap-ubuntu-control | VPN_REPO_URL=https://github.com/example/VPN.git bash
```

Use another SSH key path:

```bash
wget -qO- https://raw.githubusercontent.com/kaktusus44/VPN/main/scripts/bootstrap-ubuntu-control | VPN_SSH_KEY=~/.ssh/customer_vpn bash
```
