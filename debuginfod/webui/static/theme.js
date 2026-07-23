(function (global) {
  "use strict";

  var STORAGE_KEY = "debuginfod-theme";

  function preferredTheme() {
    return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  }

  function currentTheme() {
    return document.documentElement.getAttribute("data-theme") || preferredTheme();
  }

  function updateToggleUi(theme) {
    document.querySelectorAll(".theme-toggle").forEach(function (btn) {
      var isDark = theme === "dark";
      btn.setAttribute("aria-pressed", isDark ? "true" : "false");
      btn.title = isDark ? "Светлая тема" : "Тёмная тема";
      var darkIcon = btn.querySelector(".theme-icon-dark");
      var lightIcon = btn.querySelector(".theme-icon-light");
      if (darkIcon) darkIcon.hidden = !isDark;
      if (lightIcon) lightIcon.hidden = isDark;
    });
  }

  function applyTheme(theme) {
    var next = theme === "light" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch (_) {
      /* ignore */
    }
    updateToggleUi(next);
  }

  function toggleTheme() {
    applyTheme(currentTheme() === "dark" ? "light" : "dark");
  }

  function initTheme() {
    var saved = null;
    try {
      saved = localStorage.getItem(STORAGE_KEY);
    } catch (_) {
      saved = null;
    }
    applyTheme(saved || preferredTheme());
    document.querySelectorAll(".theme-toggle").forEach(function (btn) {
      if (!btn.dataset.themeBound) {
        btn.dataset.themeBound = "1";
        btn.addEventListener("click", toggleTheme);
      }
    });
  }

  var bootTheme = null;
  try {
    bootTheme = localStorage.getItem(STORAGE_KEY);
  } catch (_) {
    bootTheme = null;
  }
  document.documentElement.setAttribute("data-theme", bootTheme || preferredTheme());

  global.debuginfodTheme = {
    init: initTheme,
    apply: applyTheme,
    toggle: toggleTheme,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initTheme);
  } else {
    initTheme();
  }
})(window);
