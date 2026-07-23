# Dedup (Quik)

## Назначение

Сжатие серий `.debug` в каталогах `build_*` через xdelta3: base остаётся (с dwz/objcopy), дельты → `.debug.xdelta`, оригиналы дельт удаляются.

## Пайплайн

1. **Discover** — обход `build_*` под `SCAN_PATH`, регистрация `.debug` в `dedup_files`
2. **Группировка** — `project + file_stem`, base = min `file_build_num`
3. **Preprocess** — `objcopy --decompress-debug-sections` + `dwz` (стратегия `xdelta-decompress-dwz`)
4. **Xdelta** — encode, verify decode, удаление оригинала delta
5. **Compress base** — опционально `objcopy --compress-debug-sections` на base

## Фоновый режим (v0.2+)

После scan dedup **не блокирует** scan thread:

```
Scan pool stopped → Scan complete (лог) → Background dedup started
```

Повторный hourly scan при `indexed=0` и `pending=0` **пропускает discover** — экономия минут на сетевом хранилище.

## Метрики в логах

```
dedup ingest: discovered=2126 compressed_deltas=1811 groups=315 errors=0
              pending=0 error_files=0 done=2126 bytes_before=... bytes_after=...
```

| Поле | Смысл |
|------|-------|
| `discovered` | Файлов зарегистрировано/обновлено при discover |
| `compressed_deltas` | Создано xdelta (не «все файлы») |
| `groups` | Групп с pending-работой |
| `done` | Файлов со статусом `done` в БД |
| `pending` / `error_files` | Осталось обработать / постоянные ошибки |

**Важно:** `compressed_deltas` + число base/singleton групп ≈ `discovered`. После успешного прогона `pending=0`.

## Статусы `dedup_files`

| status | Описание |
|--------|----------|
| `pending` | Ожидает обработки |
| `done` | Обработан (base/delta/full) |
| `error` | Постоянная ошибка (или transient до reset) |

Transient (память): `memory limit exceeded`, `dedup stopped` — сбрасываются в `pending` при следующем ingest.

## storage_kind

| kind | На диске |
|------|----------|
| `base` | Сжатый `.debug` (base группы) |
| `delta` | Только `.debug.xdelta` |
| `full` | Одиночный файл без пары |

## Отдача клиенту

`GET /buildid/.../debuginfo` → если файл delta, `restore_to_cache()` собирает `.debug` в `CACHE_DIR/dedup-restored/`.

## Проверка

```sql
SELECT storage_kind, status, COUNT(*) FROM dedup_files GROUP BY 1, 2;
SELECT project, files_compressed, errors, duration_ms FROM dedup_runs ORDER BY id DESC LIMIT 10;
```
