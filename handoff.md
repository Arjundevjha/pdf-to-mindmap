# Handoff Document

## Executive Summary

1. **Production-Grade AST Math & Markdown Rendering Engine**:
   - **AST Compilation Architecture**: Migrated `MathRenderer.tsx` from custom regex replacement to an industry-standard Abstract Syntax Tree (AST) pipeline powered by `react-markdown`, `remark-math`, `rehype-katex`, and `remark-gfm`.
   - **KaTeX Copy-Paste Duplication Fix (`output: 'html'`)**: Pure HTML rendering prevents duplicated characters when copying formulas from node cards and summaries.
   - **Backend Math Syntax Validator & Recursive Sanitizer (`sanitize_mindmap_math`)**:
     - Auto-heals broken expressions like `a[x+\frac{b}{2a}$$^2`, missing fraction exponents (`x+\frac{b}{2a}^2 \to \left(x+\frac{b}{2a}\right)^2`), unparenthesized completing-the-square clauses, and step markers `{2}:$$`.
     - Recursively runs across all mindmap nodes before returning JSON to the client.
   - **Fragmented Delimiter & Unclosed Macro Auto-Healing**:
     - Automatically repairs leading commands outside math delimiters: `\Delta$(k)>0$` $\to$ `$\Delta(k) > 0$`.
     - Automatically absorbs unbracketed discriminant statements: `\Delta(k) > 0` $\to$ `$\Delta(k) > 0$`.
     - Reconstructs scrambled PDF vertical fraction text into canonical surd rationalization identities: `For $\frac{A}{\sqrt{p} + \sqrt{q}}$, multiply numerator and denominator by $\frac{\sqrt{p} - \sqrt{q}}{\sqrt{p} - \sqrt{q}}$`.

2. **Syllabus-Aligned Revision Schemas & Dynamic Topic Naming**:
   - Refactored all system prompts (`math`, `physics`, `history`, `geography`, `general`) specifically for **Secondary Revision Notes**.
   - Replaced academic jargon ("Main Thesis", "Professor", "Architectural Framework") with practical, exam-focused headers:
     - `### Core Concept & Exam Rule` $\to$ **Key Principle** & **Step-by-Step Method** & **Exam Pitfalls & Conditions**.
     - `### Formulas & Identities` $\to$ clean display equations and symbol definitions.
     - `### Worked Exam Example` $\to$ concrete problem walkthroughs with intermediate substitutions and final answers.
   - **Specific Root Topic Naming**: Root node labels now dynamically state the exact academic topic name (e.g. *Algebraic Foundations & Quadratic Functions*, *Kinematics & Dynamics*) without generic `"Document Overview"` or `"O-Level"` prefixes.

3. **1-Page Intervaled Visual Chunking & Direct Child Promotion**:
   - In `/api/generate-mindmap-vision`, multi-page documents are processed page-by-page (1 page per request at 96 DPI JPEG).
   - Requests are intervaled by **1.5s** to stay safely below Groq's 8,000 TPM limit.
   - **Direct Child Promotion (Zero Wrapper Nodes)**: Subtopic branches from each page are promoted directly as top-level children of the master root, eliminating artificial intermediate wrapper cards.
   - **Calibrated Token Limits**: Token budgets are calibrated per model (2500 for `openai/gpt-oss-120b`, 2000 for `qwen/qwen3.6-27b`) with fast fallback to `openai/gpt-oss-120b` (30k TPM) to prevent 429 rate limits.

4. **Collapsed Mindmap Initialization & Progressive Expansion**:
   - Mindmaps initialize in a clean collapsed state with interactive `+` expansion controls.
   - Camera zoom and viewport remain stable on expansion/collapse.
   - Topbar includes "Expand All" and "Collapse All" quick-action controls.

5. **Groq Model Migration & Decommissioning of Groq Compound**:
   - **Decommissioned Models**: `groq/compound` and `groq/compound-mini` have been retired and removed from active model options.
   - **Active Production Model Suite**:
     - Flagship & High-Capacity: `openai/gpt-oss-120b` (30,000 TPM limit) and `qwen/qwen3.6-27b`.
     - Fast & High-Throughput: `openai/gpt-oss-20b` (30,000 TPM limit).
     - Multimodal Vision: `qwen/qwen3.6-27b` (Vision Mode).
   - **Backward-Compatible Alias Resolution**: Added automatic backend mapping in `MODEL_ALIASES` so any incoming or stored requests for `groq/compound`, `compound`, `groq/compound-mini`, `compound-mini`, `mixtral-8x7b-32768`, or `gemma2-9b-it` automatically route to `openai/gpt-oss-120b` or `openai/gpt-oss-20b`.
   - **Frontend UI & Load Balancer Updates**:
     - Updated dropdown selector in [`frontend/src/App.tsx`](file:///Users/abc/Desktop/Gen%20AI%20research%20tool/frontend/src/App.tsx) to list only active models.
     - Updated friendly model label mapping in [`frontend/src/components/UploadZone.tsx`](file:///Users/abc/Desktop/Gen%20AI%20research%20tool/frontend/src/components/UploadZone.tsx).
     - Synchronized documentation in [`ARCHITECTURE.md`](file:///Users/abc/Desktop/Gen%20AI%20research%20tool/ARCHITECTURE.md).

## Active State of Codebase Files
- [`frontend/src/components/MathRenderer.tsx`](file:///Users/abc/Desktop/Gen%20AI%20research%20tool/frontend/src/components/MathRenderer.tsx): Upgraded KaTeX & Markdown AST renderer with delimiter auto-healing, sentence extraction, and inline card mode.
- [`frontend/src/components/MindmapCanvas.tsx`](file:///Users/abc/Desktop/Gen%20AI%20research%20tool/frontend/src/components/MindmapCanvas.tsx): Node cards render titles via `MathRenderer`; interactive expansion controls.
- [`frontend/src/App.tsx`](file:///Users/abc/Desktop/Gen%20AI%20research%20tool/frontend/src/App.tsx): Document handling, topbar controls, active model selector (GPT-OSS 120B / Qwen 3.6 27B / GPT-OSS 20B).
- [`frontend/src/components/UploadZone.tsx`](file:///Users/abc/Desktop/Gen%20AI%20research%20tool/frontend/src/components/UploadZone.tsx): Upload drag-and-drop zone and model badge display formatting.
- [`backend/main.py`](file:///Users/abc/Desktop/Gen%20AI%20research%20tool/backend/main.py): Backend LaTeX validation, syllabus prompts, active load-balanced model pool (`openai/gpt-oss-120b`, `qwen/qwen3.6-27b`, `openai/gpt-oss-20b`), legacy alias fallbacks, and child node promotion.
- [`ARCHITECTURE.md`](file:///Users/abc/Desktop/Gen%20AI%20research%20tool/ARCHITECTURE.md): System architecture and model pool documentation.

## Verification
- Production TypeScript build (`npm run build`) completed successfully with 0 errors.
- Python backend syntax compilation (`py_compile`) passed cleanly.
