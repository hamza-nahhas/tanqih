#!/bin/bash
# Tanqih: the first run installs what it needs (uv, Python, the libraries), then it opens the interface.
# Double-click this file in Finder. Keep the Terminal window open while you work.
cd "$(dirname "$0")" || exit 1
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  echo "First run: installing uv (it brings Python and the libraries)..."
  if ! curl -LsSf https://astral.sh/uv/install.sh | sh; then
    echo "Could not install uv. Check the internet connection and try again."
    read -r -p "Press Enter to close."
    exit 1
  fi
  export PATH="$HOME/.local/bin:$PATH"
fi
echo "Starting Tanqih (the first run downloads Python and the libraries; this takes a few minutes)..."
uv run python -m tanqih || read -r -p "Something went wrong (see above). Press Enter to close."
