#!/bin/bash
# Install runtime dependencies on Astra Linux 1.8.4
set -euo pipefail
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip xdelta3 p7zip-full gcc
echo "Optional PostgreSQL: sudo apt-get install -y postgresql"
echo "Python package: pip install -e '.[dev,postgres]'"
