# Производительность

## Scan

- **Process pool** (Linux fork): изоляция RAM на воркер, `max_tasks_per_child=1`
- **Инкрементальность**: `scanned_files` по mtime+size — повторный scan пропускает неизменённые файлы
- **Memory governor** капит `SCAN_WORKERS` при нехватке headroom

Типичное время на ~2000 файлов (сеть): 1–2 мин при `SCAN_WORKERS=6–8`.

## Dedup

Узкие места:

1. **CPU** — `decompress` + xdelta на файлах 100–200 MiB (один core 100%)
2. **Serial mode** — файлы > `DEBUGINFOD_MEMORY_DEDUP_SERIAL_ABOVE_MB` (64 MiB по умолчанию)
3. **Discover** — полный обход дерева (пропускается если `indexed=0` и нет pending)

### Рекомендации для больших `.debug`

```env
# Больше RAM на один файл — но выше риск OOM
DEBUGINFOD_MEMORY_DEDUP_SERIAL_ABOVE_MB=128
DEBUGINFOD_MEMORY_DEDUP_PEAK_FACTOR_DECOMPRESS=20.0
DEBUGINFOD_MEMORY_MAX_RAM_MB=6144
DEBUGINFOD_MEMORY_MIN_AVAILABLE_MB=1536

# Меньше параллелизма при нехватке RAM
DEBUGINFOD_DEDUP_WORKERS=2
```

На 13 GiB RAM с занятыми ~8.8 GiB системой effective `max_rss` ~4.7 GiB — dedup идёт serial на крупных файлах **нормально**, это часы CPU, не баг.

## Web UI

Оптимизации:

- `get_stats()` — `SUM(size)` из SQL, без `stat()` по каждому артефакту
- `dedup_storage_totals()` — агрегация в SQL
- Кэш stats 15 с, scans 30 с
- Poll UI 60 с

Если UI всё ещё медленный:

- Проверьте сетевую задержку до `SCAN_PATH`
- Убедитесь что dedup в фоне (`dedup_in_progress` в `/ui/api/stats`)
- SQLite WAL включён для concurrent read

## Память: как читать логи

```
Memory limit adjust: max_rss capped 6144 -> 4771 MiB (35% of 13633 MiB RAM)
Scan workers capped 8 -> 6 (memory limits)
Dedup forced serial (largest file 209.7 MiB > 64 MiB threshold)
```

- `rss_soft` throttle при scan — scan pool освобождается **до** dedup
- Dedup в фоне не держит scan pool

## Ожидаемое поведение после деплоя

| Этап | Длительность |
|------|----------------|
| Первый scan + dedup | Часы (CPU-bound) |
| Повторный scan (без изменений) | ~1–2 мин scan, dedup skip ~мгновенно |
| UI load | < 1 с (с кэшем) |
