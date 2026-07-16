# debuginfod-python

Альтернативная реализация [debuginfod](https://sourceware.org/elfutils/Debuginfod.html) на **Python** с хранением артефактов через **xdelta3** (diff/patch). Предназначена для сравнения с [debuginfod-go](https://github.com/RioTwWks/debuginfod-go), где файлы индексируются и отдаются «как есть» с диска.

## Идея сравнения

| Аспект | debuginfod-go | debuginfod-python |
|--------|---------------|-------------------|
| Язык | Go | Python |
| Хранение | Индекс в SQLite + оригинальные файлы на диске | Content-addressed blobs + xdelta3-дельты |
| Дедупликация | Нет (только кеш извлечения из архивов) | Да, через SHA-256 и патчи между версиями |
| Отдача клиенту | Прямой `ServeFile` / stream из архива | Реконструкция из full/delta chain + кеш |
| Порт по умолчанию | 8002 | 8003 |

При повторных сборках одного и того же бинарника (та же «семья» пути) сервер пытается сохранить **xdelta3-патч** относительно предыдущей версии. Если патч не меньше порога (`DEBUGINFOD_DELTA_MIN_RATIO`, по умолчанию 85% от оригинала), сохраняется полный blob.

## Требования

- Python 3.11+
- **xdelta3** (`apt install xdelta3` / `dnf install xdelta3`)
- gcc (для генерации тестовых артефактов)

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
# Укажите каталоги с debuginfo/ELF, например:
# DEBUGINFOD_SCAN_PATH=/usr/lib/debug,/path/to/build/outputs

python -m debuginfod
# или
debuginfod -s /usr/lib/debug -p 8003
```

## HTTP API (совместимость с debuginfod)

Стандартные эндпоинты:

- `GET /buildid/{BUILDID}/debuginfo`
- `GET /buildid/{BUILDID}/executable`
- `GET /buildid/{BUILDID}/source/{absolute/path}`
- `GET /buildid/{BUILDID}/section/{name}`
- `GET /metadata?key=glob|file|buildid&value=...`
- `GET /healthz`, `GET /readyz`
- `POST /admin/rescan` (опционально `X-Admin-Token`)

Дополнительно для бенчмарка:

- `GET /stats` — статистика хранения (full vs delta, сэкономленные байты, compression ratio)

## Web UI

Дашборд по аналогии с [debuginfod-go](https://github.com/RioTwWks/debuginfod-go): **http://localhost:8003/ui/**

- Статистика индекса (артефакты, сканирование, HTTP-запросы)
- Метрики xdelta3-хранилища (сэкономленные байты, коэффициент сжатия)
- Поиск артефактов: build-id (префикс), glob, file
- Ссылки на скачивание debuginfo/executable

API дашборда:

- `GET /ui/api/stats`
- `GET /ui/api/search?key=buildid|glob|file&...`

Отключить UI: `DEBUGINFOD_UI_ENABLED=false` или флаг `--no-ui`.

### Benchmark UI

**http://localhost:8003/ui/benchmark/** — визуализация сравнения Go vs Python:

- форма запуска бенчмарка (URL обоих серверов, testdata, число прогонов)
- графики латентности по версиям бинарника (canvas)
- сравнение дискового пространства (Go testdata vs Python blobs)
- таблица деталей и история запусков

API:

- `GET /ui/api/benchmark/config` — параметры по умолчанию
- `POST /ui/api/benchmark/run` — запуск сравнения
- `GET /ui/api/benchmark/last` — последний отчёт
- `GET /ui/api/benchmark/history` — история (до 20 записей)

Пример для GDB/LLDB:

```bash
export DEBUGINFOD_URLS="http://localhost:8003"
debuginfod-find executable <BUILDID>
```

## Сравнительный бенчмарк

### 1. Поднять оба сервиса

**Go** (порт 8002):

```bash
cd /path/to/debuginfod-go
DEBUGINFOD_PORT=8002 DEBUGINFOD_SCAN_PATH=./testdata/versions ./debuginfod
```

**Python** (порт 8003):

```bash
DEBUGINFOD_PORT=8003 DEBUGINFOD_SCAN_PATH=./testdata/versions python -m debuginfod
```

### 2. Сгенерировать тестовые версии бинарника

```bash
python scripts/generate_test_artifacts.py -o testdata/versions -n 10
```

Скрипт собирает `demo_v1` … `demo_vN` с флагом `-Wl,--build-id=sha1` (обязательно для бенчмарка).

Если бинарники уже есть, но без build-id — пересоберите их этой командой.

### 3. Запустить сравнение

```bash
python scripts/compare_benchmark.py \
  --go-url http://localhost:8002 \
  --py-url http://localhost:8003 \
  --testdata testdata/versions
```

Скрипт выводит JSON с:

- латентностью загрузки executable для каждой версии (Go vs Python);
- статистикой хранения Python-сервера (`/stats`).

### Метрики для презентации коллегам

1. **Диск**: `total_stored_bytes` vs суммарный размер всех версий (Go хранит каждую копию на диске в scan path).
2. **Сжатие**: доля `delta` blobs, `compression_ratio` в `/stats` и `/metadata`.
3. **Латентность**: среднее время первого и повторного `GET /buildid/.../executable` (у Python есть overhead реконструкции xdelta3).
4. **CPU/RAM**: наблюдение через `htop` при массовых запросах.

## Конфигурация

| Переменная | По умолчанию | Описание |
|------------|--------------|----------|
| `DEBUGINFOD_DB_PATH` | `debuginfod.sqlite` | SQLite с метаданными |
| `DEBUGINFOD_SCAN_PATH` | `.` | Каталоги для сканирования (через запятую) |
| `DEBUGINFOD_PORT` | `8003` | HTTP-порт |
| `DEBUGINFOD_BLOB_DIR` | `.debuginfod-blobs` | Full blobs и deltas |
| `DEBUGINFOD_RECONSTRUCT_CACHE_DIR` | `.debuginfod-reconstruct-cache` | Кеш реконструированных файлов |
| `DEBUGINFOD_DELTA_MIN_RATIO` | `0.85` | Порог: delta сохраняется, если patch < ratio × original |
| `DEBUGINFOD_XDELTA3_PATH` | `xdelta3` | Путь к бинарнику xdelta3 |
| `DEBUGINFOD_RESCAN_INTERVAL` | `3600` | Интервал фонового rescan (сек) |
| `DEBUGINFOD_UI_ENABLED` | `true` | Web UI на `/ui/` |
| `DEBUGINFOD_BENCHMARK_GO_URL` | `http://localhost:8002` | URL debuginfod-go для бенчмарка |
| `DEBUGINFOD_BENCHMARK_TESTDATA` | `testdata/versions` | Каталог с demo_v* для бенчмарка |

## Тесты

```bash
pytest -q
```

## Архитектура

```
Сканирование ELF → build-id + family_key
       ↓
Первая версия семьи → full blob (SHA-256)
Следующие версии   → xdelta3 -e -s base new patch
       ↓
SQLite: artifacts(build_id, type) → content_hash → blobs(full|delta)
       ↓
HTTP GET → reconstruct (цепочка delta) → stream клиенту
```

## Ограничения v1

- Сканируются loose ELF и исходники из DWARF (без .deb/.rpm — в Go-версии они есть).
- Цепочки delta: каждая версия патчится от предыдущей; глубокая цепочка увеличивает стоимость реконструкции.
- Federation/upstream proxy не реализован.

Эти ограничения не мешают A/B-тесту «индекс на диске» vs «xdelta3-хранилище» на одном наборе ELF.
