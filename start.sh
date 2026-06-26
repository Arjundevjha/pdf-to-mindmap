#!/bin/bash

# Visual styling
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}===============================================${NC}"
echo -e "${BLUE}    PDF-to-Mindmap Study App Launcher          ${NC}"
echo -e "${BLUE}===============================================${NC}"

# Stop script on error
set -e

# Using system Python as requested

# Trap Ctrl+C (SIGINT) to cleanly shutdown both servers
cleanup() {
    echo -e "\n\n${YELLOW}[!] Stopping all application servers...${NC}"
    if [ -n "$BACKEND_PID" ]; then
        kill $BACKEND_PID 2>/dev/null || true
    fi
    if [ -n "$FRONTEND_PID" ]; then
        kill $FRONTEND_PID 2>/dev/null || true
    fi
    echo -e "${GREEN}[✓] Cleaned up background processes. Goodbye!${NC}"
    exit 0
}
trap cleanup SIGINT

# Start Backend Server
echo -e "${GREEN}[*] Starting FastAPI Backend on http://localhost:8000 ...${NC}"
cd backend
python3 -m uvicorn main:app --port 8000 --host 127.0.0.1 --reload > /dev/null 2>&1 &
BACKEND_PID=$!
cd ..

# Start Frontend Dev Server
echo -e "${GREEN}[*] Starting Vite Frontend on http://localhost:5173 ...${NC}"
cd frontend
npm run dev &
FRONTEND_PID=$!
cd ..

echo -e "${BLUE}===============================================${NC}"
echo -e "${GREEN}[✓] Application is running!${NC}"
echo -e "    - Backend API: http://localhost:8000"
echo -e "    - Frontend Web: http://localhost:5173"
echo -e "${YELLOW}Press [Ctrl+C] to stop both servers at any time.${NC}"
echo -e "${BLUE}===============================================${NC}"

# Keep script running and wait for background processes
set +e
wait $BACKEND_PID $FRONTEND_PID
