#!/bin/bash
# Install runtime dependencies on RedOS 8
set -euo pipefail
sudo dnf install -y python3 python3-pip xdelta3 p7zip gcc
echo "Optional PostgreSQL: sudo dnf install -y postgresql-server postgresql"
echo "Python package: pip install -e '.[dev,postgres]'"
