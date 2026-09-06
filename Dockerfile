FROM python:3.11-slim

# Install system dependencies: Tesseract OCR, English traineddata, and Node.js for frontend build
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    libtesseract-dev \
    curl \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install frontend dependencies and build production static bundle
COPY package*.json ./
COPY frontend/package*.json ./frontend/
RUN cd frontend && npm install

COPY frontend ./frontend
RUN cd frontend && npm run build

# Install backend Python dependencies
COPY backend/requirements.txt ./backend/
RUN cd backend && pip install --no-cache-dir -r requirements.txt

# Copy backend application source
COPY backend ./backend
COPY handoff.md ./

EXPOSE 8000

ENV PORT=8000
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
