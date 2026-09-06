# Handoff Document

## Executive Summary

1. **Production Tesseract OCR & Text Scanning Architecture**:
   - **Root Cause Analysis (`513a6019b4114e34a2a9f88c292343e6.pdf`)**:
     - The document is a pure scanned image containing **0 digital text characters**.
     - In local macOS, Tesseract 5.5.1 was pre-installed at `/usr/local/bin/tesseract` via Homebrew, successfully extracting 643 characters.
     - In production Linux (Render/PaaS), `pip install pytesseract` in `requirements.txt` installed only the Python wrapper, not the underlying C++ binary (`/usr/bin/tesseract`) or English traineddata (`eng.traineddata`).
     - `build.sh` did not install system packages, causing `pytesseract` to throw `TesseractNotFoundError`, which was swallowed into `[OCR Error: ...]` and silently dropped, resulting in a 400 Bad Request error.
   - **System Package Provisioning**:
     - **[`build.sh`](file:///Users/abc/Desktop/pdf-to-mindmap/build.sh)**: Added automated `apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng libtesseract-dev` for Render / Debian / Ubuntu.
     - **[`Aptfile`](file:///Users/abc/Desktop/pdf-to-mindmap/Aptfile)**: Created root `Aptfile` listing `tesseract-ocr`, `tesseract-ocr-eng`, and `libtesseract-dev` for buildpack-based PaaS deployments.
     - **[`Dockerfile`](file:///Users/abc/Desktop/pdf-to-mindmap/Dockerfile)**: Created production multi-stage Dockerfile baking in Python 3.11, Tesseract OCR, English data, and the built React frontend.
   - **Backend OCR Engine Hardening ([`backend/main.py`](file:///Users/abc/Desktop/pdf-to-mindmap/backend/main.py))**:
     - **Multi-Path Binary Discovery (`get_tesseract_cmd`)**: Resolves `TESSERACT_CMD`, `shutil.which`, `/usr/bin/tesseract`, `/usr/local/bin/tesseract`, `/opt/homebrew/bin/tesseract`, `/app/bin/tesseract`, and `~/.local/bin/tesseract`.
     - **Tessdata Directory Auto-Configuration**: Auto-detects `tessdata` paths (`/usr/share/tesseract-ocr/5/tessdata`, `/usr/share/tessdata`) and sets `TESSDATA_PREFIX`.
     - **ThreadPoolExecutor Migration**: Replaced `ProcessPoolExecutor` with `ThreadPoolExecutor`. Because Tesseract runs as an external subprocess releasing the Python GIL, threads avoid multiprocessing fork restrictions, memory bloat, and IPC serialization failures in cloud containers.
     - **Actionable Diagnostic Logging**: If OCR fails or errors, logs the exact failure with `logger.error` and returns clear diagnostic detail in HTTP responses.
     - **Health Endpoint**: `/api/health` now reports `"tesseract_available": true/false` and `"tesseract_path": str`.

2. **Tri-Provider Cloud Architecture (Groq + Google Gemini + OpenRouter)**:
   - **Unified Multi-Cloud Routing**: Added full integration for **OpenRouter** alongside **Google Gemini** and **Groq Cloud**.
   - **Supported Model Families**:
     - **Groq Cloud**: `openai/gpt-oss-20b` (Ultra-Fast ~580 tok/s), `openai/gpt-oss-120b` (Flagship 128k context), `qwen/qwen3.8-27b`, `qwen/qwen3.6-27b`.
     - **Google Gemini Cloud**: `gemini-2.5-flash` (High Speed, native JSON mode), `gemini-3.5-flash` (Advanced reasoning).
     - **OpenRouter Cloud**: `deepseek/deepseek-chat` (DeepSeek V3, 128k context), `meta-llama/llama-3.3-70b-instruct`.
   - **Independent Rate-Limit Pool & Auto-Failover**: The backend dynamically balances across all configured providers. If any single provider encounters a 429 quota exhaustion or transient outage, requests instantly fail over across provider boundaries.
   - **Frontend Dropdown & Friendly Badges**: Updated model selection in [`frontend/src/App.tsx`](file:///Users/abc/Desktop/pdf-to-mindmap/frontend/src/App.tsx) and [`frontend/src/components/UploadZone.tsx`](file:///Users/abc/Desktop/pdf-to-mindmap/frontend/src/components/UploadZone.tsx).

3. **Comprehensive Mathematical Syntax Repair & KaTeX Auto-Healing**:
   - **Zero-Width Character Sanitization**: Strips invisible OCR artifacts (`\u200b`, `\u200c`, `\u200d`, `\ufeff`) that previously broke regex match boundaries.
   - **Unicode Symbol & Bullet Normalization**: Normalizes unicode minus `−` (U+2212) $\to$ `-`, unicode asterisk `∗` (U+2217) $\to$ `*`, preventing broken markdown bullet headers like `−∗∗GoverningIdentity∗∗:`.
   - **Corrupted Sizing Macro Healing (`≤ft` $\to$ `\left`)**: Automatically repairs `≤ft(` and `\?≤ft` into standard LaTeX `\left(` prior to any inequality replacements.
   - **Vertical Multiline Fraction Reconstruction**: Reassembles vertical OCR fractions inside parentheses (`( \n y \n x \n )` or `( \n 5 \n 25 \n )`) into standard LaTeX fractions `\left(\frac{x}{y}\right)` and `\left(\frac{25}{5}\right)`.
   - **Quotient Fraction Subtraction Inversion**: Corrects inverted logarithmic quotient fractions (e.g. $\log_a \left(\frac{y}{x}\right) = \log_a x - \log_a y \to \log_a \left(\frac{x}{y}\right) = \log_a x - \log_a y$, and numerical $\log_5 \left(\frac{5}{25}\right) = \log_5 25 - \log_5 5 \to \log_5 \left(\frac{25}{5}\right) = \log_5 25 - \log_5 5 = 2 - 1 = 1$).
   - **Broken Log Subscript Collapsing**: Subscripts multiline splits like `log \n 5` into `\log_5`.
   - **Mandatory LaTeX Delimiter Wrapping**:
     - Automatically ensures all `Governing Identity` formulas are wrapped in display math `$$ ... $$`.
     - Automatically wraps inline equations in `Problem Walkthrough` steps in `$ ... $`.
   - **Dual-Layer Defense**: Implemented identically across backend Python (`repair_math_syntax_backend`) and frontend TypeScript AST pipeline (`prepareMathInput` in [`MathRenderer.tsx`](file:///Users/abc/Desktop/pdf-to-mindmap/frontend/src/components/MathRenderer.tsx)).

## Active State of Codebase Files
- [`build.sh`](file:///Users/abc/Desktop/pdf-to-mindmap/build.sh): Automated `apt-get` installation for `tesseract-ocr`, `tesseract-ocr-eng`, and `libtesseract-dev`.
- [`Aptfile`](file:///Users/abc/Desktop/pdf-to-mindmap/Aptfile): System package list for cloud buildpacks.
- [`Dockerfile`](file:///Users/abc/Desktop/pdf-to-mindmap/Dockerfile): Multi-stage container definition with Tesseract OCR, Node.js frontend builder, and Python 3.11 backend.
- [`backend/main.py`](file:///Users/abc/Desktop/pdf-to-mindmap/backend/main.py): Multi-path Tesseract discovery, ThreadPoolExecutor OCR engine, diagnostic error logger, `/api/health` status reporting, tri-provider router.
- [`frontend/src/components/MathRenderer.tsx`](file:///Users/abc/Desktop/pdf-to-mindmap/frontend/src/components/MathRenderer.tsx): KaTeX AST renderer with delimiter auto-healing.
- [`frontend/src/App.tsx`](file:///Users/abc/Desktop/pdf-to-mindmap/frontend/src/App.tsx): Multi-cloud model selection UI.
- [`frontend/src/components/UploadZone.tsx`](file:///Users/abc/Desktop/pdf-to-mindmap/frontend/src/components/UploadZone.tsx): Model friendly names and multi-cloud badges.
- [`handoff.md`](file:///Users/abc/Desktop/pdf-to-mindmap/handoff.md): Comprehensive documentation.

## Verification & Benchmarks
- **Live Scanned PDF Test (`513a6019b4114e34a2a9f88c292343e6.pdf`)**: Successfully processed via `/api/upload-pdf`, extracted **641 characters** (`Chapter 6: Exponential & Logarithmic Functions...`) with `ocr_processed: true` in 0.59s.
- **Health Check (`/api/health`)**:
  ```json
  {"status":"ok","tesseract_available":true,"tesseract_path":"/usr/local/bin/tesseract","groq_configured":true,"gemini_configured":true,"openrouter_configured":true}
  ```
- **Backend Compilation**: `python3 -m py_compile backend/main.py` passed with 0 errors.
- **Frontend Production Build**: `npm run build` in `frontend/` completed with 0 errors.
