#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BIN="${XDG_BIN_HOME:-$HOME/.local/bin}"
mkdir -p "$BIN"
ln -sfn "$ROOT/imprint" "$BIN/imprint"
chmod +x "$ROOT/imprint" "$ROOT/imprint-engine.py"
echo "Linked $BIN/imprint -> $ROOT/imprint"
case ":$PATH:" in
  *":$BIN:"*) ;;
  *) echo "Put $BIN on your PATH if imprint is not found." ;;
esac
