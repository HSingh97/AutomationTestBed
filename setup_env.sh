#!/bin/bash

echo "[+] Detecting Operating System..."
OS="$(uname -s)"
PYTHON_CMD="python3"

if [ "$OS" = "Linux" ]; then
    echo "[+] Linux detected. Installing system dependencies via apt..."
    sudo apt-get update -y
    sudo apt-get install -y python3-pip python3-venv build-essential cargo sshpass snmp
elif [ "$OS" = "Darwin" ]; then
    echo "[+] macOS detected. Installing system dependencies via Homebrew..."
    if ! command -v brew &> /dev/null; then
        echo "[-] Homebrew is not installed. Please install Homebrew first: https://brew.sh/"
        exit 1
    fi
    # Force stable Python 3.11 to avoid bleeding-edge C-compiler errors on M-series Macs
    brew install python@3.11 sshpass net-snmp
    PYTHON_CMD="python3.11"
else
    echo "[-] Unsupported Operating System: $OS"
    exit 1
fi

echo -e "\n[+] Creating Python Virtual Environment..."
# Creates the virtual environment using the OS-specific Python command
$PYTHON_CMD -m venv .venv
source .venv/bin/activate

echo -e "\n[+] Upgrading core build tools..."
pip install --upgrade pip setuptools wheel

echo -e "\n[+] Installing Framework Requirements..."
pip install -r requirements.txt

echo -e "\n[+] Installing Playwright Browsers..."
if [ "$OS" = "Linux" ]; then
    playwright install --with-deps chromium
else
    # Mac doesn't use apt dependencies for Playwright
    playwright install chromium
fi

echo -e "\n[=======================================================]"
echo "[+] Setup Complete! Run 'source .venv/bin/activate' to start."
echo "[=======================================================]"