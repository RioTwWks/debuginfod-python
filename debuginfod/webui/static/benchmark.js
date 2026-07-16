(function () {
  const form = document.getElementById("bench-form");
  const runBtn = document.getElementById("run-btn");
  const statusEl = document.getElementById("bench-status");
  const hintEl = document.getElementById("bench-hint");
  const warningsEl = document.getElementById("bench-warnings");
  const summaryGrid = document.getElementById("summary-grid");
  const latencyPanel = document.getElementById("latency-panel");
  const storagePanel = document.getElementById("storage-panel");
  const detailsPanel = document.getElementById("details-panel");
  const historyPanel = document.getElementById("history-panel");
  const detailsBody = document.getElementById("details-body");
  const historyBody = document.getElementById("history-body");
  const latencyCanvas = document.getElementById("latency-chart");
  const storageCanvas = document.getElementById("storage-chart");

  const COLORS = {
    go: "#22c55e",
    py: "#3b82f6",
    muted: "#8b9cb3",
    grid: "#2d3a4f",
    text: "#e8edf4",
  };

  function formatNumber(n) {
    return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 }).format(n);
  }

  function formatBytes(bytes) {
    if (!bytes) return "0 B";
    const units = ["B", "KB", "MB", "GB"];
    const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
    return (bytes / Math.pow(1024, i)).toFixed(1) + " " + units[i];
  }

  function parseErrorMessage(text) {
    if (!text) return "неизвестная ошибка";
    try {
      const data = JSON.parse(text);
      if (data.detail) {
        return typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
      }
    } catch (_e) {
      /* plain text */
    }
    return text;
  }

  function formatError(err) {
    if (err && err.message) return parseErrorMessage(err.message);
    return String(err);
  }

  function escapeHtml(s) {
    const div = document.createElement("div");
    div.textContent = s;
    return div.innerHTML;
  }

  function setupCanvas(canvas) {
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    const width = rect.width || canvas.width;
    const height = rect.height || canvas.height;
    canvas.width = Math.floor(width * dpr);
    canvas.height = Math.floor(height * dpr);
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx, width, height };
  }

  function drawGroupedBars(canvas, labels, series) {
    const { ctx, width, height } = setupCanvas(canvas);
    ctx.clearRect(0, 0, width, height);

    const padding = { top: 24, right: 20, bottom: 48, left: 56 };
    const chartW = width - padding.left - padding.right;
    const chartH = height - padding.top - padding.bottom;

    const allValues = series.flatMap((s) => s.values.filter((v) => v != null));
    const maxVal = Math.max(...allValues, 1) * 1.15;
    const groupCount = labels.length;
    const barCount = series.length;
    const groupWidth = chartW / Math.max(groupCount, 1);
    const barWidth = Math.min(28, (groupWidth - 16) / barCount);

    ctx.strokeStyle = COLORS.grid;
    ctx.fillStyle = COLORS.muted;
    ctx.font = "12px system-ui, sans-serif";
    for (let i = 0; i <= 4; i++) {
      const y = padding.top + (chartH * i) / 4;
      const val = maxVal * (1 - i / 4);
      ctx.beginPath();
      ctx.moveTo(padding.left, y);
      ctx.lineTo(width - padding.right, y);
      ctx.stroke();
      ctx.fillText(formatNumber(val), 8, y + 4);
    }

    labels.forEach(function (label, gi) {
      const groupX = padding.left + gi * groupWidth + groupWidth / 2;
      series.forEach(function (s, si) {
        const value = s.values[gi];
        if (value == null) return;
        const barH = (value / maxVal) * chartH;
        const x = groupX - (barCount * barWidth) / 2 + si * barWidth;
        const y = padding.top + chartH - barH;
        ctx.fillStyle = s.color;
        ctx.fillRect(x, y, barWidth - 4, barH);
      });
      ctx.fillStyle = COLORS.text;
      ctx.textAlign = "center";
      ctx.fillText(label, groupX, height - 16);
    });
  }

  function drawStorageBars(canvas, labels, values, colors) {
    const { ctx, width, height } = setupCanvas(canvas);
    ctx.clearRect(0, 0, width, height);

    const padding = { top: 24, right: 20, bottom: 56, left: 56 };
    const chartW = width - padding.left - padding.right;
    const chartH = height - padding.top - padding.bottom;
    const maxVal = Math.max(...values, 1) * 1.1;
    const barWidth = Math.min(120, chartW / labels.length - 24);

    values.forEach(function (value, i) {
      const x = padding.left + i * (chartW / labels.length) + (chartW / labels.length - barWidth) / 2;
      const barH = (value / maxVal) * chartH;
      const y = padding.top + chartH - barH;
      ctx.fillStyle = colors[i];
      ctx.fillRect(x, y, barWidth, barH);
      ctx.fillStyle = COLORS.text;
      ctx.textAlign = "center";
      ctx.fillText(labels[i], x + barWidth / 2, height - 28);
      ctx.fillStyle = COLORS.muted;
      ctx.fillText(formatBytes(value), x + barWidth / 2, y - 8);
    });
  }

  function renderWarnings(report) {
    const warnings = report.warnings || [];
    const goRescan = (report.rescan_results || {}).go;
    if (goRescan && goRescan.status === "error" && goRescan.code === 401) {
      if (!warnings.some(function (w) { return w.indexOf("401") >= 0; })) {
        warnings.push(
          "Go rescan: 401 — укажите Go admin key (тот же, что DEBUGINFOD_ADMIN_KEY у debuginfod-go)"
        );
      }
    }
    if (warnings.length === 0) {
      warningsEl.hidden = true;
      warningsEl.innerHTML = "";
      return;
    }
    warningsEl.innerHTML =
      "<strong>Предупреждения</strong><ul>" +
      warnings.map(function (w) { return "<li>" + escapeHtml(w) + "</li>"; }).join("") +
      "</ul>";
    warningsEl.hidden = false;
  }

  function renderSummary(summary) {
    const cards = [
      { label: "Бинарников", value: summary.binary_count, highlight: true },
      { label: "Go latency (avg)", value: summary.go_mean_latency_ms ? summary.go_mean_latency_ms.toFixed(1) + " ms" : "—" },
      { label: "Python latency (avg)", value: summary.py_mean_latency_ms ? summary.py_mean_latency_ms.toFixed(1) + " ms" : "—" },
      { label: "Latency py/go", value: summary.latency_ratio_py_vs_go ? summary.latency_ratio_py_vs_go.toFixed(2) + "×" : "—" },
      { label: "Go на диске", value: formatBytes(summary.go_disk_bytes) },
      { label: "Python blobs", value: formatBytes(summary.py_stored_bytes) },
      { label: "Сэкономлено", value: formatBytes(summary.py_bytes_saved) },
      { label: "Сжатие Python", value: ((summary.py_compression_ratio || 1) * 100).toFixed(1) + "%" },
    ];

    summaryGrid.innerHTML = cards
      .map(function (c) {
        const cls = c.highlight ? "stat-card highlight" : "stat-card";
        const val = typeof c.value === "number" ? formatNumber(c.value) : c.value;
        return (
          '<div class="' + cls + '"><span class="stat-value">' + escapeHtml(String(val)) +
          '</span><span class="stat-label">' + escapeHtml(c.label) + "</span></div>"
        );
      })
      .join("");
    summaryGrid.hidden = false;
  }

  function renderCharts(report) {
    const labels = report.binaries.map((b) => b.label);
    const goVals = report.binaries.map((b) => (b.go_latency_ms ? b.go_latency_ms.mean : null));
    const pyVals = report.binaries.map((b) => (b.py_latency_ms ? b.py_latency_ms.mean : null));

    drawGroupedBars(latencyCanvas, labels, [
      { color: COLORS.go, values: goVals },
      { color: COLORS.py, values: pyVals },
    ]);
    latencyPanel.hidden = false;

    const s = report.summary;
    drawStorageBars(
      storageCanvas,
      ["Go (testdata)", "Python blobs", "Python original"],
      [s.go_disk_bytes || 0, s.py_stored_bytes || 0, s.py_original_bytes || 0],
      [COLORS.go, COLORS.py, COLORS.muted]
    );
    storagePanel.hidden = false;
  }

  function renderDetails(report) {
    detailsBody.innerHTML = report.binaries
      .map(function (b) {
        const goMs = b.go_latency_ms
          ? b.go_latency_ms.mean.toFixed(1)
          : (b.go_error ? '<span class="cell-error" title="' + escapeHtml(b.go_error) + '">err</span>' : "—");
        const pyMs = b.py_latency_ms
          ? b.py_latency_ms.mean.toFixed(1)
          : (b.py_error ? '<span class="cell-error" title="' + escapeHtml(b.py_error) + '">err</span>' : "—");
        let ratio = "—";
        if (b.go_latency_ms && b.py_latency_ms && b.go_latency_ms.mean > 0) {
          ratio = (b.py_latency_ms.mean / b.go_latency_ms.mean).toFixed(2) + "×";
        }
        return (
          "<tr><td>" + escapeHtml(b.label) + "</td>" +
          '<td class="mono">' + escapeHtml(b.build_id.slice(0, 16) + "…") + "</td>" +
          "<td>" + formatBytes(b.file_size_bytes) + "</td>" +
          "<td>" + goMs + "</td><td>" + pyMs + "</td><td>" + ratio + "</td></tr>"
        );
      })
      .join("");
    detailsPanel.hidden = false;
  }

  function renderHistory(history) {
    if (!history || history.length === 0) {
      historyPanel.hidden = true;
      return;
    }
    historyBody.innerHTML = history
      .map(function (item) {
        const s = item.summary || {};
        return (
          "<tr><td class='mono'>" + escapeHtml(item.finished_at || "") + "</td>" +
          "<td>" + (s.binary_count || 0) + "</td>" +
          "<td>" + (s.go_mean_latency_ms ? s.go_mean_latency_ms.toFixed(1) : "—") + "</td>" +
          "<td>" + (s.py_mean_latency_ms ? s.py_mean_latency_ms.toFixed(1) : "—") + "</td>" +
          "<td>" + ((s.py_compression_ratio || 1) * 100).toFixed(1) + "%</td></tr>"
        );
      })
      .join("");
    historyPanel.hidden = false;
  }

  function renderReport(report) {
    renderWarnings(report);
    renderSummary(report.summary);
    renderCharts(report);
    renderDetails(report);
    statusEl.textContent = "Готово: " + (report.finished_at || "");
    statusEl.classList.remove("error");
  }

  async function loadConfig() {
    try {
      const res = await fetch("/ui/api/benchmark/config");
      if (!res.ok) return;
      const data = await res.json();
      document.getElementById("go-url").value = data.go_url;
      document.getElementById("py-url").value = data.py_url;
      document.getElementById("testdata").value = data.testdata;
      if (data.binary_count === 0) {
        hintEl.textContent = "В testdata нет demo_v* — запустите scripts/generate_test_artifacts.py";
      } else {
        let hint = "Найдено бинарников: " + data.binary_count + " (" + data.binaries.join(", ") + ")";
        if (data.scan_paths && data.scan_paths.length) {
          hint += ". Scan paths сервера: " + data.scan_paths.join(", ");
        }
        if (!data.go_admin_key_configured) {
          hint += ". Go admin key не задан в .env — rescan Go может вернуть 401.";
        }
        hintEl.textContent = hint;
      }
    } catch (_err) {
      /* ignore */
    }
  }

  async function loadLast() {
    const res = await fetch("/ui/api/benchmark/last");
    if (!res.ok) return;
    const data = await res.json();
    if (data.report) renderReport(data.report);
  }

  async function loadHistory() {
    const res = await fetch("/ui/api/benchmark/history");
    if (!res.ok) return;
    const data = await res.json();
    renderHistory(data.history || []);
  }

  form.addEventListener("submit", async function (e) {
    e.preventDefault();
    runBtn.disabled = true;
    runBtn.textContent = "Выполняется…";
    statusEl.textContent = "Бенчмарк запущен, это может занять минуту…";
    statusEl.classList.remove("error");

    const body = {
      go_url: document.getElementById("go-url").value.trim(),
      py_url: document.getElementById("py-url").value.trim(),
      testdata: document.getElementById("testdata").value.trim(),
      runs: Number(document.getElementById("runs").value),
      rescan: document.getElementById("rescan").checked,
      go_admin_key: document.getElementById("go-admin-key").value,
      py_admin_key: document.getElementById("py-admin-key").value,
    };

    try {
      const res = await fetch("/ui/api/benchmark/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || "HTTP " + res.status);
      }
      const data = await res.json();
      renderReport(data.report);
      await loadHistory();
    } catch (err) {
      statusEl.textContent = "Ошибка: " + formatError(err);
      statusEl.classList.add("error");
    } finally {
      runBtn.disabled = false;
      runBtn.textContent = "Запустить бенчмарк";
    }
  });

  window.addEventListener("resize", function () {
    fetch("/ui/api/benchmark/last")
      .then((r) => r.json())
      .then((d) => {
        if (d.report) renderCharts(d.report);
      })
      .catch(function () {});
  });

  loadConfig();
  loadLast();
  loadHistory();
})();
