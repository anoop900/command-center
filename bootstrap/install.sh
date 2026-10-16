#!/bin/sh
# Command Center installer for macOS and Linux. No root needed.
#   sh bootstrap/install.sh                 install from the folder this script sits in
#   sh bootstrap/install.sh --source PATH   install from another folder (a clone or an unzipped download)
#   sh bootstrap/install.sh --dry-run       print what it would do and change nothing
# Steps: find Python 3.11+, make a private virtual environment, install cc into it, link `cc` into ~/.local/bin,
# then run `cc setup`, which opens the wizard in your browser. It installs nothing system-wide and edits no shell files.
set -eu

SOURCE=$(cd "$(dirname "$0")/.." && pwd)
DRY=0
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY=1 ;;
        --source) [ $# -ge 2 ] || { echo "ERROR: --source needs a folder." >&2; exit 1; }; SOURCE=$2; shift ;;
        *) echo "ERROR: unknown option '$1'. Use --source PATH or --dry-run." >&2; exit 1 ;;
    esac
    shift
done

fail() { echo "ERROR: $1" >&2; exit 1; }
step() {
    if [ "$DRY" = 1 ]; then echo "WOULD: $1"; return 0; fi
    echo "-> $1"
}

[ -f "$SOURCE/pyproject.toml" ] || fail "No pyproject.toml in '$SOURCE'. Pass --source with the Command Center folder."

PY=
for c in python3.13 python3.12 python3.11 python3 python; do
    command -v "$c" >/dev/null 2>&1 || continue
    if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then PY=$c; break; fi
done
if [ -z "$PY" ]; then
    case "$(uname -s)" in
        Darwin) hint='Install it with Homebrew ("brew install python") or from https://www.python.org/downloads/, then run this script again.' ;;
        *) hint='Install it with your package manager (for example "sudo apt install python3 python3-venv" or "sudo dnf install python3"), then run this script again.' ;;
    esac
    fail "Python 3.11 or later was not found. $hint"
fi
echo "Using Python: $(command -v "$PY")"

HOME_DIR=${XDG_DATA_HOME:-$HOME/.local/share}/command-center
VENV=$HOME_DIR/venv
BIN=$HOME/.local/bin

step "create a virtual environment in $VENV"
if [ "$DRY" = 0 ]; then
    mkdir -p "$HOME_DIR"
    "$PY" -m venv "$VENV" || fail "Could not create a virtual environment. On Debian or Ubuntu run: sudo apt install python3-venv"
fi
step "pip install '$SOURCE'"
if [ "$DRY" = 0 ]; then "$VENV/bin/python" -m pip install --quiet --upgrade "$SOURCE" || fail "pip could not install Command Center."; fi
if [ "$DRY" = 0 ] && ! command -v soffice >/dev/null 2>&1 && ! command -v libreoffice >/dev/null 2>&1; then
    echo "NOTE: PDF resumes need LibreOffice. Install it (brew install --cask libreoffice, or your package manager's libreoffice), then build kits."
fi
step "link cc into $BIN"
if [ "$DRY" = 0 ]; then
    mkdir -p "$BIN"
    ln -sf "$VENV/bin/cc" "$BIN/cc"
fi
if [ "$DRY" = 1 ]; then echo "WOULD: run cc setup"; exit 0; fi

case ":$PATH:" in
    *":$BIN:"*) ;;
    *) echo "Note: $BIN is not on your PATH. Add this line to your shell profile to run 'cc' from anywhere:"
       echo "  export PATH=\"$BIN:\$PATH\"" ;;
esac
echo "Starting the setup wizard. Your browser opens at http://127.0.0.1:8765/ (Ctrl+C here to stop)."
exec "$VENV/bin/cc" setup
