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
ключей генерить. Для `main` он также спросит, есть ли уже `reserve`-нода для
централизованного мониторинга. Если `reserve` уже есть, её IP попадёт в
`inventories/managed/hosts.yml`, на неё поставятся VPN services и exporters, а
Prometheus на `main` будет скрейпить обе ноды. Для `main` скрипт также спросит,
подключать ли Telegram alerts, и пароль администратора Grafana. Если количество
не ввести, будет 100. Он сам создаёт Ansible inventory, запускает VPN core,
monitoring layer и проверяет результат. Grafana на `main` публикуется наружу
через nginx HTTPS с Basic Auth. Alertmanager на `main` публикуется наружу через
nginx HTTPS с Basic Auth на порту `9443`. Prometheus на `main` публикуется
наружу через nginx HTTPS с Basic Auth на порту `9445`. В итоговом выводе скрипт
показывает, сколько заняло развёртывание.

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
VPN_ALERTMANAGER_BASIC_AUTH_PASSWORD='<strong-password>' \
VPN_RESERVE_IP='5.6.7.8' \
scripts/deploy-server main 1.2.3.4
```

Если VPS не может читать `https://apt.grafana.com` напрямую, скачайте Grafana
`.deb` на управляющей машине и передайте путь через `VPN_GRAFANA_DEB_PATH`:

```bash
curl -fsSL -o /tmp/grafana.deb \
  https://dl.grafana.com/grafana/release/13.2.1/grafana_13.2.1_33191028959_linux_amd64.deb
VPN_GRAFANA_DEB_PATH=/tmp/grafana.deb scripts/deploy-server main 1.2.3.4
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

В итоговом выводе `redeploy-server` тоже показывает общее время выполнения
после ввода всех параметров и подтверждения `DESTROY`.

### Развернуть reserve отдельно

Если `main` уже поднят, новый reserve VPS можно развернуть отдельной командой.
Сначала добавьте тот же публичный Ansible SSH-ключ `~/.ssh/vpn_ansible.pub` в
`/root/.ssh/authorized_keys` на reserve VPS.

Затем на управляющей Ubuntu:

```bash
cd ~/VPN
git pull
VPN_CLIENT_COUNT=100 scripts/deploy-server reserve 5.6.7.8
```

Замените `5.6.7.8` на IP reserve. Этот запуск поставит на reserve VPN services,
node exporter и vpn-custom-exporter. Monitoring stack, Grafana, Alertmanager и
Prometheus ставятся только на `main`.

Важно: после такого отдельного deploy метрики reserve ещё не появятся в
Prometheus на `main`. Prometheus читает список целей из Ansible inventory,
который рендерится на `main`. Поэтому после успешного deploy reserve нужно
подключить reserve к monitoring отдельным лёгким скриптом:

```bash
scripts/connect-reserve-monitoring 1.2.3.4 5.6.7.8
```

Замените `1.2.3.4` на IP main, а `5.6.7.8` на IP reserve. Скрипт обновит
`inventories/managed/hosts.yml`, установит/обновит monitoring exporters на
reserve и перерендерит Prometheus на `main`, чтобы он начал скрейпить обе ноды.
VPN core, AmneziaWG/Xray config, клиентские ключи и `/var/lib/vpn` этот скрипт
не трогает.

### Добавить reserve при повторном deploy main

Если `main` уже развёрнут, reserve можно добавить позднее. Сначала добавьте тот
же публичный Ansible SSH-ключ `~/.ssh/vpn_ansible.pub` в
`/root/.ssh/authorized_keys` на новом reserve VPS.

Затем на управляющей Ubuntu выполните:

```bash
cd ~/VPN
git pull
scripts/connect-reserve-monitoring 1.2.3.4 5.6.7.8
```

Замените `1.2.3.4` на IP `main`, а `5.6.7.8` на IP `reserve`. Скрипт обновит
`inventories/managed/hosts.yml`, поставит exporters на reserve и перерендерит
Prometheus на main так, чтобы он скрейпил оба сервера. На reserve UFW откроет
exporter-порты `9100` и `9187` только для IP main. VPN core и клиентские ключи
не изменяются.

После обновления `main` метрики второго сервера появятся в Prometheus targets:
`node` для `:9100` и `vpn-custom-exporter` для `:9187`, с labels `role="reserve"`
и `server="vpn-reserve"`.

Проверить, что Prometheus подтянул reserve:

```bash
ssh -i ~/.ssh/vpn_ansible root@1.2.3.4
curl -s http://127.0.0.1:9090/api/v1/targets | jq '.data.activeTargets[] | {job: .labels.job, server: .labels.server, role: .labels.role, health: .health}'
```

## Что сделать при создании VPS

1. Откройте свою Ubuntu-машину.
2. Выполните bootstrap-команду из раздела "Первый запуск".
3. Скопируйте публичный SSH-ключ, который появится в выводе.
4. При создании VPS у провайдера добавьте этот SSH-ключ для `root` на каждом
   целевом сервере: main и, если уже есть, reserve.
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
  connect-reserve-monitoring.yml
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
  connect-reserve-monitoring
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
