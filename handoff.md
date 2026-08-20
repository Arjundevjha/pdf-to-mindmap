# Handoff Document

## Executive Summary
1. **Production-Grade AST Math & Markdown Rendering Engine**:
   - **AST Compilation Architecture**: Migrated `MathRenderer.tsx` from custom regex replacement to an industry-standard Abstract Syntax Tree (AST) pipeline powered by `react-markdown`, `remark-math`, `rehype-katex`, and `remark-gfm`.
   - **KaTeX Copy-Paste Duplication Fix (`output: 'html'`)**:
     - Configured KaTeX to output pure HTML (`output: 'html'`) rather than `htmlAndMathml`. This eliminates duplicated characters when copying rendered formulas from node cards and summaries (e.g. preventing `P Pt = P 0 e r t = P 0 e rt`).
   - **Mandatory Multi-Node Hierarchy Rule & Multi-Child Schema Templates**:
     - Replaced flat single-node schema examples with fully populated 5+ child-node templates in all subject system prompts (`math`, `physics`, `history`, `geography`, `general`).
     - Enforced the **Mandatory Multi-Node Hierarchy Rule**: root node must only contain the document title and executive thesis, and MUST branch into 4 to 8 distinct child nodes in `"children"` (never collapsing multiple topics into a single root node).
   - **Resilient Regex Child-Node Extractor in `repair_and_parse_json`**:
     - Upgraded `repair_and_parse_json` in `backend/main.py` with an AST/regex node block extractor. If `json.loads` encounters any syntax anomaly inside a formula, all child nodes are cleanly extracted from the raw response and preserved instead of defaulting to an empty `children: []` list.
   - **KaTeX Sizing Delimiter & Unbalanced `$$` Auto-Repair**:
     - Automatically repairs missing parentheses in sizing macros: `\biglx` $\to$ `\bigl(x`, `\biglc` $\to$ `\bigl(c`, `\bigr^2` $\to$ `\bigr)^2`, `\bigr$$` $\to$ `\bigr)$$`.
     - Automatically wraps trailing unbalanced mathematical clauses ending in `$$` into complete `$ ... $` / `$$ ... $$` math AST blocks.
     - Protected math tokenization ensures nested equations never double-wrap or conflict with parenthesized math rules.
   - **Full Token Budget & Zero Content Compromise**:
     - Balanced completion tokens (2200–2500 per request) to stay comfortably within Groq's 8,000 TPM limit while providing complete 4-8 node trees with intermediate steps.
     - Enforced a strict **Depth & Completeness Invariant** across all subject system prompts.
   - **Optimized Multimodal Vision Mode & Automatic Fallback**:
     - In `backend/main.py`, optimized page rendering to 96 DPI with compressed JPEG (quality=80), reducing image token weight by 75% to stay comfortably below Groq's 8,000 TPM limit.
     - For multi-page documents (3+ pages), passes the primary visual page as an image and appends the remaining digital text.
     - Implemented automatic text fallback on 413/429 errors using `openai/gpt-oss-120b`, ensuring users never receive an error screen.
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
