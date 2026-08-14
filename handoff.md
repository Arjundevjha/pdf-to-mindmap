# Handoff Document

## Executive Summary
1. **Decommissioned `llama-3.1-8b-instant`**: Fully removed from frontend selectors, backend aliases, and routing pools. Re-balanced round-robin load distribution across all 6 active Groq models (`llama-3.3-70b-versatile`, `deepseek-r1-distill-llama-70b`, `mixtral-8x7b-32768`, `gemma2-9b-it`, `llama3-70b-8192`, `llama3-8b-8192`) with automatic fallback to 70B models upon rate limits.
2. **Added Mathematics & Physics Subject Modes**:
   - Added dedicated STEM system prompts for `math` and `physics` with standard LaTeX formatting (`$inline$` and `$$block$$`), variable definitions, and SI units.
   - Structured 4-section summary format: `### Core Concept`, `### Equations & Variables` (omitted if none), `### Graph & Curve Behavior` (omitted if none), `### Physical Meaning & Application`.
   - Node payloads support `equations: string[]` and `graph: { fn, domain, xLabel, yLabel, title }`.
3. **Synchronous KaTeX & Dynamic 2D Math/Physics Plotting**:
   - Built `MathRenderer.tsx` using `katex` for fast, zero-reflow LaTeX rendering.
   - Built `FunctionPlotter.tsx` to evaluate mathematical string expressions on the fly and render interactive 2D function curves and trajectories with coordinate crosshairs and tooltips.
   - Integrated live thumbnail preview on Mindmap Canvas Node Cards + interactive full canvas in the Side Details Drawer.
   - Updated Dagre layout bounding boxes so mathematical cards and plotted graphs space out smoothly without overlapping.

## Active State of Codebase Files
- [`backend/main.py`](file:///Users/abc/Desktop/Gen%20AI%20research%20tool/backend/main.py): Active Groq free-tier pool, STEM system prompts for Math & Physics, JSON auto-repair, and Wikimedia rate-limit spacing.
- [`frontend/src/components/MathRenderer.tsx`](file:///Users/abc/Desktop/Gen%20AI%20research%20tool/frontend/src/components/MathRenderer.tsx): Synchronous KaTeX equation and markdown math renderer.
- [`frontend/src/components/FunctionPlotter.tsx`](file:///Users/abc/Desktop/Gen%20AI%20research%20tool/frontend/src/components/FunctionPlotter.tsx): 2D mathematical curve and physics function plotter with axes, grid, and coordinate tooltips.
- [`frontend/src/components/MindmapCanvas.tsx`](file:///Users/abc/Desktop/Gen%20AI%20research%20tool/frontend/src/components/MindmapCanvas.tsx): Canvas node cards with LaTeX preview badges, dynamic 2D plot thumbnails, and adaptive Dagre bounding boxes.
- [`frontend/src/App.tsx`](file:///Users/abc/Desktop/Gen%20AI%20research%20tool/frontend/src/App.tsx): Updated Model & Subject selectors, rich Side Details Drawer with full interactive plotter, featured LaTeX cards, and KaTeX breakdown.
- [`frontend/src/workers/layout.worker.ts`](file:///Users/abc/Desktop/Gen%20AI%20research%20tool/frontend/src/workers/layout.worker.ts): ESM Web Worker for off-thread Dagre graph layout.

## Immediate Next Steps
- User visual verification of Mathematics and Physics mindmap generation with live 2D plotting on `http://localhost:5173`.
