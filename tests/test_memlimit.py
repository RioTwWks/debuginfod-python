"""Memory limit helper tests."""

from __future__ import annotations

from debuginfod.memlimit import MemoryGovernor, MemoryLimits, MemoryUsage, mb_to_bytes


def test_mb_to_bytes() -> None:
    assert mb_to_bytes(1) == 1024 * 1024


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
