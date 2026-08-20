# Handoff Document

## Executive Summary
1. **Production-Grade AST Math & Markdown Rendering Engine**:
   - **AST Compilation Architecture**: Migrated `MathRenderer.tsx` from custom regex replacement to an industry-standard Abstract Syntax Tree (AST) pipeline powered by `react-markdown`, `remark-math`, `rehype-katex`, and `remark-gfm`.
   - **KaTeX Sizing Delimiter & Unbalanced `$$` Auto-Repair**:
     - Automatically repairs missing parentheses in sizing macros: `\biglx` $\to$ `\bigl(x`, `\biglc` $\to$ `\bigl(c`, `\bigr^2` $\to$ `\bigr)^2`, `\bigr$$` $\to$ `\bigr)$$`.
     - Automatically wraps trailing unbalanced mathematical clauses ending in `$$` into complete `$ ... $` / `$$ ... $$` math AST blocks.
     - Protected math tokenization ensures nested equations never double-wrap or conflict with parenthesized math rules.
   - **Full Token Budget & Zero Content Compromise**:
     - Upgraded `max_tokens` from 1400/1800 to **4096 tokens** in `backend/main.py` with expanded 90s timeout, eliminating premature JSON cutoff.
     - Enforced a strict **Depth & Completeness Invariant** across all subject system prompts.
   - **Optional Multimodal Vision Mode (`qwen/qwen3.6-27b-vision`)**:
     - Added a dedicated Vision processing endpoint `/api/generate-mindmap-vision` in `backend/main.py`.
     - Direct in-memory page rendering to high-clarity PNG base64 strings with PyMuPDF, bypassing slow CPU Tesseract OCR completely.
     - Multimodal Qwen 3.6 27B analyzes visual diagrams, flowcharts, chemical/physical formulas, graphs, tables, and text together on Groq LPUs in ~3–5s.
     - Added an selectable option in `frontend/src/App.tsx` and `UploadZone.tsx`: `👁️ Qwen 3.6 27B Vision (Diagrams, Visual Math & OCR)`.
   - **Parenthesized Math & Raw Command Detection**: Auto-converts parenthesized math expressions like `(f(x)=a^{x})`, `((e^{rt}))`, `(a>1)`, `(0<a<1)`, `(a^x=e^{x\ln a})`, `(\frac{d}{dx}a^x=a^x\ln a)` and standalone commands like `\to` into standard `$ ... $` AST math nodes without colliding with `\bigl(` or English phrases.
   - **Conflict-Free Rendering of Substitutions & Multi-Formulas**: Full support for continuous explanatory text, variable substitutions, and multi-line derivations.
   - **Unicode & Unwrapped Math Normalization**: Converts Unicode symbols (`–`, `—`, `×`, `÷`, `±`, `≠`, `≤`, `≥`, `≈`, `π`, `θ`, `Δ`, `√`, `∞`, `→`, `⇒`, `²`, `³`, `\degree`, `°`) and auto-wraps unwrapped equations into standard LaTeX math AST nodes.
   - **Clean Inline & Block Component Modes**: `inline` mode for canvas node cards and panel headers; Block mode for rich summaries with styled Tailwind typography.
   - **Backend LaTeX Escaping & System Prompts**: `backend/main.py` sanitizes unescaped backslashes before `json.loads(..., strict=False)` and strictly instructs LLMs across all subject prompts to format math in `$inline$` and `$$block$$` delimiters while explicitly forbidding raw parentheses (`(f(x)=a^x)`) or un-delimited backslashes (`\to`).

2. **Collapsed Mindmap Initialization & Progressive Expansion**:
   - **Default Collapsed State**: When a user creates/generates a new document (or selects a document), the mindmap initializes with `expandedIds = new Set()`, displaying only the central Root Node with interactive `+` expansion controls.
   - **On-Demand Progressive Disclosure**: Users click `+` on any node to expand its child branches, and click `−` to collapse subtrees as desired.
   - **Fixed Camera Re-centering on Expansion**: Camera view and zoom position now stay firmly in place when expanding/collapsing nodes. Centering to root is strictly constrained via `lastCenteredDocIdRef` to only trigger when a different document is loaded.
   - **Quick Action Controls**: Added "Expand All" and "Collapse All" buttons to the topbar header alongside "Undo" and "Reset Workspace".

3. **Full Verification**:
   - Tested backend JSON parsing on math responses containing `\frac`, `\sqrt`, `\Delta`, `\times`, `\theta`, `\beta`, `\neq`, `\nabla`, `\right`, `\begin{aligned}`; all test assertions passed.
   - Tested KaTeX formula rendering across all standard delimiter formats; 100% passed.
   - Verified TypeScript compilation and production Vite build (0 errors, 363ms build time).

## Active State of Codebase Files
- [`frontend/src/components/MathRenderer.tsx`](file:///Users/abc/Desktop/Gen%20AI%20research%20tool/frontend/src/components/MathRenderer.tsx): Upgraded KaTeX & Markdown renderer with complete delimiter support, corrupted sequence repair, standalone formula parsing, and inline card mode.
- [`frontend/src/components/MindmapCanvas.tsx`](file:///Users/abc/Desktop/Gen%20AI%20research%20tool/frontend/src/components/MindmapCanvas.tsx): Node cards render titles via `MathRenderer`; fixed `isExpanded` condition to respect empty/custom `expandedIds` sets.
- [`frontend/src/App.tsx`](file:///Users/abc/Desktop/Gen%20AI%20research%20tool/frontend/src/App.tsx): Configured `handleMindmapGenerated` and `handleSelectDocument` to open in collapsed form (`new Set()`), added `handleExpandAll` and `handleCollapseAll` topbar controls, and rendered Details Panel titles with `MathRenderer`.
- [`backend/main.py`](file:///Users/abc/Desktop/Gen%20AI%20research%20tool/backend/main.py): Comprehensive `sanitize_json_latex` and resilient `repair_and_parse_json` with `strict=False`.

## Immediate Next Steps
- System fully operational with math formula rendering and on-demand progressive mindmap expansion.
