# Эксплуатация

## Запуск

```bash
source .venv/bin/activate
python -m debuginfod
```

Проверка:

```bash
curl -s http://localhost:8003/healthz
curl -s http://localhost:8003/stats | jq .
```

## systemd

Пример unit: `deploy/debuginfod-python.service`. Environment file: `deploy/debuginfod.env.example`.

Рекомендуется задать:

```env
DEBUGINFOD_LOG_DIR=/var/log/debuginfod
DEBUGINFOD_LOG_LEVEL=info
```

Права на каталог логов — у пользователя службы.

## Логи

### Консоль

По умолчанию все сообщения идут в stderr uvicorn/процесса:

```
2026-07-23 11:55:53,610 INFO debuginfod.main: Starting debuginfod-python on port 8003
```

### Файл (ротация по дням)

```env
DEBUGINFOD_LOG_LEVEL=debug
DEBUGINFOD_LOG_DIR=/var/log/debuginfod
```

Файлы:

- `/var/log/debuginfod/debuginfod.log` — текущий день
- `/var/log/debuginfod/debuginfod.log.2026-07-22` — архив

Уровни: `DEBUG` (детали skip/index), `INFO` (scan/dedup итоги), `WARNING` (memory throttle, incomplete dedup), `ERROR`.

### Ключевые сообщения

| Сообщение | Значение |
|-----------|----------|
| `Scan complete: indexed=N skipped=M` | Scan завершён; dedup может ещё идти в фоне |
| `Background dedup started` | Dedup в отдельном потоке |
| `Skipping dedup ingest` | Нет pending/error, scan indexed 0 — discover пропущен |
| `Dedup forced serial` | Крупный файл (> `SERIAL_ABOVE_MB`) — один поток |
| `dedup ingest: ... done=N` | Итог фонового ingest |

## Web UI

- **Дашборд** — `/ui/`, опрос stats каждые 60 с
- **Сканирования** — история scan/dedup runs
- **Сканировать** — ручной `POST /ui/api/rescan`

Поля API `/ui/api/stats`:

- `scan_in_progress` — идёт индексация
- `dedup_in_progress` — идёт фоновый dedup

## Ручные операции

```bash
# Rescan (с admin key)
curl -X POST http://localhost:8003/admin/rescan -H "X-Admin-Token: $KEY"

# Dedup backfill
curl -X POST "http://localhost:8003/admin/dedup-backfill?project=QuikServer"
```

## Мониторинг БД

```bash
sqlite3 debuginfod.sqlite "SELECT status, COUNT(*) FROM dedup_files GROUP BY status;"
sqlite3 debuginfod.sqlite "SELECT * FROM scan_runs ORDER BY id DESC LIMIT 5;"
```

Ожидаемое после полного dedup: `done=2126, pending=0, error=0` (числа зависят от дерева).
