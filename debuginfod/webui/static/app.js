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

  let searchKey = "buildid";
  let nextOffset = 0;
  let lastSearchValue = "";

  const hints = {
    buildid:
      "Пустой запрос — первые 50 артефактов. Поиск по префиксу build-id (hex).",
    glob: "Шаблон fnmatch, как в /metadata: /usr/bin/*, /usr/lib/debug/**",
    file: "Точный путь файла, как в /metadata?key=file&value=…",
  };

  const placeholders = {
    buildid: "Префикс build-id (hex), например deadbeef",
    glob: "Шаблон пути, например /usr/bin/*",
    file: "Абсолютный путь, например /usr/bin/ls",
  };

  function formatNumber(n) {
    return new Intl.NumberFormat("ru-RU").format(n);
  }

  function formatBytes(bytes) {
    if (bytes === 0) return "0 B";
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

  function escapeHtml(s) {
    const div = document.createElement("div");
    div.textContent = s;
    return div.innerHTML;
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
    loadMoreBtn.hidden = true;
    doSearch("", false);
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

    const cards = [
      { label: "Артефакты", value: data.artifacts_total, highlight: true },
      { label: "Executable", value: data.artifacts_executable },
      { label: "Debuginfo", value: data.artifacts_debuginfo },
      { label: "Исходники", value: data.sources_total },
      { label: "Просканировано файлов", value: data.scanned_files_total },
      { label: "HTTP запросов", value: data.http_requests_total },
      { label: "Blobs+кэш", value: formatBytes(data.cache_bytes) },
    ];

    if (data.bytes_saved && data.bytes_saved > 0) {
      cards.push({ label: "Сэкономлено (xdelta3)", value: formatBytes(data.bytes_saved) });
      cards.push({
        label: "Сжатие",
        value: (data.compression_ratio * 100).toFixed(1) + "%",
      });
    }

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
      "<span class='scan-item'><strong>" +
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
  }

  function buildSearchParams(value, append) {
    const params = new URLSearchParams();
    params.set("key", searchKey);
    if (searchKey === "buildid") {
      if (value) params.set("q", value);
    } else {
      params.set("value", value);
      if (append && nextOffset > 0) {
        params.set("offset", String(nextOffset));
      }
    }
    return params;
  }

  async function doSearch(query, append) {
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

  function renderRow(row) {
    const typeCls = row.type === "executable" ? "executable" : "debuginfo";
    const file = row.archive ? row.archive + " → " + row.file : row.file;
    const links =
      '<a href="/buildid/' +
      encodeURIComponent(row.buildid) +
      '/debuginfo">debuginfo</a>' +
      '<a href="/buildid/' +
      encodeURIComponent(row.buildid) +
      '/executable">executable</a>';
    return (
      "<tr>" +
      '<td class="mono">' +
      escapeHtml(row.buildid) +
      "</td>" +
      '<td><span class="type-badge ' +
      typeCls +
      '">' +
      escapeHtml(row.type) +
      "</span></td>" +
      '<td class="mono">' +
      escapeHtml(file) +
      "</td>" +
      "<td>" +
      escapeHtml(row.buildid_kind || "—") +
      "</td>" +
      '<td class="links">' +
      links +
      "</td>" +
      "</tr>"
    );
  }

  function renderResults(data, append) {
    let label = "";
    if (searchKey === "buildid") {
      label = data.query ? ' по «' + data.query + '»' : "";
    } else {
      label = data.value ? ' (' + searchKey + ': «' + data.value + '»)' : "";
    }

    const totalShown = append
      ? resultsBody.querySelectorAll("tr").length + (data.results ? data.results.length : 0)
      : data.count;

    let status =
      "Найдено: " + formatNumber(append ? totalShown : data.count) + label;
    if (!data.complete) {
      status += " (есть ещё — нажмите «Ещё результаты»)";
    }
    searchStatus.textContent = status;

    if (!data.results || data.results.length === 0) {
      if (!append) {
        resultsTable.hidden = true;
      }
      loadMoreBtn.hidden = true;
      return;
    }

    const html = data.results.map(renderRow).join("");
    if (append) {
      resultsBody.insertAdjacentHTML("beforeend", html);
    } else {
      resultsBody.innerHTML = html;
    }

    resultsTable.hidden = false;
    nextOffset = data.next_offset || 0;
    loadMoreBtn.hidden = data.complete || !nextOffset;
  }

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

  let debounce;
  searchInput.addEventListener("input", function () {
    clearTimeout(debounce);
    debounce = setTimeout(function () {
      doSearch(searchInput.value.trim(), false);
    }, 350);
  });

  loadStats();
  setInterval(loadStats, 30000);
  doSearch("", false);
})();
