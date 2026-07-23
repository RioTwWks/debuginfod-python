# Конфигурация (.env)

Все параметры читаются из `.env` (через `python-dotenv`) или переменных окружения. Префикс: `DEBUGINFOD_`.

## Основные

| Переменная | По умолчанию | Описание |
|------------|--------------|----------|
| `DEBUGINFOD_DB_PATH` | `debuginfod.sqlite` | Путь к SQLite (если нет `DATABASE_URL`) |
| `DEBUGINFOD_DATABASE_URL` | — | PostgreSQL: `postgresql://user:pass@host/db` |
| `DEBUGINFOD_SCAN_PATH` | `.` | Корни сканирования (через запятую) |
| `DEBUGINFOD_PORT` | `8003` | HTTP-порт |
| `DEBUGINFOD_HOST` | `0.0.0.0` | Bind-адрес |
| `DEBUGINFOD_CACHE_DIR` | `.debuginfod-cache` | Кэш восстановления delta → `.debug` |
| `DEBUGINFOD_RESCAN_INTERVAL` | `3600` | Интервал фонового rescan (сек) |
| `DEBUGINFOD_SCAN_ENABLED` | `true` | Фоновый scan |
| `DEBUGINFOD_UI_ENABLED` | `true` | Web UI |

## Логирование

| Переменная | По умолчанию | Описание |
|------------|--------------|----------|
| `DEBUGINFOD_LOG_LEVEL` | `info` | `debug`, `info`, `warning`, `error` |
| `DEBUGINFOD_LOG_DIR` | — | Каталог для файлов логов. Пусто = только stderr |

При заданном `DEBUGINFOD_LOG_DIR` создаётся `debuginfod.log` с **ротацией по дням** (полночь, суффикс `.YYYY-MM-DD`, хранится 30 файлов). Уровень фильтрации — из `DEBUGINFOD_LOG_LEVEL`.

Пример:

```env
DEBUGINFOD_LOG_LEVEL=debug
DEBUGINFOD_LOG_DIR=/var/log/debuginfod
```

## Scan

| Переменная | По умолчанию | Описание |
|------------|--------------|----------|
| `DEBUGINFOD_SCAN_WORKERS` | `4` | Параллельные воркеры индексации (process pool на Linux) |
| `DEBUGINFOD_SCAN_DWARF_MAX_MB` | `32` | Макс. размер ELF для извлечения DWARF sources (`0` = отключить) |

## Dedup (Quik)

| Переменная | По умолчанию | Описание |
|------------|--------------|----------|
| `DEBUGINFOD_DEDUP_ENABLED` | `false`* | Включить dedup (*`true` если задан `DEDUP_PROJECTS`) |
| `DEBUGINFOD_DEDUP_PROJECTS` | — | Проекты через запятую, напр. `QuikServer,Front` |
| `DEBUGINFOD_DEDUP_WORKERS` | `4` | Параллельные группы dedup |
| `DEBUGINFOD_DEDUP_STRATEGY` | `xdelta-decompress-dwz` | Препроцессор перед xdelta |
| `DEBUGINFOD_DEDUP_COMPRESS_BASE` | `true` | `objcopy --compress-debug-sections` для base |
| `DEBUGINFOD_XDELTA_PATH` | `xdelta3` | Путь к xdelta3 |
| `DEBUGINFOD_DWZ_PATH` | `dwz` | Путь к dwz |
| `DEBUGINFOD_OBJCOPY_PATH` | `objcopy` | Путь к objcopy |
| `DEBUGINFOD_DEDUP_MAX_FILE_MB` | `256` | Файлы крупнее — `error`, не обрабатываются |

## Лимиты памяти

| Переменная | По умолчанию | Описание |
|------------|--------------|----------|
| `DEBUGINFOD_MEMORY_MAX_RAM_MB` | `0` | Макс. RSS процесса+детей (`0` = без лимита) |
| `DEBUGINFOD_MEMORY_MAX_SWAP_MB` | `0` | Макс. swap дерева процессов |
| `DEBUGINFOD_MEMORY_MIN_AVAILABLE_MB` | `512` | Мин. `MemAvailable` в системе |
| `DEBUGINFOD_MEMORY_MAX_SYSTEM_RAM_PCT` | `65` | Доп. throttle по % занятой RAM |
| `DEBUGINFOD_MEMORY_DEDUP_PEAK_FACTOR` | `3.0` | Оценка пика RAM на файл (xdelta) |
| `DEBUGINFOD_MEMORY_DEDUP_PEAK_FACTOR_DECOMPRESS` | `20.0` | Пик для `decompress` стратегии |
| `DEBUGINFOD_MEMORY_DEDUP_SERIAL_ABOVE_MB` | `64` | Файлы крупнее — dedup только последовательно |

См. [performance.md](performance.md) для тонкой настройки на 13+ GiB RAM.

## Администрирование

| Переменная | Описание |
|------------|----------|
| `DEBUGINFOD_ADMIN_KEY` | Токен для `POST /admin/rescan` |
| `DEBUGINFOD_METADATA_MAXTIME` | Таймаут metadata search (сек) |
| `DEBUGINFOD_METADATA_PAGE_SIZE` | Размер страницы metadata |

## CLI

```bash
python -m debuginfod --help
python -m debuginfod --no-scan -p 8003 -s /data/builds
python -m debuginfod --env-file /etc/debuginfod/debuginfod.env
```
