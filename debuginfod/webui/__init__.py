"""Web UI dashboard (analog of debuginfod-go /ui/)."""

from __future__ import annotations

from debuginfod.webui.routes import register_webui

__all__ = ["register_webui"]
