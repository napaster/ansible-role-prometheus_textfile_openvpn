# prometheus_textfile_openvpn

Раскатывает Python парсер OpenVPN status файлов + systemd timer'а, который
пишет `.prom` файл в textfile_collector dir `node_exporter`'а. Используется
на хостах **без локального Prometheus** (VMs из группы
`monitoring_prometheus_vms`), где центральная промка скрейпит только `:9100`
и нужно подсунуть `openvpn_*` метрики через тот же node_exporter endpoint.

Метрики **строго совместимы по именам и лейблам** с
[natrontech/openvpn-exporter](https://github.com/natrontech/openvpn-exporter)
и нашим [`openvpn_exporter`](https://github.com/napaster/ansible-role-openvpn_exporter):
`openvpn_up`, `openvpn_server_connected_clients`,
`openvpn_server_client_{sent,received}_bytes_total`,
`openvpn_status_update_time_seconds`, `openvpn_server_route_last_reference_time_seconds`.
Дашборды/алерты одни и те же независимо от того, как метрики собирались.

## Платформы

* ArchLinux (path `/var/lib/prometheus-node-exporter/textfile_collector` — Arch default,
  тот же что `prometheus-node-exporter` пакет ставит в `/etc/conf.d/`)
* Debian/EL — должно работать, дефолтный путь textfile dir совпадает.
  Не тестировалось — открой issue если у себя проверил

## Требования

* `openvpn-server@<port>.service` запущен и пишет status в `/run/openvpn-server/<port>.status`
  (директива `status /run/openvpn-server/<port>.status <interval>` + `status-version 2`
  в `/etc/openvpn/server/<port>.conf`)
* `node_exporter` запущен с
  `--collector.textfile.directory=/var/lib/prometheus-node-exporter/textfile_collector`
* Python 3.6+ (для f-strings в parser'е)

## Как это работает

```
openvpn-server@<port>.service  ──── каждые 60s ──→  /run/openvpn-server/<port>.status (CSV v2)
                                                            │
                                                            ▼
systemd timer (openvpn-textfile-collect.timer) ──── каждые 60s ──→ /usr/local/bin/openvpn_textfile_collect.py
                                                                              │
                                                                              ▼
                            /var/lib/prometheus-node-exporter/textfile_collector/openvpn.prom
                                                                              │
                                                                              ▼ (атомарный pickup при next scrape)
                                                                  node_exporter :9100/metrics
                                                                              │
                                                                              ▼
                                                                  central Prometheus (remote scrape)
```

## Переменные

| Переменная | По умолчанию | Описание |
|---|---|---|
| `prometheus_textfile_openvpn_script_dest` | `/usr/local/bin/openvpn_textfile_collect.py` | Путь parser-скрипта |
| `prometheus_textfile_openvpn_textfile_dir` | `/var/lib/prometheus-node-exporter/textfile_collector` | textfile dir |
| `prometheus_textfile_openvpn_status_glob` | `/run/openvpn-server/*.status` | Glob — парсятся все instance'ы которые тут лежат |
| `prometheus_textfile_openvpn_output_path` | `<textfile_dir>/openvpn.prom` | Куда писать `.prom` файл |
| `prometheus_textfile_openvpn_interval_sec` | `60` | Период systemd timer |

## Пример использования

Playbook (см. [napaster/playbook `monitoring/deploy_prometheus_textfile_openvpn.yml`](https://github.com/napaster/playbook/blob/master/monitoring/deploy_prometheus_textfile_openvpn.yml)):
```yaml
- name: Deploy openvpn textfile collector (for hosts without local Prometheus)
  hosts: "{{ target | default('none') }}"
  become: true
  roles:
    - prometheus_textfile_openvpn
```

Запуск:
```
ansible-playbook playbook/monitoring/deploy_prometheus_textfile_openvpn.yml -e target=ag.napaster.ru
```

Scrape config на центральной промке (`host_vars/promka/prometheus_stack.yml` или аналог) —
**обычный node_exporter scrape** уже соберёт `openvpn_*` метрики. Если хочешь, чтобы они
оказались под `job="openvpn"` (как на хостах с natrontech-бинарём), добавь отдельный job
с `metric_relabel_configs`:

```yaml
- job_name: openvpn
  scrape_interval: 30s
  static_configs:
    - targets: ['ag.napaster.ru:9100']
      labels: {instance: 'ag.napaster.ru', site: napaster, cluster: napaster}
  metric_relabel_configs:
    - source_labels: [__name__]
      regex: 'openvpn_.*'
      action: keep
```

## Альтернатива для хостов с локальным Prometheus

Если на хосте есть свой prometheus stack (gw.* gateways) — используй
[`openvpn_exporter`](https://github.com/napaster/ansible-role-openvpn_exporter).
Бинарь от natrontech listen'ит на `127.0.0.1:9176`, локальная промка скрейпит
напрямую. Текстфайл-вариант там не нужен.

## Notes

* Parser сам в себе — без зависимостей кроме stdlib Python 3
* Атомарная запись через temp file + rename — node_exporter не увидит
  partial-read'а
* `prometheus_textfile_openvpn_status_glob` парсит **все** status'ы (multi-instance
  ready). Каждая метрика получает label `status_path="/run/openvpn-server/<port>.status"`
* Timer запускается через `OnUnitActiveSec=60s` — точно после
  предыдущего успешного run'а, без накопления queue при медленных файлах
