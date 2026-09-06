#!/usr/bin/env bash
# Exit on error
set -o errexit

echo "[*] Installing Python backend dependencies..."
python3 -m pip install -r requirements.txt

echo "[*] Ensuring Tesseract OCR binary and traineddata..."
python3 setup_tesseract.py
