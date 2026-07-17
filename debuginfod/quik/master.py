"""Master batch selection for Quik deduplication groups."""

from __future__ import annotations

from debuginfod.quik.grouping import BatchGroup, BuildBatch


def select_master_batch(group: BatchGroup) -> BuildBatch:
    """Pick master directory: minimum build number in the group (filediffs step 4)."""
    if not group.batches:
        raise ValueError("empty batch group")
    return min(group.batches, key=lambda b: b.build_number)
