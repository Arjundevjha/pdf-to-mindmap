#!/usr/bin/env bash
# exit on error
set -o errexit

# 1. Install frontend packages and build the React production bundle
cd frontend
npm install
npm run build
cd ..

# 2. Install Python backend dependencies
cd backend
python3 -m pip install -r requirements.txt
cd ..