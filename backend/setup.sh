#!/usr/bin/env bash
# Exit immediately on error
set -o errexit

echo "[*] Installing Python dependencies..."
python3 -m pip install -r requirements.txt

echo "[*] Provisioning Tesseract OCR standalone binary & language models..."
python3 setup_tesseract.py

echo "[*] Setup completed successfully."
