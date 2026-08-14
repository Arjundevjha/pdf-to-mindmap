# Handoff Document

## Executive Summary
1. **Resolved Fallback Node Root Cause (Groq Rate & Decommissioned Model Pool)**:
   - **Root Cause**: Groq decommissioned older models (`llama3-70b-8192`, `llama3-8b-8192`, `mixtral-8x7b-32768`, `gemma2-9b-it`). When `llama-3.3-70b-versatile` hit a free-tier 6,000 TPM limit (due to `max_tokens=4096`), all decommissioned fallback models threw `400 Bad Request`, forcing the backend into the emergency placeholder fallback node.
   - **Fix**:
     - Updated `ALL_FREE_TIER_MODELS` to active, live Groq models: `llama-3.3-70b-versatile`, `llama-3.1-8b-instant` (30,000 TPM limit), `openai/gpt-oss-120b`, `openai/gpt-oss-20b`.
     - Capped `max_tokens` to `2400` on 70B and `2000` on 8B, preventing TPM reservation spikes and 429 rate limit rejections.
2. **Pure Unified Math & Physics Architecture**:
   - Rebuilt `math` and `physics` prompts in `backend/main.py` using the pure unified schema (`id`, `label`, `summary`, `children`).
   - Deleted `FunctionPlotter.tsx` and standardized on uniform 240px card dimensions.
   - Initial camera orientation smoothly centers onto the root node on document load.
   - Side Details Drawer renders rich KaTeX math typography without reflows.

## Active State of Codebase Files
- [`backend/main.py`](file:///Users/abc/Desktop/Gen%20AI%20research%20tool/backend/main.py): Active Groq model fallback pool (`llama-3.3-70b`, `llama-3.1-8b`, `gpt-oss-120b`, `gpt-oss-20b`), safe TPM token caps, deep multi-bullet STEM prompts.
- [`frontend/src/components/MindmapCanvas.tsx`](file:///Users/abc/Desktop/Gen%20AI%20research%20tool/frontend/src/components/MindmapCanvas.tsx): ReactFlowProvider, initial camera focus on root node, uniform 240px cards, transparent handles.
- [`frontend/src/components/MathRenderer.tsx`](file:///Users/abc/Desktop/Gen%20AI%20research%20tool/frontend/src/components/MathRenderer.tsx): Synchronous KaTeX math and Markdown renderer.
- [`frontend/src/App.tsx`](file:///Users/abc/Desktop/Gen%20AI%20research%20tool/frontend/src/App.tsx): Active model dropdown, clean drawer closing, seamless document persistence.

## Immediate Next Steps
- Services running and verified on `http://localhost:5173`.
