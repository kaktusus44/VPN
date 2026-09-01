# VPN Server Setup

Короткая инструкция для заказчика.

## Простые команды

### Первый запуск на управляющей Ubuntu

Эта команда готовит Ubuntu, создаёт SSH-ключ, скачивает репозиторий, ставит
локальный Ansible, затем спросит роль и IP сервера и запустит deploy:

```bash
wget -qO- https://raw.githubusercontent.com/kaktusus44/VPN/main/scripts/bootstrap-ubuntu-control | bash
```

### Если репозиторий уже скачан

```bash
cd ~/VPN
git pull
```

### Развернуть VPN

```bash
cd ~/VPN
scripts/deploy-server
```

Скрипт сам спросит роль (`main` или `reserve`), IP сервера и сколько Amnezia
ключей генерить. Для `main` он также спросит, подключать ли Telegram alerts,
и пароль администратора Grafana. Если количество не ввести, будет 100. Он сам
создаёт внутренний Ansible inventory, запускает VPN core, monitoring layer и
проверяет результат. Grafana на `main` публикуется наружу через nginx HTTPS с
Basic Auth; Prometheus и Alertmanager остаются на localhost.

Можно без вопросов:

```bash
VPN_CLIENT_COUNT=100 scripts/deploy-server main 1.2.3.4
```

Для полностью безвопросного `main` deploy с алертами:

```bash
VPN_CLIENT_COUNT=100 \
VPN_TELEGRAM_ALERTS=true \
VPN_TELEGRAM_BOT_TOKEN='<bot-token>' \
VPN_TELEGRAM_CHAT_ID='-1001234567890' \
VPN_GRAFANA_ADMIN_PASSWORD='<strong-password>' \
VPN_GRAFANA_BASIC_AUTH_PASSWORD='<strong-password>' \
scripts/deploy-server main 1.2.3.4
```

### Удалить то, что развернули

```bash
cd ~/VPN
scripts/destroy-server
```

Скрипт сам спросит роль и IP сервера, затем попросит ввести `DESTROY`.

Можно без вопросов:

```bash
VPN_RESET_CONFIRM=DESTROY scripts/destroy-server main 1.2.3.4
```

Destroy удаляет управляемые VPN services/configs/packages и `/var/lib/vpn`.
Он не удаляет SSH server, `/root/.ssh/authorized_keys` и локальный приватный
ключ Ansible.

### Удалить и сразу развернуть заново

```bash
cd ~/VPN
VPN_RESET_CONFIRM=DESTROY VPN_CLIENT_COUNT=100 scripts/redeploy-server 1.2.3.4 main
```

## Что сделать при создании VPS

1. Откройте свою Ubuntu-машину.
2. Выполните bootstrap-команду из раздела "Первый запуск".
3. Скопируйте публичный SSH-ключ, который появится в выводе.
4. При создании VPS у провайдера добавьте этот SSH-ключ для `root`.
5. Если VPS уже создан без ключа, bootstrap покажет готовую команду. Скопируйте
   её целиком и выполните в консоли VPS под `root`.
6. Вернитесь в Ubuntu, нажмите Enter, укажите роль и IP сервера.

Обычному пользователю не нужно вручную запускать `ansible-playbook`,
указывать `inventories/test/hosts.yml` или отдельно запускать `check-server`.

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
  monitoring.yml
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
  monitoring_stack/
  alerting/
  validation/
scripts/
  bootstrap-ubuntu-control
  check-server
  export-client-links
  compare-server-configs
  rotate-endpoint
  deploy-server
  destroy-server
  redeploy-server
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
