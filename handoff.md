# Handoff Document

## Executive Summary
1. **Pure Rebuild of Math & Physics Completed**:
   - Deleted `FunctionPlotter.tsx` and eliminated fragile custom JSON schema fields (`graph`, `equations`).
   - Rebuilt `math` and `physics` system prompts in `backend/main.py` using the pure unified schema (`id`, `label`, `summary`, `children`) identical to History and Geography.
   - All equations ($...$, $$...$$), proofs, and worked problems are embedded in the rich Markdown summary and rendered via synchronous KaTeX in the Details Drawer.
2. **Standardized Canvas & Initial Camera Orientation**:
   - `MindmapCanvas.tsx` standardizes on uniform 240px card dimensions with transparent connection handles (zero dot artifacts).
   - Integrated `useReactFlow()` and `<ReactFlowProvider>` so the camera smoothly centers directly on the first (root) node at 100% zoom upon entering any document.
   - All nodes are expanded by default (`expandedIds.size === 0 || expandedIds.has(node.id)`).
3. **End-to-End Verified via `agent-browser`**:
   - Tested on user PDF `/Users/abc/Desktop/e488bdd2a0d84c639020eb5967762805.pdf`.
   - Verified clean tree rendering, camera centering, drawer open/close `X` behavior, and KaTeX math formatting.

## Active State of Codebase Files
- [`backend/main.py`](file:///Users/abc/Desktop/Gen%20AI%20research%20tool/backend/main.py): In-depth STEM prompts with pure unified schema, `sanitize_json_latex` JSON parser, native digital PDF extraction priority.
- [`frontend/src/components/MindmapCanvas.tsx`](file:///Users/abc/Desktop/Gen%20AI%20research%20tool/frontend/src/components/MindmapCanvas.tsx): ReactFlowProvider, initial camera orientation on root node, uniform 240px card layout, transparent handles.
- [`frontend/src/components/MathRenderer.tsx`](file:///Users/abc/Desktop/Gen%20AI%20research%20tool/frontend/src/components/MathRenderer.tsx): Synchronous KaTeX math and Markdown renderer.
- [`frontend/src/App.tsx`](file:///Users/abc/Desktop/Gen%20AI%20research%20tool/frontend/src/App.tsx): Clean drawer closing, seamless document persistence, accessible document list.

## Immediate Next Steps
- Application is live and verified on `http://localhost:5173`.
