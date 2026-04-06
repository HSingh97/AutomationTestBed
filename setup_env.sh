#!/bin/bash
echo "[+] Updating OS and installing base dependencies..."
sudo apt-get update -y
sudo apt-get install -y \
    python3-pip \
    python3-venv \
    build-essential \
    cargo \
    sshpass \
    snmp

echo "[+] Creating Python Virtual Environment..."
python3 -m venv venv
source venv/bin/activate

echo "[+] Installing high-speed Python packages..."
pip3 install --upgrade pip
pip3 install -r requirements.txt

echo "[+] Installing Playwright browsers and OS dependencies..."
playwright install --with-deps chromium