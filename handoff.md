# Handoff Document

## Executive Summary
1. **Math & Physics Rendering & KaTeX Styling**:
   - Linked top-level `katex.min.css` in both `<head>` ([`frontend/index.html`](file:///Users/abc/Desktop/Gen%20AI%20research%20tool/frontend/index.html)) and `index.css` to fix fraction/radical positioning and eliminate "dots for math".
   - Implemented `sanitize_json_latex()` in [`backend/main.py`](file:///Users/abc/Desktop/Gen%20AI%20research%20tool/backend/main.py) to escape raw LaTeX backslashes before `json.loads()`, eliminating control character corruption (`\frac` becoming `\x0crac`, `\theta` becoming `\t`).
   - Enhanced [`frontend/src/components/MathRenderer.tsx`](file:///Users/abc/Desktop/Gen%20AI%20research%20tool/frontend/src/components/MathRenderer.tsx) with control character recovery, unwrapped LaTeX pattern detection, and error boundaries.
2. **Comprehensive Node Depth & Multi-Bullet Structure**:
   - Upgraded Math & Physics system prompts with strict **No One-Liners** depth invariants.
   - Enforced rich multi-bullet sections across every node:
     - `### Core Concept`: Mathematical/Physical Principle, Key Mechanism & Derivation, Conditions & Edge Cases.
     - `### Equations & Variables`: Primary Formulation ($$LaTeX$$), Variable Definitions & Units, Discriminant/Dimensional Analysis.
     - `### Graph & Curve Behavior`: Key Geometric Features, Roots/Turning Points, Domain & Range, Dynamics.
     - `### Physical Meaning & Application`: Real-World Engineering Applications, Step-by-Step Worked Problems with full algebraic substitutions and numerical answers.
3. **Auto-Expansion & Navigation**:
   - Configured `handleMindmapGenerated` and `handleSelectDocument` in [`frontend/src/App.tsx`](file:///Users/abc/Desktop/Gen%20AI%20research%20tool/frontend/src/App.tsx) to auto-expand all tree nodes across the canvas and open the root node in the Details Drawer.

## Active State of Codebase Files
- [`backend/main.py`](file:///Users/abc/Desktop/Gen%20AI%20research%20tool/backend/main.py): In-depth STEM system prompts, `sanitize_json_latex` JSON parser, active Groq 6-model fallback pool.
- [`frontend/src/components/MathRenderer.tsx`](file:///Users/abc/Desktop/Gen%20AI%20research%20tool/frontend/src/components/MathRenderer.tsx): Synchronous KaTeX math renderer with control character sanitization.
- [`frontend/src/components/FunctionPlotter.tsx`](file:///Users/abc/Desktop/Gen%20AI%20research%20tool/frontend/src/components/FunctionPlotter.tsx): 2D mathematical curve and physics function plotter.
- [`frontend/src/components/MindmapCanvas.tsx`](file:///Users/abc/Desktop/Gen%20AI%20research%20tool/frontend/src/components/MindmapCanvas.tsx): Canvas node cards with LaTeX preview badges and 2D plot thumbnails.
- [`frontend/src/App.tsx`](file:///Users/abc/Desktop/Gen%20AI%20research%20tool/frontend/src/App.tsx): Auto-expanded canvas nodes, Side Details Drawer with full interactive plotter and deep KaTeX markdown breakdowns.
- [`frontend/index.html`](file:///Users/abc/Desktop/Gen%20AI%20research%20tool/frontend/index.html) & [`frontend/src/index.css`](file:///Users/abc/Desktop/Gen%20AI%20research%20tool/frontend/src/index.css): KaTeX styles and webfonts linked.

## Immediate Next Steps
- User visual verification on `http://localhost:5173`.
