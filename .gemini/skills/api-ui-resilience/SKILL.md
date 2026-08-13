---
name: api-ui-resilience
description: Guidelines and best practices for building rate-limit resilient APIs, distributed LLM routing pools, stable JSON mode configurations, and graceful UI error/loading state recovery.
---

# Robust API & UI Resilience Guidelines

These guidelines document standard architectural, API, and UI design invariants that must be preserved to ensure high availability, robustness, and a polished user experience.

## 1. Input Validation & Error Boundaries
- **Strict File Checks**: File upload boundaries (drag-and-drop or file picker) must validate both MIME type (e.g. `application/pdf`) and file extension (e.g. `.pdf`) before initiating backend processing.
- **UI State Reset**: Async actions (such as upload, extraction, and generation) must wrap their promises in `try-catch-finally` or equivalent handlers that guarantee the UI state returns to `idle` upon failure. Never leave loading/spinner states active indefinitely.
- **Observability**: Display user-facing errors via the centralized Toast system and log detailed technical errors to the server console.

## 2. API Resilience & Rate Limit Mitigation
- **Exponential Backoff**: Concurrent API workers calling LLM gateways (such as Groq) must wrap requests in retry loops (at least 3 attempts) that implement exponential backoff with random jitter.
- **Retry-After Headers**: Intercept HTTP 429 errors and prioritize parsing the `Retry-After` header to set the exact sleep duration when retrying.
- **Distributed Chunk Routing**: When processing multi-part documents concurrently:
  - Leverage model pool distribution (e.g. rotating requests between compatible models) to multiply rate-limit capacity.
  - Return metadata about the exact models utilized in headers (such as `X-Model-Used`) so the frontend can notify the user of distributed processing.

## 3. LLM Compatibility & Model Pools
- **JSON Mode Constraints**: Restrict concurrent chunk distribution pools strictly to models known to produce clean JSON output. Avoid using reasoning models (e.g., models outputting `<think>` tags) in strict JSON-mode concurrent pools, as extra tags can trigger gateway schema errors or parser crashes.
- **Dynamic Chunk Sizing**: Automatically scale character chunk size down when using lightweight models with tight TPM (Tokens Per Minute) limits, or when distributed parallel routing is active.
- **Provider Model Identifier Validation**: Direct LLM provider APIs (e.g. Groq Cloud API) require exact native model identifiers (e.g. `llama-3.3-70b-versatile`). Do not pass vendor-prefixed strings (e.g. `meta-llama/`) to direct provider endpoints.
- **Backend Model Alias Mapping**: Implement a `MODEL_ALIASES` dictionary on backend LLM endpoints to automatically map unrecognized, legacy, or vendor-prefixed model names to active supported provider models, preventing `404 model_not_found` gateway errors.

## 4. Document Consolidation
- **No Hardcoded Placeholders**: When merging child mindmaps or documents, dynamically consolidate sub-chunk summaries (parsing and merging sections like "Core Concepts" and "Examples") rather than returning static placeholder text for the master root node.

## 5. Agent Subprocess Isolation & Security Invariants
- **Subprocess Crew Execution**: Run long-running AI agent crews (e.g. CrewAI, LangChain workflows) in dedicated backend subprocesses (`asyncio.create_subprocess_exec`) rather than inline threads/tasks. Stream output directly to disk logs (`outputs/logs/{job_id}.log`) for real-time tailing and status tracking.
- **Subprocess Cancellation**: Provide process termination handlers (`process.terminate()` followed by `process.kill()`) so active agent jobs can be stopped without impacting the parent web server.
- **Path Traversal Boundary Validation (CWE-23)**: When serving or deleting user-requested files from disk, always sanitize filenames with `os.path.basename()` and enforce strict directory boundary checks:
  ```python
  safe_name = os.path.basename(filename)
  filepath = os.path.abspath(os.path.join(ALLOWED_DIR, safe_name))
  if not filepath.startswith(os.path.abspath(ALLOWED_DIR)):
      raise HTTPException(status_code=400, detail="Invalid filename")
  ```
- **Safe DOM Heading Navigation (CWE-643)**: Avoid dynamic XPath evaluation (`document.evaluate`) with unescaped text in React components. Instead, query elements natively and filter by text:
  ```typescript
  const elements = Array.from(document.querySelectorAll(`.markdown-container h${level}`));
  const target = elements.find(el => el.textContent?.trim() === title.trim());
  ```

## 6. UI Auth State & UX Integrity Invariants
- **Auth Session Resilience**: Asynchronous auth listeners (`onAuthStateChange`, `getSession`) must NOT clear local authenticated user state when `session` is `null` (e.g. while email confirmation is pending or unconfirmed), as this triggers component unmounting and grey loading screen freezes.
- **No Unrequested UX Alterations**: When resolving a bug or feature request, fix the primary existing flow directly instead of adding unrequested alternative UI buttons or extra user flows.

## 7. Groq Free-Tier TPM & Candidate Fallback Chains
- **Dynamic Model Token Caps**:
  - 8B / Lightweight models on Groq Free Tier have strict 6,000 Tokens Per Minute (TPM) caps. Set `max_tokens: 1500` for 8B/small models to prevent `HTTP 413 Payload Too Large / TPM Limit Exceeded`.
  - 70B / Large models have high capacity (100,000 TPM). Set `max_tokens: 4096` for full deep-study coverage.
- **Candidate Fallback Chain**:
  - When processing API requests, build a candidate model order: `[selected_model] → [70B models] → [8B models]`.
  - If a model encounters HTTP 413 (TPM Cap) or HTTP 429 (Rate Limit), immediately switch to the next fallback candidate rather than retrying the same failing model.
- **Emergency Node Recovery**:
  - Synthesize a fallback section node if all model candidates fail, ensuring the document rendering never crashes with a 500 error.

## 8. Stack-Based JSON Auto-Repair for Truncated Completions
- **Auto-Repair Engine**:
  - If an LLM response is truncated near context boundaries, track unescaped double-quotes and structural bracket stacks (`{`, `[`).
  - Auto-close unclosed string literals and append missing structural brackets in reverse order before parsing with `json.loads`.
  - Fall back to label/summary string regex extraction if parsing fails, guaranteeing clean mindmap generation.

## 9. External Media API Sourcing (Wikimedia / Wikipedia)
- **MediaWiki User-Agent Specification**:
  - Requests to MediaWiki / Wikimedia Commons APIs MUST use compliant User-Agent headers: `AppName/Version (URL; ContactEmail)`.
- **Request Spacing**:
  - Space out media fetch queries with `await asyncio.sleep(0.5)` between nodes to prevent `HTTP 429 Too Many Requests` from Wikimedia API.
- **Local SSL Verification**:
  - Set `verify=False` on `httpx.AsyncClient` when fetching educational media in local macOS Python environments to prevent local SSL certificate chain errors.
