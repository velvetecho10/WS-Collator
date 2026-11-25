#!/bin/bash
cd "$(dirname "$0")"

APP_VERSION="v28"

echo "============================================"
echo " WS Timestamp Collator $APP_VERSION"
echo " Running from: $(pwd)"
echo "============================================"
echo

# Shared venv location, deliberately OUTSIDE this app folder. Every
# copy of this app you ever extract points at the same
# environment, so re-downloading or re-extracting a new version doesn't
# force a full reinstall - only a genuinely fresh environment does.
VENV_DIR="$HOME/.ws_tools/ws_timestamp_collator_venv"

if [ -f "$VENV_DIR/pyvenv.cfg" ] && [ -f "$VENV_DIR/bin/activate" ]; then
    echo "Existing shared environment found - checking dependencies..."
    source "$VENV_DIR/bin/activate"
    pip install -r requirements.txt --quiet
else
    if [ -d "$VENV_DIR" ]; then
        echo "Found an environment folder but it looks incomplete - rebuilding it."
        rm -rf "$VENV_DIR"
    else
        echo "No shared environment found yet - setting one up now. This only happens once, ever, regardless of how many versions of this app you download later."
    fi
    mkdir -p "$(dirname "$VENV_DIR")"
    echo "Setting up environment at $VENV_DIR ..."
    python3 -m venv "$VENV_DIR"
    source "$VENV_DIR/bin/activate"
    echo "Installing dependencies..."
    pip install --upgrade pip > /dev/null
    pip install -r requirements.txt
fi

echo
echo "Launching WS Timestamp Collator..."
streamlit run App.py
