(function () {
  const statsGrid = document.getElementById("stats-grid");
  const scanInfo = document.getElementById("scan-info");
  const uptimeEl = document.getElementById("uptime");
  const searchForm = document.getElementById("search-form");
  const searchInput = document.getElementById("search-input");
  const searchHint = document.getElementById("search-hint");
  const searchStatus = document.getElementById("search-status");
  const resultsTable = document.getElementById("results-table");
  const resultsBody = document.getElementById("results-body");
  const loadMoreBtn = document.getElementById("load-more");
  const modeButtons = document.querySelectorAll(".mode-btn");
  const mainTabs = document.querySelectorAll(".main-tab");
  const tabDashboard = document.getElementById("tab-dashboard");
  const tabScans = document.getElementById("tab-scans");
  const indexSummary = document.getElementById("index-summary");
  const dedupSummary = document.getElementById("dedup-summary");
  const dedupStatus = document.getElementById("dedup-status");
  const scanRunsBody = document.getElementById("scan-runs-body");
  const dedupProjectsBody = document.getElementById("dedup-projects-body");
  const dedupRunsBody = document.getElementById("dedup-runs-body");
  const rescanBtn = document.getElementById("rescan-btn");
  const rescanStatus = document.getElementById("rescan-status");

  let searchKey = "path";
  let nextOffset = 0;
  let lastSearchValue = "";
  let scansLoaded = false;
  let lastScanFinishedAt = "";
  let rescanPollTimer = null;
  let lastResultRows = [];
  let expandedRowIdx = null;
  const detailCache = new Map();
  const MAX_RESULT_ROWS = 300;
  const STATS_POLL_MS = 60000;
  let statsPollTimer = null;

  const hints = {
    path:
      "Путь относительно SCAN_PATH. Пустой запрос — обзор первых 50 файлов. Подстрока или fnmatch: Released/Quik*, *lib.so*.debug",
    name:
      "Имя файла (basename): quik-16.0.0.10.debug, *.debug, libQt5*. Введите запрос и нажмите «Найти».",
    buildid:
      "Префикс hex build-id (необязательно). Пустой запрос — первые 50. Длинные SHA показываются сокращённо — кликните строку для деталей.",
  };

  const placeholders = {
    path: "Released/Quik* или build_*/*.debug",
    name: "Имя файла, например quik-16.0.0.10.debug",
    buildid: "Префикс build-id, например 006f5ce9",
  };

  function formatNumber(n) {
    return new Intl.NumberFormat("ru-RU").format(n);
  }

  function formatBytes(bytes) {
    if (!bytes || bytes === 0) return "0 B";
    const units = ["B", "KB", "MB", "GB", "TB"];
    const i = Math.floor(Math.log(bytes) / Math.log(1024));
    return (bytes / Math.pow(1024, i)).toFixed(1) + " " + units[i];
  }

  function formatDuration(seconds) {
    if (seconds < 60) return seconds + " с";
    if (seconds < 3600) return Math.floor(seconds / 60) + " мин";
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    return h + " ч " + m + " мин";
  }

  function formatMs(ms) {
    if (ms < 1000) return ms + " ms";
    return (ms / 1000).toFixed(1) + " с";
  }

  function formatDate(iso) {
    if (!iso) return "—";
    try {
      return new Date(iso).toLocaleString("ru-RU");
    } catch (_) {
      return iso;
    }
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function shortBuildID(id) {
    if (!id || id.length <= 16) return id;
    return id.slice(0, 8) + "…" + id.slice(-6);
  }

  function splitRelativePath(rel) {
    if (!rel) return { dir: "—", file: "—" };
    const i = rel.lastIndexOf("/");
    if (i < 0) return { dir: "—", file: rel };
    return { dir: rel.slice(0, i), file: rel.slice(i + 1) };
  }

  function typeBadges(types) {
    const list = types || [];
    if (!list.length) return '<span class="muted">—</span>';
    return list
      .map(function (t) {
        return (
          '<span class="type-badge ' + escapeHtml(t) + '">' + escapeHtml(t) + "</span>"
        );
      })
      .join(" ");
  }

  function artifactLinks(buildid, types) {
    const available = (types || []).filter(function (t) {
      return t === "debuginfo" || t === "executable";
    });
    if (!available.length) {
      return '<span class="muted">—</span>';
    }
    return available
      .map(function (t) {
        return (
          '<a class="type-badge ' +
          t +
          '" href="/buildid/' +
          encodeURIComponent(buildid) +
          "/" +
          t +
          '" download>' +
          escapeHtml(t) +
          "</a>"
        );
      })
      .join("");
  }

  function setMainTab(tab) {
    mainTabs.forEach(function (btn) {
      const active = btn.dataset.tab === tab;
      btn.classList.toggle("active", active);
      btn.setAttribute("aria-selected", active ? "true" : "false");
    });
    tabDashboard.classList.toggle("active", tab === "dashboard");
    tabDashboard.hidden = tab !== "dashboard";
    tabScans.classList.toggle("active", tab === "scans");
    tabScans.hidden = tab !== "scans";
    if (tab === "scans" && !scansLoaded) {
      loadScans();
    }
  }

  function normalizeRow(row) {
    if (row.entries && row.entries.length) {
      return row;
    }
    const entry = {
      buildid: row.buildid,
      type: row.type,
      file: row.file,
      file_path: row.file_path,
      archive: row.archive,
      archive_path: row.archive_path,
      archive_rel: row.archive_rel,
      member_path: row.member_path,
      buildid_kind: row.buildid_kind,
      raw_buildid: row.raw_buildid,
      relative_path: row.relative_path,
      filename: row.filename,
      directory: row.directory,
      mtime_ns: row.mtime_ns,
      mtime: row.mtime,
      comment: row.comment,
    };
    return {
      buildid: row.buildid,
      types: row.type ? [row.type] : row.types || [],
      buildid_kind: row.buildid_kind,
      raw_buildid: row.raw_buildid,
      relative_path: row.relative_path,
      filename: row.filename,
      directory: row.directory,
      entries: [entry],
      sources: row.sources || [],
      sources_count: row.sources_count || 0,
    };
  }

  function detailField(label, value, mono) {
    if (value === undefined || value === null || value === "") return "";
    return (
      '<div class="detail-field"><span class="detail-label">' +
      escapeHtml(label) +
      '</span><span class="detail-value' +
      (mono ? " mono" : "") +
      '">' +
      escapeHtml(String(value)) +
      "</span></div>"
    );
  }

  function renderCommentBlock(comment) {
    if (
      !comment ||
      ((!comment.lines || !comment.lines.length) && !comment.git_commit)
    ) {
      return "";
    }
    let html =
      '<div class="detail-section comment-section"><h4>ELF .comment</h4><div class="detail-grid">';
    html += detailField("Toolchain", comment.toolchain, false);
    html += detailField("Copyright", comment.copyright, false);
    if (comment.labels && comment.labels.length) {
      html += detailField("Метки", comment.labels.join(" · "), false);
    }
    html += detailField("Версия продукта", comment.product_version, false);
    html += detailField("Git commit", comment.git_commit, true);
    html += "</div>";
    if (comment.lines && comment.lines.length) {
      html +=
        '<pre class="comment-raw">' +
        escapeHtml(comment.lines.join("\n")) +
        "</pre>";
    }
    html += "</div>";
    return html;
  }

  function renderEntryBlock(entry, buildid) {
    const bid = entry.buildid || buildid || "";
    const download =
      entry.type === "debuginfo" || entry.type === "executable"
        ? '<a class="type-badge ' +
          entry.type +
          '" href="/buildid/' +
          encodeURIComponent(bid) +
          "/" +
          entry.type +
          '">/buildid/' +
          escapeHtml(bid) +
          "/" +
          entry.type +
          "</a>"
        : "";
    return (
      '<div class="detail-entry">' +
      "<h4>" +
      typeBadges([entry.type]) +
      "</h4>" +
      '<div class="detail-grid">' +
      detailField("Отн. путь", entry.relative_path, true) +
      detailField("Каталог", entry.directory, true) +
      detailField("Файл", entry.filename, true) +
      detailField("Абс. путь", entry.file_path, true) +
      detailField("Архив (отн.)", entry.archive_rel, true) +
      detailField("Архив (абс.)", entry.archive_path || entry.archive, true) +
      detailField("Member", entry.member_path, true) +
      detailField("Mtime", entry.mtime, false) +
      detailField("Mtime (ns)", entry.mtime_ns, false) +
      "</div>" +
      renderCommentBlock(entry.comment) +
      (download ? '<div class="detail-links">' + download + "</div>" : "") +
      "</div>"
    );
  }

  function renderSourcesBlock(row) {
    const sources = row.sources || [];
    const total = row.sources_count || sources.length;
    if (!total) return "";
    let html =
      '<div class="detail-section"><h4>Исходники (' +
      formatNumber(total) +
      ")</h4>";
    if (!sources.length) {
      html += '<p class="muted">Показаны не все — см. /buildid/…/source</p>';
    } else {
      html +=
        '<table class="detail-table"><thead><tr><th>source path</th><th>отн. путь</th><th>архив</th><th>mtime</th></tr></thead><tbody>';
      sources.forEach(function (s) {
        html +=
          "<tr><td class='mono'>" +
          escapeHtml(s.source_path) +
          "</td><td class='mono'>" +
          escapeHtml(s.relative_path) +
          "</td><td class='mono'>" +
          escapeHtml(s.archive_rel || s.archive_path || "—") +
          "</td><td>" +
          escapeHtml(s.mtime || "—") +
          "</td></tr>";
      });
      html += "</tbody></table>";
    }
    html += "</div>";
    return html;
  }

  function renderDetailInner(row) {
    const data = normalizeRow(row);
    const buildid = data.buildid || "";
    const copyBtn =
      '<button type="button" class="copy-btn" data-copy="' +
      escapeHtml(buildid) +
      '">копировать build-id</button>';
    let html =
      '<div class="detail-section"><h4>Идентификация</h4><div class="detail-grid">' +
      detailField("Build-ID", buildid, true) +
      detailField("Raw build-id", data.raw_buildid, true) +
      detailField("Build-ID kind", data.buildid_kind, false) +
      "</div>" +
      copyBtn +
      "</div>";

    const entries = data.entries || [];
    html +=
      '<div class="detail-section"><h4>Артефакты по типам (' +
      entries.length +
      ")</h4>";
    entries.forEach(function (entry) {
      html += renderEntryBlock(entry, buildid);
    });
    html += "</div>";

    html += renderSourcesBlock(data);

    const types = data.types || [];
    if (types.length) {
      html +=
        '<div class="detail-section"><h4>API</h4><div class="detail-links">' +
        artifactLinks(buildid, types) +
        ' <a class="badge link" href="/metadata?key=buildid&amp;value=' +
        encodeURIComponent(buildid) +
        '" target="_blank" rel="noopener">metadata</a>' +
        "</div></div>";
    }
    return html;
  }

  function bindCopyButtons(root) {
    if (!root) return;
    root.querySelectorAll(".copy-btn").forEach(function (btn) {
      btn.addEventListener("click", function (e) {
        e.stopPropagation();
        const text = btn.getAttribute("data-copy") || "";
        navigator.clipboard.writeText(text).catch(function () {});
        btn.textContent = "скопировано";
        setTimeout(function () {
          btn.textContent = "копировать build-id";
        }, 1500);
      });
    });
  }

  function refreshExpandedRows() {
    resultsBody.querySelectorAll(".artifact-row").forEach(function (tr) {
      const idx = parseInt(tr.getAttribute("data-idx") || "-1", 10);
      const expanded = idx === expandedRowIdx;
      tr.classList.toggle("expanded", expanded);
      const toggle = tr.querySelector(".col-toggle");
      if (toggle) toggle.textContent = expanded ? "▼" : "›";
    });
    resultsBody.querySelectorAll(".artifact-detail-row").forEach(function (tr) {
      const idx = parseInt(tr.getAttribute("data-detail-for") || "-1", 10);
      const expanded = idx === expandedRowIdx;
      tr.hidden = !expanded;
      if (expanded) {
        const cell = tr.querySelector(".artifact-detail-cell");
        if (cell && detailCache.has(idx)) {
          cell.innerHTML = detailCache.get(idx);
        }
      }
    });
  }

  function toggleRow(idx) {
    if (expandedRowIdx === idx) {
      expandedRowIdx = null;
      refreshExpandedRows();
      return;
    }
    expandedRowIdx = idx;
    refreshExpandedRows();
    if (!detailCache.has(idx)) {
      loadRowDetail(idx);
    } else {
      bindCopyButtons(resultsBody);
    }
  }

  async function loadRowDetail(idx) {
    const row = normalizeRow(lastResultRows[idx]);
    const buildid = row.buildid;
    if (!buildid) {
      detailCache.set(idx, '<p class="muted">Нет build-id</p>');
      refreshExpandedRows();
      return;
    }
    const cell = resultsBody.querySelector(
      '.artifact-detail-row[data-detail-for="' + idx + '"] .artifact-detail-cell'
    );
    if (cell) {
      cell.innerHTML = '<div class="muted">Загрузка…</div>';
    }
    try {
      const res = await fetch("/ui/api/artifact/" + encodeURIComponent(buildid));
      if (!res.ok) {
        throw new Error("HTTP " + res.status);
      }
      const data = await res.json();
      detailCache.set(idx, renderDetailInner(data));
    } catch (err) {
      detailCache.set(idx, '<p class="error">Ошибка: ' + escapeHtml(err.message) + "</p>");
    }
    refreshExpandedRows();
    bindCopyButtons(resultsBody);
  }

  function clearSearchResults(message) {
    nextOffset = 0;
    lastSearchValue = "";
    expandedRowIdx = null;
    detailCache.clear();
    searchStatus.textContent = message || "";
    searchStatus.classList.remove("error");
    resultsTable.hidden = true;
    resultsBody.innerHTML = "";
    loadMoreBtn.hidden = true;
  }

  function scheduleStatsPoll() {
    if (statsPollTimer) {
      clearInterval(statsPollTimer);
    }
    statsPollTimer = setInterval(function () {
      if (!document.hidden) {
        loadStats();
      }
    }, STATS_POLL_MS);
  }

  function setSearchMode(key) {
    searchKey = key;
    nextOffset = 0;
    modeButtons.forEach(function (btn) {
      const active = btn.dataset.key === key;
      btn.classList.toggle("active", active);
      btn.setAttribute("aria-selected", active ? "true" : "false");
    });
    searchInput.placeholder = placeholders[key] || "";
    searchHint.textContent = hints[key] || "";
    searchInput.value = "";
    if (key === "name") {
      clearSearchResults("Введите имя файла и нажмите «Найти»");
    } else {
      clearSearchResults("Нажмите «Найти» для обзора первых 50 результатов");
    }
  }

  async function loadStats() {
    try {
      const res = await fetch("/ui/api/stats");
      if (!res.ok) throw new Error("HTTP " + res.status);
      const data = await res.json();
      renderStats(data);
    } catch (err) {
      statsGrid.innerHTML =
        '<div class="stat-card loading"><span class="stat-label">Ошибка загрузки статистики</span></div>';
    }
  }

  function renderStats(data) {
    uptimeEl.textContent = "uptime " + formatDuration(data.uptime_seconds);

    let dedupValue;
    let dedupLabel = "Экономия dedup";
    if (data.dedup_enabled) {
      const pct =
        data.dedup_saved_percent > 0
          ? " (" + data.dedup_saved_percent.toFixed(1) + "%)"
          : "";
      dedupValue = formatBytes(data.dedup_bytes_saved || 0) + pct;
    } else {
      dedupValue = "выключено";
      dedupLabel = "Экономия dedup (DEBUGINFOD_DEDUP_ENABLED=false)";
    }

    const cards = [
      { label: "Артефакты", value: data.artifacts_total, highlight: true },
      { label: "Executable", value: data.artifacts_executable },
      { label: "Debuginfo", value: data.artifacts_debuginfo },
      { label: "Исходники", value: data.sources_total },
      { label: "Просканировано файлов", value: data.scanned_files_total },
      {
        label: "Объём индекса",
        value: formatBytes(data.index_bytes_on_disk || 0),
      },
      { label: dedupLabel, value: dedupValue, highlight: !!data.dedup_enabled },
      { label: "HTTP запросов (API)", value: data.http_requests_total },
      { label: "Кэш", value: formatBytes(data.cache_bytes) },
    ];

    statsGrid.innerHTML = cards
      .map(function (c) {
        const cls = c.highlight ? "stat-card highlight" : "stat-card";
        const val =
          typeof c.value === "number" ? formatNumber(c.value) : c.value;
        return (
          '<div class="' +
          cls +
          '"><span class="stat-value">' +
          escapeHtml(String(val)) +
          '</span><span class="stat-label">' +
          escapeHtml(c.label) +
          "</span></div>"
        );
      })
      .join("");

    const scanParts = [
      "<span class='scan-item'><strong>" +
        formatNumber(data.last_scan_indexed) +
        "</strong> <span>проиндексировано</span></span>",
      "<span class='scan-item' title='Файлы без изменений (mtime/size) с прошлого scan, а также ELF без build-id'>" +
        "<strong>" +
        formatNumber(data.last_scan_skipped) +
        "</strong> <span>пропущено</span></span>",
      "<span class='scan-item'><strong>" +
        formatNumber(data.last_scan_errors) +
        "</strong> <span>ошибок</span></span>",
      "<span class='scan-item'><strong>" +
        formatNumber(data.last_scan_duration_ms) +
        " ms</strong> <span>длительность</span></span>",
    ];
    if (data.last_scan_finished_at) {
      scanParts.push(
        "<span class='scan-item'><strong>" +
          escapeHtml(data.last_scan_finished_at) +
          "</strong> <span>завершено</span></span>"
      );
    }
    scanInfo.innerHTML = scanParts.join("");

    if (data.last_scan_finished_at) {
      lastScanFinishedAt = data.last_scan_finished_at;
    }

    if (rescanBtn) {
      rescanBtn.hidden = !data.scan_enabled;
    }
  }

  async function triggerRescan() {
    if (!rescanBtn || rescanBtn.disabled) return;
    rescanBtn.disabled = true;
    if (rescanStatus) {
      rescanStatus.hidden = false;
      rescanStatus.textContent = "запуск…";
    }
    const startedAt = lastScanFinishedAt;
    try {
      const res = await fetch("/ui/api/rescan", { method: "POST" });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || "HTTP " + res.status);
      }
      if (rescanStatus) rescanStatus.textContent = "сканирование…";
      await waitForScanComplete(startedAt);
      await loadStats();
      if (tabScans && !tabScans.hidden) {
        scansLoaded = false;
        await loadScans();
      }
      doSearch(lastSearchValue, false);
      if (rescanStatus) {
        rescanStatus.textContent = "готово";
        setTimeout(function () {
          rescanStatus.hidden = true;
          rescanStatus.textContent = "";
        }, 3000);
      }
    } catch (err) {
      if (rescanStatus) {
        rescanStatus.hidden = false;
        rescanStatus.textContent = "ошибка";
        rescanStatus.title = err.message;
      }
    } finally {
      rescanBtn.disabled = false;
    }
  }

  function waitForScanComplete(since) {
    const deadline = Date.now() + 5 * 60 * 1000;
    return new Promise(function (resolve, reject) {
      function poll() {
        if (Date.now() > deadline) {
          reject(new Error("таймаут ожидания scan"));
          return;
        }
        fetch("/ui/api/stats")
          .then(function (res) {
            if (!res.ok) throw new Error("HTTP " + res.status);
            return res.json();
          })
          .then(function (data) {
            if (data.last_scan_finished_at && data.last_scan_finished_at !== since) {
              lastScanFinishedAt = data.last_scan_finished_at;
              resolve();
              return;
            }
            rescanPollTimer = setTimeout(poll, 2000);
          })
          .catch(reject);
      }
      poll();
    });
  }

  async function loadScans() {
    try {
      const res = await fetch("/ui/api/scans?limit=50");
      if (!res.ok) throw new Error("HTTP " + res.status);
      const data = await res.json();
      renderScans(data);
      scansLoaded = true;
    } catch (err) {
      indexSummary.textContent = "Ошибка: " + err.message;
      dedupSummary.textContent = "Ошибка загрузки";
      if (dedupStatus) dedupStatus.textContent = "";
      scanRunsBody.innerHTML =
        '<tr><td colspan="8" class="muted">Ошибка загрузки</td></tr>';
      dedupRunsBody.innerHTML =
        '<tr><td colspan="9" class="muted">Ошибка загрузки</td></tr>';
      if (dedupProjectsBody) {
        dedupProjectsBody.innerHTML =
          '<tr><td colspan="9" class="muted">Ошибка загрузки</td></tr>';
      }
    }
  }

  function renderScans(data) {
    const idx = data.index_summary || {};
    indexSummary.innerHTML = [
      summaryItem(formatNumber(idx.artifacts_total), "артефактов"),
      summaryItem(formatNumber(idx.artifacts_executable), "executable"),
      summaryItem(formatNumber(idx.artifacts_debuginfo), "debuginfo"),
      summaryItem(formatNumber(idx.scanned_files_total), "файлов"),
      summaryItem(formatBytes(idx.bytes_on_disk), "на диске"),
    ].join("");

    const dedupEnabled = !!data.dedup_enabled;
    const t = data.dedup_totals || {};
    const savedPct =
      t.saved_percent > 0 ? t.saved_percent.toFixed(1) + "%" : "—";

    if (!dedupEnabled) {
      dedupSummary.innerHTML =
        '<div class="summary-item muted"><span>Хранение .debug выключено (DEBUGINFOD_DEDUP_ENABLED=false)</span></div>';
      if (dedupStatus) {
        dedupStatus.textContent =
          "Включите DEBUGINFOD_DEDUP_ENABLED=true — ingest decompress-dwz + xdelta3 между build_* (см. docs/QUIK_DEDUP.md).";
      }
    } else {
      dedupSummary.innerHTML = [
        summaryItem(formatNumber(t.files_done), "файлов обработано"),
        summaryItem(formatNumber(t.files_base), "base"),
        summaryItem(formatNumber(t.files_delta), "delta"),
        summaryItem(formatNumber(t.files_full), "singleton"),
        summaryItem(formatBytes(t.bytes_original), "исходный объём"),
        summaryItem(formatBytes(t.bytes_on_disk), "на диске"),
        summaryItem(formatBytes(t.bytes_saved) + " (" + savedPct + ")", "экономия"),
      ].join("");
      if (dedupStatus) dedupStatus.textContent = "";
    }

    renderDedupByProject(data, dedupEnabled);

    if (!data.index_scans || data.index_scans.length === 0) {
      scanRunsBody.innerHTML =
        '<tr><td colspan="8" class="muted">Нет записей (ожидается после первого scan)</td></tr>';
    } else {
      scanRunsBody.innerHTML = data.index_scans
        .map(function (r) {
          return (
            "<tr>" +
            "<td>" + escapeHtml(formatDate(r.finished_at)) + "</td>" +
            "<td>" + escapeHtml(formatMs(r.duration_ms)) + "</td>" +
            "<td>" + formatNumber(r.indexed) + "</td>" +
            "<td>" + formatNumber(r.skipped) + "</td>" +
            "<td>" + formatNumber(r.errors) + "</td>" +
            "<td>" + formatNumber(r.artifacts_total) + "</td>" +
            "<td>" + formatNumber(r.scanned_files) + "</td>" +
            "<td>" + escapeHtml(formatBytes(r.bytes_on_disk)) + "</td>" +
            "</tr>"
          );
        })
        .join("");
    }

    if (!dedupEnabled) {
      dedupRunsBody.innerHTML =
        '<tr><td colspan="9" class="muted">Хранение .debug выключено</td></tr>';
    } else if (!data.dedup_runs || data.dedup_runs.length === 0) {
      dedupRunsBody.innerHTML =
        '<tr><td colspan="9" class="muted">Нет записей (ожидается после scan с build_* и DEBUGINFOD_DEDUP_ENABLED=true)</td></tr>';
    } else {
      dedupRunsBody.innerHTML = data.dedup_runs
        .map(function (r) {
          const saved =
            r.bytes_saved > 0
              ? formatBytes(r.bytes_saved) +
                " (" +
                (r.saved_percent || 0).toFixed(1) +
                "%)"
              : "—";
          const deltaCount = r.files_compressed || 0;
          const groupCount = r.files_skipped || 0;
          return (
            "<tr>" +
            "<td>" + escapeHtml(formatDate(r.finished_at)) + "</td>" +
            "<td>" + escapeHtml(formatMs(r.duration_ms)) + "</td>" +
            "<td>" + escapeHtml(r.project || "все") + "</td>" +
            "<td>" + formatNumber(deltaCount) + "</td>" +
            "<td>" + formatNumber(groupCount) + "</td>" +
            "<td>" + formatNumber(r.errors) + "</td>" +
            "<td>" + formatBytes(r.bytes_before) + "</td>" +
            "<td>" + formatBytes(r.bytes_after) + "</td>" +
            "<td>" + saved + "</td>" +
            "</tr>"
          );
        })
        .join("");
    }
  }

  function renderDedupByProject(data, dedupEnabled) {
    if (!dedupProjectsBody) return;
    if (!dedupEnabled) {
      dedupProjectsBody.innerHTML =
        '<tr><td colspan="9" class="muted">Хранение .debug выключено</td></tr>';
      return;
    }
    const rows = (data.dedup_by_project || []).slice().sort(function (a, b) {
      return String(a.project).localeCompare(String(b.project), "ru");
    });
    if (!rows.length) {
      dedupProjectsBody.innerHTML =
        '<tr><td colspan="9" class="muted">Нет данных (ожидается после scan с каталогами build_*)</td></tr>';
      return;
    }
    dedupProjectsBody.innerHTML = rows
      .map(function (r) {
        const saved =
          r.bytes_saved > 0
            ? formatBytes(r.bytes_saved) +
              " (" +
              (r.saved_percent || 0).toFixed(1) +
              "%)"
            : "—";
        return (
          "<tr>" +
          '<td class="mono">' + escapeHtml(r.project) + "</td>" +
          "<td>" + formatNumber(r.build_dirs) + "</td>" +
          "<td>" + formatNumber(r.files_done) + "</td>" +
          "<td>" + formatNumber(r.files_base) + "</td>" +
          "<td>" + formatNumber(r.files_delta) + "</td>" +
          "<td>" + formatNumber(r.files_full) + "</td>" +
          "<td>" + formatBytes(r.bytes_original) + "</td>" +
          "<td>" + formatBytes(r.bytes_on_disk) + "</td>" +
          "<td>" + saved + "</td>" +
          "</tr>"
        );
      })
      .join("");
  }

  function summaryItem(value, label) {
    return (
      '<div class="summary-item"><strong>' +
      escapeHtml(String(value)) +
      "</strong><span>" +
      escapeHtml(label) +
      "</span></div>"
    );
  }

  function buildSearchParams(value, append) {
    const params = new URLSearchParams();
    params.set("key", searchKey);
    if (searchKey === "buildid") {
      if (value) params.set("q", value);
    } else {
      if (value) params.set("value", value);
      if (append && nextOffset > 0) {
        params.set("offset", String(nextOffset));
      }
    }
    return params;
  }

  async function doSearch(query, append) {
    if (!append && searchKey === "name" && !query) {
      clearSearchResults("Введите имя файла и нажмите «Найти»");
      return;
    }

    if (!append) {
      nextOffset = 0;
      lastSearchValue = query;
      searchStatus.textContent = "Поиск…";
      searchStatus.classList.remove("error");
      resultsTable.hidden = true;
      loadMoreBtn.hidden = true;
    } else {
      loadMoreBtn.disabled = true;
      loadMoreBtn.textContent = "Загрузка…";
    }

    try {
      const params = buildSearchParams(append ? lastSearchValue : query, append);
      const res = await fetch("/ui/api/search?" + params.toString());
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || "HTTP " + res.status);
      }
      const data = await res.json();
      renderResults(data, append);
    } catch (err) {
      searchStatus.textContent = "Ошибка поиска: " + err.message;
      searchStatus.classList.add("error");
      loadMoreBtn.hidden = true;
    } finally {
      loadMoreBtn.disabled = false;
      loadMoreBtn.textContent = "Ещё результаты";
    }
  }

  function renderDetailRow(idx) {
    const hidden = idx !== expandedRowIdx;
    const inner =
      expandedRowIdx === idx && detailCache.has(idx)
        ? detailCache.get(idx)
        : expandedRowIdx === idx
          ? '<div class="muted">Загрузка…</div>'
          : "";
    return (
      '<tr class="artifact-detail-row" data-detail-for="' +
      idx +
      '"' +
      (hidden ? " hidden" : "") +
      '><td colspan="6" class="artifact-detail-cell">' +
      inner +
      "</td></tr>"
    );
  }

  function renderGroupedRow(row, idx) {
    const types = row.types || [];
    const rel = row.relative_path || row.file || "—";
    const parts = splitRelativePath(rel);
    const expanded = idx === expandedRowIdx;
    return (
      '<tr class="artifact-row' +
      (expanded ? " expanded" : "") +
      '" tabindex="0" data-idx="' +
      idx +
      '">' +
      '<td class="col-toggle" title="Подробнее">' +
      (expanded ? "▼" : "›") +
      "</td>" +
      '<td class="mono path-cell" title="' +
      escapeHtml(rel) +
      '">' +
      escapeHtml(parts.dir) +
      "</td>" +
      '<td class="mono" title="' +
      escapeHtml(parts.file) +
      '">' +
      escapeHtml(parts.file) +
      "</td>" +
      "<td>" +
      typeBadges(types) +
      "</td>" +
      '<td class="mono" title="' +
      escapeHtml(row.buildid) +
      '">' +
      escapeHtml(shortBuildID(row.buildid)) +
      "</td>" +
      '<td class="links">' +
      artifactLinks(row.buildid, types) +
      "</td>" +
      "</tr>" +
      renderDetailRow(idx)
    );
  }

  function renderFlatRow(row, idx) {
    const types = [row.type];
    const rel = row.relative_path || row.file || "—";
    const parts = splitRelativePath(rel);
    const expanded = idx === expandedRowIdx;
    return (
      '<tr class="artifact-row' +
      (expanded ? " expanded" : "") +
      '" tabindex="0" data-idx="' +
      idx +
      '">' +
      '<td class="col-toggle" title="Подробнее">' +
      (expanded ? "▼" : "›") +
      "</td>" +
      '<td class="mono path-cell" title="' +
      escapeHtml(rel) +
      '">' +
      escapeHtml(parts.dir) +
      "</td>" +
      '<td class="mono">' +
      escapeHtml(parts.file || row.filename || "—") +
      "</td>" +
      "<td>" +
      typeBadges(types) +
      "</td>" +
      '<td class="mono" title="' +
      escapeHtml(row.buildid) +
      '">' +
      escapeHtml(shortBuildID(row.buildid)) +
      "</td>" +
      '<td class="links">' +
      artifactLinks(row.buildid, types) +
      "</td>" +
      "</tr>" +
      renderDetailRow(idx)
    );
  }

  function bindResultRows() {
    resultsBody.querySelectorAll(".artifact-row").forEach(function (tr) {
      tr.addEventListener("click", function (e) {
        if (e.target.closest("a") || e.target.closest("button")) return;
        const idx = parseInt(tr.getAttribute("data-idx") || "-1", 10);
        if (idx >= 0) toggleRow(idx);
      });
    });
    bindCopyButtons(resultsBody);
  }

  function renderResults(data, append) {
    let label = "";
    if (searchKey === "buildid") {
      label = data.query ? ' по «' + data.query + '»' : " (обзор)";
    } else if (searchKey === "path") {
      label = data.value ? ' по пути «' + data.value + '»' : " (обзор)";
    } else {
      label = data.value ? ' по имени «' + data.value + '»' : "";
    }

    const rows = data.grouped && data.grouped.length ? data.grouped : data.results || [];
    const baseIdx = append ? lastResultRows.length : 0;
    if (!append) {
      lastResultRows = rows.slice();
      expandedRowIdx = null;
      detailCache.clear();
    } else {
      const room = MAX_RESULT_ROWS - lastResultRows.length;
      if (room <= 0) {
        searchStatus.textContent =
          "Показано максимум " + formatNumber(MAX_RESULT_ROWS) + " строк — уточните запрос";
        loadMoreBtn.hidden = true;
        return;
      }
      lastResultRows = lastResultRows.concat(rows.slice(0, room));
    }

    const totalShown = lastResultRows.length;

    let status = "Найдено: " + formatNumber(rows.length) + label;
    if (append) {
      status = "Показано: " + formatNumber(totalShown) + label;
    }
    if (!data.complete) {
      status += " (есть ещё — нажмите «Ещё результаты»)";
    }
    searchStatus.textContent = status;

    if (!rows.length) {
      if (!append) resultsTable.hidden = true;
      loadMoreBtn.hidden = true;
      return;
    }

    const html = rows
      .map(function (row, i) {
        const idx = baseIdx + i;
        return data.grouped ? renderGroupedRow(row, idx) : renderFlatRow(row, idx);
      })
      .join("");

    if (append) {
      resultsBody.insertAdjacentHTML("beforeend", html);
    } else {
      resultsBody.innerHTML = html;
    }
    bindResultRows();

    resultsTable.hidden = false;
    nextOffset = data.next_offset || 0;
    loadMoreBtn.hidden =
      data.complete || !nextOffset || lastResultRows.length >= MAX_RESULT_ROWS;
  }

  mainTabs.forEach(function (btn) {
    btn.addEventListener("click", function () {
      setMainTab(btn.dataset.tab);
    });
  });

  modeButtons.forEach(function (btn) {
    btn.addEventListener("click", function () {
      setSearchMode(btn.dataset.key);
    });
  });

  searchForm.addEventListener("submit", function (e) {
    e.preventDefault();
    doSearch(searchInput.value.trim(), false);
  });

  loadMoreBtn.addEventListener("click", function () {
    doSearch(lastSearchValue, true);
  });

  if (rescanBtn) {
    rescanBtn.addEventListener("click", triggerRescan);
  }

  let debounce;
  searchInput.addEventListener("input", function () {
    if (searchKey !== "buildid" && searchKey !== "path") {
      return;
    }
    clearTimeout(debounce);
    debounce = setTimeout(function () {
      doSearch(searchInput.value.trim(), false);
    }, 600);
  });

  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) {
      loadStats();
    }
  });

  loadStats();
  scheduleStatsPoll();
  setMainTab("dashboard");
  clearSearchResults("Нажмите «Найти» для обзора первых 50 результатов");
})();
