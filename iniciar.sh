#!/usr/bin/env bash
# Inicia a interface web do conversor OFX -> PDF (Linux / macOS)
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "Preparando o ambiente pela primeira vez..."
  python3 -m venv .venv
  .venv/bin/pip install --quiet --upgrade pip
  .venv/bin/pip install --quiet -r requirements.txt
fi
exec .venv/bin/python -m ofx2pdf --web "$@"
