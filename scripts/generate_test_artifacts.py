#!/usr/bin/env python3
"""Generate synthetic ELF-like test artifacts for benchmark comparison."""

from __future__ import annotations

import argparse
import hashlib
import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path


def _write_minimal_c(path: Path, symbol: str, body: str) -> None:
    path.write_text(
        f'#include <stdio.h>\n'
        f'int {symbol}(void) {{\n{body}\n    return 0;\n}}\n'
        f'int main(void) {{ printf("%s\\n", "{symbol}"); return {symbol}(); }}\n'
    )


def _build_version(out_dir: Path, version: int, base_body: str) -> Path:
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"v{version}.c"
        extra = f"    volatile int v = {version};\n    (void)v;\n"
        _write_minimal_c(src, f"demo_v{version}", extra + base_body)
        binary = out_dir / f"demo_v{version}"
        subprocess.run(
            [
                "gcc",
                "-g",
                "-O0",
                "-Wl,--build-id=sha1",
                "-o",
                str(binary),
                str(src),
            ],
            check=True,
        )
        return binary


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate test ELF binaries for debuginfod benchmarks")
    parser.add_argument("-o", "--output", type=Path, default=Path("testdata/versions"))
    parser.add_argument("-n", "--count", type=int, default=5)
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    base_body = "    volatile int x = 1; (void)x;"

    for i in range(1, args.count + 1):
        # Each version adds a small change to simulate incremental builds
        body = base_body + f"\n    volatile int patch_{i} = {i * 17}; (void)patch_{i};\n"
        binary = _build_version(args.output, i, body)
        print(f"Built {binary}")


if __name__ == "__main__":
    main()
