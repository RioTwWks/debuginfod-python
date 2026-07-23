# Устранение неполадок

## Scan застрял / мало файлов проиндексировано

**Симптом:** бесконечные `Memory pressure (rss_soft)`, `indexed` не растёт.

**Причина:** scan process pool держит RSS выше лимита.

**Решение:** обновите до версии с shutdown pool перед dedup. Проверьте лог `Scan pool stopped; releasing memory before dedup`.

## Dedup не запускается после scan

**Симптом:** scan complete, но нет `Background dedup started`.

**Проверка:**

```env
DEBUGINFOD_DEDUP_ENABLED=true
DEBUGINFOD_DEDUP_PROJECTS=YourProject
```

```sql
SELECT status, COUNT(*) FROM dedup_files GROUP BY status;
```

Если `pending=0` и `indexed=0` — dedup корректно пропущен.

## Dedup incomplete / pending > 0

1. Проверьте `error_msg` для `status=error`:
   ```sql
   SELECT file_path, error_msg FROM dedup_files WHERE status='error' LIMIT 20;
   ```
2. Transient memory errors — перезапуск или дождаться следующего ingest (auto-reset)
3. `file exceeds DEBUGINFOD_DEDUP_MAX_FILE_MB` — увеличьте лимит или исключите файл

## UI грузится минуту

**Было:** `stat()` на тысячи путей на NFS.

**Сейчас:** SQL-агрегаты + кэш. Обновите код. Проверьте `/ui/api/stats` время ответа:

```bash
curl -w '%{time_total}\n' -o /dev/null -s http://localhost:8003/ui/api/stats
```

Должно быть < 0.5 с после первого запроса (кэш).

## CPU 100% один core, RAM низкая

Нормально во время serial dedup крупного `.debug`. Dedup CPU-bound, не memory-starved.

## Логи не пишутся в файл

1. `DEBUGINFOD_LOG_DIR` задан и каталог существует/создаётся
2. Права на запись у процесса
3. Уровень: при `INFO` сообщения `DEBUG` не попадут в файл

## Проверка dedup завершён

```sql
SELECT status, COUNT(*) FROM dedup_files GROUP BY status;
-- ожидается: done=N, pending=0, error=0
```

```bash
grep "dedup complete" /var/log/debuginfod/debuginfod.log
```

## Сбор диагностики

```bash
curl -s http://localhost:8003/ui/api/stats | jq .
curl -s http://localhost:8003/stats | jq .
sqlite3 debuginfod.sqlite ".schema dedup_files"
tail -100 /var/log/debuginfod/debuginfod.log
```
