"""Process-tree and system memory limits for scan/dedup (Linux /proc)."""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Callable

logger = logging.getLogger(__name__)

DEFAULT_DEDUP_PEAK_FACTOR = 3.0


@dataclass(frozen=True)
class MemoryUsage:
    rss_bytes: int
    swap_bytes: int
    mem_available_bytes: int
    system_swap_bytes: int = 0


@dataclass(frozen=True)
class MemoryLimits:
    """Limits for debuginfod process tree during heavy work."""

    max_rss_bytes: int = 0
    max_swap_bytes: int = 0
    min_mem_available_bytes: int = 0
    poll_interval_sec: float = 0.5
    dedup_peak_factor: float = DEFAULT_DEDUP_PEAK_FACTOR

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


def read_system_swap_used_bytes() -> int:
    """System-wide swap used (SwapTotal - SwapFree) from /proc/meminfo."""
    total_kb = _read_int_kb("/proc/meminfo", "SwapTotal:")
    free_kb = _read_int_kb("/proc/meminfo", "SwapFree:")
    if total_kb <= 0:
        return 0
    return max(0, total_kb - free_kb) * 1024


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
        system_swap_bytes=read_system_swap_used_bytes(),
    )


def estimate_dedup_peak_bytes(file_bytes: int, peak_factor: float) -> int:
    """Rough peak RAM for one dedup job (copy + decompress + verify)."""
    if file_bytes <= 0:
        return 0
    return max(file_bytes, int(file_bytes * max(1.0, peak_factor)))


def _has_job_headroom(
    governor: MemoryGovernor,
    usage: MemoryUsage,
    peak_bytes: int,
    reserved_bytes: int,
) -> bool:
    if governor._over_limit(usage) is not None:
        return False
    limits = governor.limits
    if peak_bytes <= 0:
        return True
    need = peak_bytes + reserved_bytes
    if limits.min_mem_available_bytes > 0:
        return usage.mem_available_bytes >= limits.min_mem_available_bytes + need
    return usage.mem_available_bytes >= need


class JobBudget:
    """Reserved memory budget for one dedup/scan heavy step."""

    def __init__(self, governor: MemoryGovernor, peak_bytes: int) -> None:
        self._governor = governor
        self.peak_bytes = peak_bytes

    def __enter__(self) -> JobBudget:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self._governor._release_budget(self.peak_bytes)


class MemoryGovernor:
    """Pause work until process-tree and system memory are within limits."""

    def __init__(
        self,
        limits: MemoryLimits,
        *,
        root_pid: int | None = None,
        sleeper: Callable[[float], None] | None = None,
        baseline_system_swap_bytes: int | None = None,
    ) -> None:
        self.limits = limits
        self.root_pid = root_pid
        self._sleep = sleeper or time.sleep
        self._last_warn = 0.0
        self._lock = threading.Lock()
        self._reserved_bytes = 0
        self._baseline_system_swap = (
            read_system_swap_used_bytes()
            if baseline_system_swap_bytes is None
            else max(0, baseline_system_swap_bytes)
        )

    @property
    def baseline_system_swap_bytes(self) -> int:
        return self._baseline_system_swap

    def system_swap_delta_bytes(self, usage: MemoryUsage) -> int:
        """Swap growth since governor start (ignores pre-existing system swap)."""
        return max(0, usage.system_swap_bytes - self._baseline_system_swap)

    def _over_limit(self, usage: MemoryUsage) -> str | None:
        limits = self.limits
        if limits.max_rss_bytes > 0 and usage.rss_bytes >= limits.max_rss_bytes:
            return "rss"
        if limits.max_swap_bytes > 0:
            if usage.swap_bytes >= limits.max_swap_bytes:
                return "tree_swap"
            delta = self.system_swap_delta_bytes(usage)
            if delta >= limits.max_swap_bytes:
                return "swap_delta"
        if (
            limits.min_mem_available_bytes > 0
            and usage.mem_available_bytes < limits.min_mem_available_bytes
        ):
            return "mem_available"
        return None

    def wait_for_headroom(self, stop_event: object | None = None) -> bool:
        """Block until under limits. Returns False if stop_event is set."""
        if not self.limits.enabled:
            return True

        while True:
            if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
                return False

            usage = process_tree_usage(self.root_pid)
            reason = self._over_limit(usage)
            if reason is None:
                return True

            self._log_pressure(reason, usage)
            self._sleep(self.limits.poll_interval_sec)

    def wait_for_job(
        self,
        file_bytes: int,
        stop_event: object | None = None,
        *,
        peak_factor: float | None = None,
    ) -> bool:
        """Block until limits allow starting a job with the given file size."""
        peak = estimate_dedup_peak_bytes(
            file_bytes,
            peak_factor if peak_factor is not None else self.limits.dedup_peak_factor,
        )
        if not self.limits.enabled and peak <= 0:
            return True

        while True:
            if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
                return False

            with self._lock:
                usage = process_tree_usage(self.root_pid)
                reason = self._over_limit(usage)
                if reason is None and _has_job_headroom(
                    self,
                    usage,
                    peak,
                    self._reserved_bytes,
                ):
                    return True

            self._log_pressure(reason or "job_headroom", usage, peak_bytes=peak)
            self._sleep(self.limits.poll_interval_sec)

    def acquire_job_budget(
        self,
        file_bytes: int,
        stop_event: object | None = None,
        *,
        peak_factor: float | None = None,
    ) -> JobBudget | None:
        """Reserve RAM budget for a heavy job; release via context manager."""
        peak = estimate_dedup_peak_bytes(
            file_bytes,
            peak_factor if peak_factor is not None else self.limits.dedup_peak_factor,
        )
        if not self.limits.enabled:
            return JobBudget(self, 0)

        while True:
            if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
                return None

            with self._lock:
                usage = process_tree_usage(self.root_pid)
                reason = self._over_limit(usage)
                if reason is None and _has_job_headroom(
                    self,
                    usage,
                    peak,
                    self._reserved_bytes,
                ):
                    self._reserved_bytes += peak
                    return JobBudget(self, peak)

            self._log_pressure(reason or "job_budget", usage, peak_bytes=peak)
            self._sleep(self.limits.poll_interval_sec)

    def effective_workers(self, max_workers: int, largest_file_bytes: int) -> int:
        """Reduce parallelism when large files would exceed available RAM."""
        workers = max(1, max_workers)
        if not self.limits.enabled or largest_file_bytes <= 0:
            return workers

        usage = process_tree_usage(self.root_pid)
        headroom = usage.mem_available_bytes
        if self.limits.min_mem_available_bytes > 0:
            headroom -= self.limits.min_mem_available_bytes
        headroom -= self._reserved_bytes
        if headroom <= 0:
            return 1

        per_job = estimate_dedup_peak_bytes(largest_file_bytes, self.limits.dedup_peak_factor)
        if per_job <= 0:
            return workers
        return max(1, min(workers, headroom // per_job))

    def _release_budget(self, peak_bytes: int) -> None:
        with self._lock:
            self._reserved_bytes = max(0, self._reserved_bytes - peak_bytes)

    def snapshot(self) -> MemoryUsage:
        return process_tree_usage(self.root_pid)

    def _log_pressure(
        self,
        reason: str,
        usage: MemoryUsage,
        *,
        peak_bytes: int = 0,
    ) -> None:
        now = time.monotonic()
        if now - self._last_warn >= 5.0:
            self._last_warn = now
            extra = f" need~={peak_bytes / (1024 * 1024):.1f} MiB" if peak_bytes else ""
            swap_delta = self.system_swap_delta_bytes(usage)
            logger.warning(
                "Memory pressure (%s%s): rss=%.1f MiB tree_swap=%.1f MiB "
                "sys_swap_delta=%.1f MiB (baseline=%.1f MiB) mem_available=%.1f MiB "
                "reserved=%.1f MiB — throttling",
                reason,
                extra,
                usage.rss_bytes / (1024 * 1024),
                usage.swap_bytes / (1024 * 1024),
                swap_delta / (1024 * 1024),
                self._baseline_system_swap / (1024 * 1024),
                usage.mem_available_bytes / (1024 * 1024),
                self._reserved_bytes / (1024 * 1024),
            )
