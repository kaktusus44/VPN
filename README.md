# VPN Server Setup

Короткая инструкция для заказчика.

## Что сделать

1. Откройте свою Ubuntu-машину.

2. Выполните одну команду:

```bash
wget -qO- https://raw.githubusercontent.com/kaktusus44/VPN/main/scripts/bootstrap-ubuntu-control | bash
```

3. Скопируйте публичный SSH-ключ, который появится в конце вывода.

4. При создании VPS-сервера у провайдера добавьте этот SSH-ключ для `root`.

5. Вернитесь в Ubuntu и запустите проверку, указав IP нового сервера:

```bash
cd ~/VPN
scripts/check-server 1.2.3.4
```

Замените `1.2.3.4` на IP сервера.

6. Пришлите результат проверки инженеру.

Если репозиторий уже был скачан раньше:

```bash
cd ~/VPN
git pull
```

Не запускайте `playbooks/vpn-core.yml` без подтверждения инженера.

## Технические заметки

Повторяемое развёртывание VPN-серверов `main` и `reserve`.

Основной VPN: AmneziaWG.
Резервный канал: Xray / VLESS / Reality.
DNS: Unbound только внутри VPN.
Мониторинг: отдельный слой.

Подробная инструкция для Ubuntu:
[docs/ubuntu-control-machine.md](docs/ubuntu-control-machine.md).

## Первый тестовый сервер

`108.165.33.37` — первый чистый тестовый сервер.

Preflight от 2026-08-30:

- Ubuntu 24.04.4 LTS
- kernel `6.8.0-111-generic`;
- снаружи найден только SSH на `22/tcp`;
- UFW установлен, но выключен;
- AmneziaWG, Xray, Unbound, fail2ban, nginx и monitoring stack не найдены.

Не храните SSH-пароли в репозитории. Лучше добавить сгенерированный ключ
`~/.ssh/vpn_ansible.pub` у VPS-провайдера при создании сервера и дальше
подключаться через SSH-ключ.

## Структура репозитория

```text
inventories/
  test/hosts.yml
  prod/hosts.yml
group_vars/
  all.yml
  vpn_servers.yml
host_vars/
  vpn-test-1.yml
playbooks/
  preflight.yml
  vpn-core.yml
  monitoring-agents.yml
  validate.yml
  roles/
  base/
  firewall/
  fail2ban/
  amneziawg/
  unbound_dns/
  xray_reality/
  vpn_clients/
  monitoring_agent/
  alerting/
  validation/
scripts/
  bootstrap-ubuntu-control
  check-server
  export-client-links
  compare-server-configs
  rotate-endpoint
docs/
  ubuntu-control-machine.md
  runbook.md
  export-file-contract.md
```

## Формат экспорта

Основной экспорт — простой текстовый файл. Одна строка — один готовый артефакт
для клиента:

```text
client-001 main amneziawg <ready-client-link>
client-001 main vless_xhttp vless://...
client-001 main vless_tcp vless://...
```

Порядок полей:

```text
client_id server_role protocol ready_link
```

## Monitoring

Мониторинг — отдельный слой. VPN-сервер должен отдавать host metrics,
AWG metrics и проектные VPN health metrics. Алерты лучше отправлять через
Prometheus alert rules и Alertmanager, а не прямой Telegram-логикой внутри
exporter.
