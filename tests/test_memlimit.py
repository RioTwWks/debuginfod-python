"""Memory limit helper tests."""

from __future__ import annotations

from debuginfod.memlimit import (
    JobBudget,
    MemoryGovernor,
    MemoryLimits,
    MemoryUsage,
    _job_pressure_reason,
    estimate_dedup_peak_bytes,
    mb_to_bytes,
)


def test_mb_to_bytes() -> None:
    assert mb_to_bytes(1) == 1024 * 1024


def test_estimate_decompress_peak_bytes() -> None:
    from debuginfod.memlimit import MemoryLimits, estimate_decompress_peak_bytes

    limits = MemoryLimits(dedup_peak_factor_decompress=20.0)
    assert estimate_decompress_peak_bytes(100 * 1024 * 1024, limits) == 2000 * 1024 * 1024


def test_soft_rss_triggers_early() -> None:
    limits = MemoryLimits(max_rss_bytes=1000)
    governor = MemoryGovernor(limits, sleeper=lambda _: None)
    usage = MemoryUsage(rss_bytes=750, swap_bytes=0, mem_available_bytes=10**9)
    assert governor._over_limit(usage) == "rss_soft"


def test_over_limit_rss() -> None:
    limits = MemoryLimits(max_rss_bytes=100)
    governor = MemoryGovernor(limits, sleeper=lambda _: None)
    usage = MemoryUsage(rss_bytes=200, swap_bytes=0, mem_available_bytes=10**9)

    class _Stop:
        def is_set(self) -> bool:
            return False

    calls = {"n": 0}

    def fake_usage(_root: int | None = None) -> MemoryUsage:
        calls["n"] += 1
        if calls["n"] >= 2:
            return MemoryUsage(rss_bytes=50, swap_bytes=0, mem_available_bytes=10**9)
        return usage

    import debuginfod.memlimit as memlimit

    original = memlimit.process_tree_usage
    memlimit.process_tree_usage = fake_usage  # type: ignore[assignment]
    try:
        assert governor.wait_for_headroom(_Stop()) is True
    finally:
        memlimit.process_tree_usage = original


def test_over_limit_system_swap() -> None:
    limits = MemoryLimits(max_swap_bytes=100)
    governor = MemoryGovernor(
        limits,
        sleeper=lambda _: None,
        baseline_system_swap_bytes=0,
    )

    class _Stop:
        def is_set(self) -> bool:
            return False

    calls = {"n": 0}

    def fake_usage(_root: int | None = None) -> MemoryUsage:
        calls["n"] += 1
        if calls["n"] >= 2:
            return MemoryUsage(
                rss_bytes=0,
                swap_bytes=0,
                mem_available_bytes=10**9,
                system_swap_bytes=50,
            )
        return MemoryUsage(
            rss_bytes=0,
            swap_bytes=0,
            mem_available_bytes=10**9,
            system_swap_bytes=200,
        )

    import debuginfod.memlimit as memlimit

    original = memlimit.process_tree_usage
    memlimit.process_tree_usage = fake_usage  # type: ignore[assignment]
    try:
        assert governor.wait_for_headroom(_Stop()) is True
    finally:
        memlimit.process_tree_usage = original


def test_preexisting_system_swap_does_not_block() -> None:
    limits = MemoryLimits(max_swap_bytes=100)
    baseline = 4000 * 1024 * 1024
    governor = MemoryGovernor(
        limits,
        sleeper=lambda _: None,
        baseline_system_swap_bytes=baseline,
    )
    usage = MemoryUsage(
        rss_bytes=0,
        swap_bytes=0,
        mem_available_bytes=10**9,
        system_swap_bytes=baseline,
    )
    assert governor._over_limit(usage) is None
    assert governor.system_swap_delta_bytes(usage) == 0


def test_job_headroom_ignores_sticky_rss_soft() -> None:
    limits = MemoryLimits(max_rss_bytes=1000, min_mem_available_bytes=100)
    governor = MemoryGovernor(limits, sleeper=lambda _: None)
    usage = MemoryUsage(rss_bytes=750, swap_bytes=0, mem_available_bytes=10**9)
    assert governor._over_limit(usage) == "rss_soft"
    assert _job_pressure_reason(governor, usage, 50 * 1024 * 1024, 0) is None


def test_job_headroom_blocks_when_mem_unavailable() -> None:
    limits = MemoryLimits(max_rss_bytes=10**12, min_mem_available_bytes=500)
    governor = MemoryGovernor(limits, sleeper=lambda _: None)
    usage = MemoryUsage(rss_bytes=0, swap_bytes=0, mem_available_bytes=400)
    assert _job_pressure_reason(governor, usage, 100, 0) == "mem_available"


def test_wait_for_job_requires_headroom() -> None:
    limits = MemoryLimits(min_mem_available_bytes=500)
    governor = MemoryGovernor(limits, sleeper=lambda _: None)

    class _Stop:
        def is_set(self) -> bool:
            return False

    calls = {"n": 0}

    def fake_usage(_root: int | None = None) -> MemoryUsage:
        calls["n"] += 1
        if calls["n"] >= 2:
            return MemoryUsage(rss_bytes=0, swap_bytes=0, mem_available_bytes=2000)
        return MemoryUsage(rss_bytes=0, swap_bytes=0, mem_available_bytes=400)

    import debuginfod.memlimit as memlimit

    original = memlimit.process_tree_usage
    memlimit.process_tree_usage = fake_usage  # type: ignore[assignment]
    try:
        assert governor.wait_for_job(100, _Stop()) is True
    finally:
        memlimit.process_tree_usage = original


def test_acquire_job_budget_reserves_and_releases() -> None:
    limits = MemoryLimits(max_rss_bytes=10**12, dedup_peak_factor=2.0)
    governor = MemoryGovernor(limits, sleeper=lambda _: None)

    usage = MemoryUsage(rss_bytes=0, swap_bytes=0, mem_available_bytes=10**9)

    import debuginfod.memlimit as memlimit

    original = memlimit.process_tree_usage
    memlimit.process_tree_usage = lambda _root=None: usage  # type: ignore[assignment]
    try:
        budget = governor.acquire_job_budget(100)
        assert isinstance(budget, JobBudget)
        assert governor._reserved_bytes == 200
        with budget:
            pass
        assert governor._reserved_bytes == 0
    finally:
        memlimit.process_tree_usage = original


def test_effective_workers_reduces_for_large_files() -> None:
    limits = MemoryLimits(min_mem_available_bytes=1024 * 1024, dedup_peak_factor=3.0)
    governor = MemoryGovernor(limits, sleeper=lambda _: None)

    import debuginfod.memlimit as memlimit

    original = memlimit.process_tree_usage
    memlimit.process_tree_usage = lambda _root=None: MemoryUsage(  # type: ignore[assignment]
        rss_bytes=0,
        swap_bytes=0,
        mem_available_bytes=7 * 1024 * 1024,
    )
    try:
        # 6 MiB headroom after min_available, 3 MiB per 1 MiB file -> 2 workers max
        assert governor.effective_workers(8, 1024 * 1024, peak_factor=3.0) == 2
        assert governor.effective_workers(8, 10 * 1024 * 1024, peak_factor=3.0) == 1
    finally:
        memlimit.process_tree_usage = original


def test_scan_headroom_ignores_rss_soft() -> None:
    limits = MemoryLimits(max_rss_bytes=1000, min_mem_available_bytes=100)
    governor = MemoryGovernor(limits, sleeper=lambda _: None)
    usage = MemoryUsage(rss_bytes=750, swap_bytes=0, mem_available_bytes=10**9)
    assert governor._over_limit(usage) == "rss_soft"
    assert governor._over_limit(usage, for_scan=True) is None

    class _Stop:
        def is_set(self) -> bool:
            return False

    import debuginfod.memlimit as memlimit

    original = memlimit.process_tree_usage
    memlimit.process_tree_usage = lambda _root=None: usage  # type: ignore[assignment]
    try:
        assert governor.wait_for_headroom(_Stop(), for_scan=True) is True
    finally:
        memlimit.process_tree_usage = original


def test_effective_scan_workers_caps_by_soft_rss() -> None:
    limits = MemoryLimits(
        max_rss_bytes=mb_to_bytes(4771),
        min_mem_available_bytes=mb_to_bytes(1536),
    )
    governor = MemoryGovernor(limits, sleeper=lambda _: None)

    import debuginfod.memlimit as memlimit

    original = memlimit.process_tree_usage
    memlimit.process_tree_usage = lambda _root=None: MemoryUsage(  # type: ignore[assignment]
        rss_bytes=0,
        swap_bytes=0,
        mem_available_bytes=mb_to_bytes(8000),
    )
    try:
        # soft RSS ~3340 MiB / 512 MiB per worker -> 6 workers max
        assert governor.effective_scan_workers(8) == 6
        assert governor.effective_scan_workers(4) == 4
    finally:
        memlimit.process_tree_usage = original


def test_wait_respects_stop() -> None:
    limits = MemoryLimits(max_rss_bytes=1)
    governor = MemoryGovernor(limits, sleeper=lambda _: None)

    class _Stop:
        def __init__(self) -> None:
            self._set = False

        def is_set(self) -> bool:
            return self._set

    stop = _Stop()
    import debuginfod.memlimit as memlimit

    original = memlimit.process_tree_usage
    memlimit.process_tree_usage = lambda _root=None: MemoryUsage(  # type: ignore[assignment]
        rss_bytes=999, swap_bytes=0, mem_available_bytes=0
    )
    try:
        stop._set = True
        assert governor.wait_for_headroom(stop) is False
    finally:
        memlimit.process_tree_usage = original
