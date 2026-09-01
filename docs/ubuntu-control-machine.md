# Ubuntu для запуска VPN-скриптов

## Быстрый старт

На Ubuntu выполните:

```bash
wget -qO- https://raw.githubusercontent.com/kaktusus44/VPN/main/scripts/bootstrap-ubuntu-control | bash
```

Скрипт установит нужные программы, скачает репозиторий в `~/VPN`, создаст
SSH-ключ `~/.ssh/vpn_ansible` и установит совместимый Ansible в `~/VPN/.venv`.
После добавления ключа на VPS он спросит роль и IP сервера, затем сам запустит
`scripts/deploy-server`.

Скопируйте публичный ключ из вывода скрипта и добавьте его при создании каждого
VPS-сервера у провайдера для `root`: `main` и, если он уже есть, `reserve`. При
ручной настройке строка ключа должна находиться в `/root/.ssh/authorized_keys`;
отдельного файла `.pub` недостаточно.
Для уже созданного VPS скрипт выводит готовую команду: скопируйте её целиком и
выполните в консоли VPS под `root`.

## Проверить сервер без deploy

```bash
cd ~/VPN
scripts/check-server 1.2.3.4 main
```

Замените `1.2.3.4` на IP нового сервера. Роль: `main` или `reserve`. Эта
команда нужна только для ручной диагностики; обычный первый запуск идёт через
bootstrap выше.

## Добавить reserve позже

Если `main` уже развёрнут, добавьте тот же публичный ключ
`~/.ssh/vpn_ansible.pub` в `/root/.ssh/authorized_keys` на новом reserve VPS.
Затем запустите main deploy повторно с IP reserve:

```bash
cd ~/VPN
git pull
VPN_RESERVE_IP=5.6.7.8 VPN_CLIENT_COUNT=100 scripts/deploy-server main 1.2.3.4
```

Замените `1.2.3.4` на IP `main`, а `5.6.7.8` на IP `reserve`. Скрипт обновит
managed inventory, поставит VPN services и exporters на reserve и обновит
Prometheus на main, чтобы он скрейпил обе ноды.

## Если репозиторий уже скачан

```bash
cd ~/VPN
git pull
```

Если репозиторий лежит не в `~/VPN`, bootstrap можно запустить так:

```bash
wget -qO- https://raw.githubusercontent.com/kaktusus44/VPN/main/scripts/bootstrap-ubuntu-control | VPN_DIR=/path/to/VPN bash
```
