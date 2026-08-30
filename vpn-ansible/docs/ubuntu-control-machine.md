# Ubuntu для запуска VPN-скриптов

## Быстрый старт

На Ubuntu выполните:

```bash
wget -qO- https://raw.githubusercontent.com/kaktusus44/VPN/main/scripts/bootstrap-ubuntu-control | bash
```

Скрипт установит нужные программы, скачает репозиторий в `~/VPN` и создаст
SSH-ключ `~/.ssh/vpn_ansible`.

Скопируйте публичный ключ из вывода скрипта и добавьте его при создании
VPS-сервера у провайдера.

## Проверить сервер

```bash
cd ~/VPN
scripts/check-server 1.2.3.4
```

Замените `1.2.3.4` на IP нового сервера.

Если проверка прошла успешно, пришлите вывод инженеру.

Не запускайте `playbooks/vpn-core.yml` без подтверждения инженера.

## Если репозиторий уже скачан

```bash
cd ~/VPN
git pull
```

Если репозиторий лежит не в `~/VPN`, bootstrap можно запустить так:

```bash
wget -qO- https://raw.githubusercontent.com/kaktusus44/VPN/main/scripts/bootstrap-ubuntu-control | VPN_DIR=/path/to/VPN bash
```
