"""Process-tree memory limits for scan/dedup (Linux /proc)."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MemoryUsage:
    rss_bytes: int
    swap_bytes: int
    mem_available_bytes: int


@dataclass(frozen=True)
class MemoryLimits:
    """Limits for debuginfod process tree during heavy work."""

    max_rss_bytes: int = 0
    max_swap_bytes: int = 0
    min_mem_available_bytes: int = 0
    poll_interval_sec: float = 0.5

    @property
    def enabled(self) -> bool:
        return (
            self.max_rss_bytes > 0
            or self.max_swap_bytes > 0
            or self.min_mem_available_bytes > 0
        )


def mb_to_bytes(mb: int) -> int:
    return max(0, mb) * 1024 * 1024


def _read_int_kb(path: str, key: str) -> int:
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith(key):
                    return int(line.split()[1])
    except OSError:
        return 0
    return 0


def read_mem_available_bytes() -> int:
    """MemAvailable from /proc/meminfo (kernel estimate of allocatable RAM)."""
    kb = _read_int_kb("/proc/meminfo", "MemAvailable:")
    if kb <= 0:
        kb = _read_int_kb("/proc/meminfo", "MemFree:")
    return kb * 1024


def _process_status_bytes(pid: int) -> tuple[int, int]:
    rss_kb = swap_kb = 0
    try:
        with open(f"/proc/{pid}/status", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    rss_kb = int(line.split()[1])
                elif line.startswith("VmSwap:"):
                    swap_kb = int(line.split()[1])
    except OSError:
        return 0, 0
    return rss_kb * 1024, swap_kb * 1024


def _child_pids(pid: int) -> list[int]:
    children: list[int] = []
    proc = "/proc"
    try:
        entries = os.listdir(proc)
    except OSError:
        return children
    for entry in entries:
        if not entry.isdigit():
            continue
        child_pid = int(entry)
        try:
            with open(f"{proc}/{entry}/stat", encoding="utf-8") as fh:
                stat = fh.read()
            after_name = stat.rsplit(")", 1)[-1].split()
            ppid = int(after_name[1])
        except (OSError, ValueError, IndexError):
            continue
        if ppid == pid:
            children.append(child_pid)
    return children


def process_tree_usage(root_pid: int | None = None) -> MemoryUsage:
    """Sum RSS/swap for root process and all descendants."""
    root = root_pid if root_pid is not None else os.getpid()
    rss = swap = 0
    stack = [root]
    seen: set[int] = set()
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        prss, pswap = _process_status_bytes(pid)
        rss += prss
        swap += pswap
        stack.extend(_child_pids(pid))
    return MemoryUsage(
        rss_bytes=rss,
        swap_bytes=swap,
        mem_available_bytes=read_mem_available_bytes(),
    )


def _over_limit(usage: MemoryUsage, limits: MemoryLimits) -> str | None:
    if limits.max_rss_bytes > 0 and usage.rss_bytes >= limits.max_rss_bytes:
        return "rss"
    if limits.max_swap_bytes > 0 and usage.swap_bytes >= limits.max_swap_bytes:
        return "swap"
    if (
        limits.min_mem_available_bytes > 0
        and usage.mem_available_bytes < limits.min_mem_available_bytes
    ):
        return "mem_available"
    return None


class MemoryGovernor:
    """Pause work until process-tree memory is below configured limits."""

    def __init__(
        self,
        limits: MemoryLimits,
        *,
        root_pid: int | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self.limits = limits
        self.root_pid = root_pid
        self._sleep = sleeper or time.sleep
        self._last_warn = 0.0

    def wait_for_headroom(self, stop_event: object | None = None) -> bool:
        """Block until under limits. Returns False if stop_event is set."""
        if not self.limits.enabled:
            return True

        while True:
            if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
                return False

            usage = process_tree_usage(self.root_pid)
            reason = _over_limit(usage, self.limits)
            if reason is None:
                return True

            now = time.monotonic()
            if now - self._last_warn >= 5.0:
                self._last_warn = now
                logger.warning(
                    "Memory pressure (%s): rss=%.1f MiB swap=%.1f MiB "
                    "mem_available=%.1f MiB — throttling scan/dedup",
                    reason,
                    usage.rss_bytes / (1024 * 1024),
                    usage.swap_bytes / (1024 * 1024),
                    usage.mem_available_bytes / (1024 * 1024),
                )
            self._sleep(self.limits.poll_interval_sec)

    def snapshot(self) -> MemoryUsage:
        return process_tree_usage(self.root_pid)
