(function () {
  const statsGrid = document.getElementById("stats-grid");
  const scanInfo = document.getElementById("scan-info");
  const uptimeEl = document.getElementById("uptime");
  const searchForm = document.getElementById("search-form");
  const searchInput = document.getElementById("search-input");
  const searchStatus = document.getElementById("search-status");
  const browseTree = document.getElementById("browse-tree");
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

  let lastSearchValue = "";
  let scansLoaded = false;
  let lastScanFinishedAt = "";
  let browseRequestId = 0;

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
    if (ms === undefined || ms === null || ms < 0) return "—";
    const totalSec = Math.floor(ms / 1000);
    if (totalSec > 59 * 60) {
      const h = Math.floor(totalSec / 3600);
      const m = Math.floor((totalSec % 3600) / 60);
      const s = totalSec % 60;
      const parts = [h + " ч"];
      if (m > 0) parts.push(m + " мин");
      if (s > 0) parts.push(s + " с");
      return parts.join(" ");
    }
    if (totalSec > 60) {
      const m = Math.floor(totalSec / 60);
      const s = totalSec % 60;
      return s > 0 ? m + " мин " + s + " с" : m + " мин";
    }
    if (totalSec === 60) return "1 мин";
    if (totalSec < 1 && ms > 0) return "<1 с";
    return totalSec + " с";
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
    const div = document.createElement("div");
    div.textContent = s;
    return div.innerHTML;
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
      '<div class="file-comment"><div class="detail-grid">';
    html += detailField("Toolchain", comment.toolchain, false);
    html += detailField("Copyright", comment.copyright, false);
    if (comment.labels && comment.labels.length) {
      html += detailField("Метки", comment.labels.join(" · "), false);
    }
    html += detailField("Версия", comment.product_version, false);
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

  function downloadHref(file) {
    if (file.dedup_id) {
      return "/ui/api/download/dedup/" + encodeURIComponent(file.dedup_id);
    }
    const type = file.type === "executable" ? "executable" : "debuginfo";
    return "/buildid/" + encodeURIComponent(file.buildid) + "/" + type;
  }

  function renderFileRow(file) {
    const name = file.filename || "—";
    const href = downloadHref(file);
    const commit = file.git_commit || (file.comment && file.comment.git_commit);
    const commitHtml = commit
      ? '<span class="file-commit mono" title="git commit">' +
        escapeHtml(commit.slice(0, 12)) +
        "…</span>"
      : "";
    const rowKey = file.dedup_id
      ? "dedup-" + file.dedup_id
      : (file.buildid || "file") + "-" + name;
    const detailsId = "file-" + escapeHtml(rowKey).replace(/[^a-zA-Z0-9_-]/g, "_");
    const hasDetails =
      commit ||
      (file.comment &&
        ((file.comment.lines && file.comment.lines.length) ||
          file.comment.toolchain ||
          file.comment.copyright));

    let html =
      '<div class="tree-file">' +
      '<a class="file-download mono" href="' +
      href +
      '" download="' +
      escapeHtml(name) +
      '" title="' +
      escapeHtml(file.relative_path || name) +
      '">' +
      escapeHtml(name) +
      "</a>" +
      commitHtml;

    if (hasDetails) {
      html +=
        '<button type="button" class="file-info-btn" aria-expanded="false" data-target="' +
        detailsId +
        '">i</button>' +
        '<div class="file-details" id="' +
        detailsId +
        '" hidden>' +
        detailField("Путь", file.relative_path, true) +
        renderCommentBlock(file.comment) +
        "</div>";
    }

    html += "</div>";
    return html;
  }

  function countTreeFiles(node) {
    let n = (node.files || []).length;
    (node.children || []).forEach(function (child) {
      n += countTreeFiles(child);
    });
    return n;
  }

  function renderTreeNode(node, depth, expandAll) {
    const files = node.files || [];
    const children = node.children || [];
    const fileCount = countTreeFiles(node);
    const label =
      escapeHtml(node.name) +
      ' <span class="tree-count">' +
      formatNumber(fileCount) +
      "</span>";

    const nodeClass =
      "tree-node" +
      (depth === 0 ? " tree-commit" : "") +
      (children.length === 0 && files.length > 0 && depth > 0 ? " tree-leaf-dir" : "");
    const openAttr = expandAll ? " open" : "";
    const summaryTitle =
      depth === 0 && node.path && node.path !== node.name
        ? ' title="' + escapeHtml(node.path) + '"'
        : "";

    if (children.length === 0 && files.length > 0 && depth > 0) {
      let html = '<details class="' + nodeClass + '"' + openAttr + ">";
      html += "<summary" + summaryTitle + ">" + label + "</summary>";
      html += '<div class="tree-files">';
      files.forEach(function (f) {
        html += renderFileRow(f);
      });
      html += "</div></details>";
      return html;
    }

    let html = '<details class="' + nodeClass + '"' + openAttr + ">";
    html += "<summary" + summaryTitle + ">" + label + "</summary>";
    html += '<div class="tree-body">';

    if (files.length) {
      html += '<div class="tree-files">';
      files.forEach(function (f) {
        html += renderFileRow(f);
      });
      html += "</div>";
    }

    children.forEach(function (child) {
      html += renderTreeNode(child, depth + 1, expandAll);
    });

    html += "</div></details>";
    return html;
  }

  function renderBrowseTree(projects, expandAll) {
    if (!projects || !projects.length) {
      browseTree.innerHTML = '<p class="muted">Ничего не найдено</p>';
      browseTree.hidden = false;
      return;
    }

    browseTree.innerHTML = projects
      .map(function (project) {
        return renderTreeNode(project, 0, expandAll);
      })
      .join("");
    browseTree.hidden = false;
    if (expandAll) {
      expandBrowseTree();
    }
    bindFileInfoButtons();
  }

  function expandBrowseTree() {
    browseTree.querySelectorAll("details.tree-node").forEach(function (el) {
      el.open = true;
    });
  }

  function bindFileInfoButtons() {
    browseTree.querySelectorAll(".file-info-btn").forEach(function (btn) {
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        const id = btn.getAttribute("data-target");
        const panel = document.getElementById(id);
        if (!panel) return;
        const open = panel.hidden;
        panel.hidden = !open;
        btn.setAttribute("aria-expanded", open ? "true" : "false");
        btn.classList.toggle("active", open);
      });
    });
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
        escapeHtml(formatMs(data.last_scan_duration_ms)) +
        "</strong> <span>длительность</span></span>",
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
      doBrowse(lastSearchValue);
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
            setTimeout(poll, 2000);
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

  async function doBrowse(query) {
    lastSearchValue = query;
    const reqId = ++browseRequestId;
    searchStatus.textContent = "Поиск…";
    searchStatus.classList.remove("error");

    try {
      const params = new URLSearchParams();
      if (query) params.set("q", query);
      const res = await fetch("/ui/api/browse?" + params.toString());
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || "HTTP " + res.status);
      }
      const data = await res.json();
      if (reqId !== browseRequestId) return;

      let status = "Найдено файлов: " + formatNumber(data.count || 0);
      if (query) {
        status += ' по «' + query + '»';
      }
      if (data.complete === false) {
        status += " (показаны первые " + formatNumber(data.limit || 0) + ")";
      }
      searchStatus.textContent = status;

      renderBrowseTree(data.projects || [], !!query);
    } catch (err) {
      if (reqId !== browseRequestId) return;
      searchStatus.textContent = "Ошибка: " + err.message;
      searchStatus.classList.add("error");
      browseTree.hidden = true;
    }
  }

  mainTabs.forEach(function (btn) {
    btn.addEventListener("click", function () {
      setMainTab(btn.dataset.tab);
    });
  });

  searchForm.addEventListener("submit", function (e) {
    e.preventDefault();
    doBrowse(searchInput.value.trim());
  });

  if (rescanBtn) {
    rescanBtn.addEventListener("click", triggerRescan);
  }

  let debounce;
  searchInput.addEventListener("input", function () {
    clearTimeout(debounce);
    debounce = setTimeout(function () {
      doBrowse(searchInput.value.trim());
    }, 250);
  });

  loadStats();
  setInterval(loadStats, 30000);
  setMainTab("dashboard");
  doBrowse("");
})();
