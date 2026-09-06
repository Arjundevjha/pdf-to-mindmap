#!/usr/bin/env bash
# exit on error
set -o errexit

# 0. Install system dependencies (Tesseract OCR & English traineddata) on Debian/Ubuntu/Render
if command -v apt-get &> /dev/null; then
    echo "[*] Detected apt-get. Installing Tesseract OCR and language data..."
    apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng libtesseract-dev || true
fi

# 1. Install frontend packages and build the React production bundle
cd frontend
npm install
npm run build
cd ..

# 2. Install Python backend dependencies
cd backend
python3 -m pip install -r requirements.txt
cd ..