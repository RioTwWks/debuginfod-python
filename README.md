# debuginfod-python

Реализация [debuginfod](https://sourceware.org/elfutils/Debuginfod.html) на **Python** с той же архитектурой, что и [debuginfod-go](https://github.com/RioTwWks/debuginfod-go): **индекс метаданных в БД + файлы на диске**, отдельная подсистема **Quik dedup** (xdelta3) после сканирования.

## Архитектура (как в debuginfod-go)

| Аспект | Поведение |
|--------|-----------|
| Индексация | Инкрементальный scan `scanned_files`, ELF с build-id → `artifacts` |
| Без build-id | Qt `.debug` без GNU build-id пропускаются (`no_build_id`), обрабатываются dedup |
| Хранение | Файлы остаются на исходных путях; БД хранит только метаданные |
| Dedup | После scan: discover `build_*` → xdelta3 → `.debug.xdelta` рядом с оригиналом |
| Отдача | `FileResponse` с диска; для delta — `restore_to_cache()` в `{cache}/dedup-restored/` |
| Порт по умолчанию | 8003 (Go — 8002) |

## Требования

- Python 3.11+
- **xdelta3**, **dwz**, **objcopy** (для dedup)
- gcc (для тестов)

## Установка

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# или
pip install -e ".[dev]"
```

## Запуск

```bash
cp .env.example .env
# DEBUGINFOD_SCAN_PATH=/path/to/build/outputs
# DEBUGINFOD_DEDUP_ENABLED=true
# DEBUGINFOD_DEDUP_PROJECTS=QuikServer,Front

python -m debuginfod
```

## HTTP API

Стандартные эндпоинты debuginfod:

- `GET /buildid/{BUILDID}/debuginfo`
- `GET /buildid/{BUILDID}/executable`
- `GET /buildid/{BUILDID}/source/{absolute/path}`
- `GET /buildid/{BUILDID}/section/{name}`
- `GET /metadata?key=glob|file|buildid&value=...`
- `GET /healthz`, `GET /readyz`
- `POST /admin/rescan` (опционально `X-Admin-Token`)
- `POST /admin/dedup-backfill` — ручной запуск dedup

Дополнительно:

- `GET /stats` — статистика индекса и dedup

## Dedup (Quik)

Включение:

```bash
DEBUGINFOD_DEDUP_ENABLED=true
DEBUGINFOD_DEDUP_PROJECTS=QuikServer,Front
DEBUGINFOD_DEDUP_WORKERS=4
DEBUGINFOD_DEDUP_STRATEGY=xdelta-decompress-dwz
DEBUGINFOD_XDELTA_PATH=xdelta3
DEBUGINFOD_DWZ_PATH=dwz
DEBUGINFOD_OBJCOPY_PATH=objcopy
```

Пайплайн (как в Go):

1. Discover каталогов `build_*` под `DEBUGINFOD_SCAN_PATH`
2. Группировка по `normalize_project + file_stem`, base = min `file_build_num`
3. `objcopy --decompress-debug-sections` + `dwz` → xdelta3 → verify → удаление оригинала
4. Дельты: `<file>.debug.xdelta` рядом с base

## Web UI

**http://localhost:8003/ui/** — дашборд, поиск, проекты dedup.

**http://localhost:8003/ui/benchmark/** — сравнение Go vs Python.

## Документация

Подробное руководство: [docs/README.md](docs/README.md)

- [Конфигурация](docs/configuration.md) — все переменные `.env`, логирование
- [Эксплуатация](docs/operations.md) — systemd, логи, мониторинг
- [Dedup](docs/dedup.md) — пайплайн и интерпретация метрик
- [Производительность](docs/performance.md) — память, воркеры, UI
- [Устранение неполадок](docs/troubleshooting.md)

## Тесты

```bash
pytest
```

## Сравнение с debuginfod-go

Оба сервера используют одинаковую модель: метаданные в SQLite/PostgreSQL, файлы на диске, dedup как post-scan hook. Для честного бенчмарка укажите одинаковый `DEBUGINFOD_SCAN_PATH`.
