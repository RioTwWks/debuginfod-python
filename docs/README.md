# Документация debuginfod-python

Подробное руководство по развёртыванию, настройке и эксплуатации сервера.

## Содержание

| Документ | Описание |
|----------|----------|
| [configuration.md](configuration.md) | Все переменные `.env`, значения по умолчанию |
| [operations.md](operations.md) | Запуск, systemd, логи, мониторинг |
| [dedup.md](dedup.md) | Quik dedup: пайплайн, метрики, интерпретация логов |
| [performance.md](performance.md) | Память, воркеры, ускорение UI и scan |
| [troubleshooting.md](troubleshooting.md) | Типичные проблемы и диагностика |

## Быстрый старт

```bash
cp .env.example .env
# отредактируйте DEBUGINFOD_SCAN_PATH, DEBUGINFOD_DEDUP_*
python -m debuginfod
```

Web UI: `http://localhost:8003/ui/`

## Архитектура

```
SCAN_PATH (диск)
    │
    ├─► Indexer (scan) ──► SQLite/PostgreSQL (метаданные)
    │       │
    │       └─► DedupRunner (фон) ──► xdelta3 + dwz + objcopy
    │
    └─► HTTP API / Web UI ──► FileResponse / restore_to_cache
```

- **Scan** — инкрементальный обход ELF, индекс `artifacts` / `scanned_files`.
- **Dedup** — после scan запускается **в фоне**; scan завершается за ~минуту, dedup может идти часами на больших `.debug`.
- **UI** — статистика кэшируется 15 с; dedup не блокирует HTTP.

## Обновление документации

При изменении поведения, `.env` или API обновляйте соответствующий файл в `docs/` и ссылку в `README.md`.
