import os
import pathlib
import json
import logging
import re
import random
import base64
from typing import Optional, List
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Body, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import pypdf
import httpx
from dotenv import load_dotenv
# Explicitly load backend/.env relative to this file path
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
load_dotenv() # Fallback to CWD

import fitz  # PyMuPDF
import pytesseract
from PIL import Image
import io
import concurrent.futures
import hashlib
import asyncio

# Supabase Configuration
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")

def get_supabase_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }

# Password hashing and SMTP configurations removed as authentication is migrated to Supabase Auth.

try:
    from setup_tesseract import (
        get_tesseract_cmd,
        ensure_tesseract_installed,
        configure_tessdata_prefix,
        start_background_provisioning,
    )
except ImportError:
    try:
        from backend.setup_tesseract import (
            get_tesseract_cmd,
            ensure_tesseract_installed,
            configure_tessdata_prefix,
            start_background_provisioning,
        )
    except ImportError:
        def get_tesseract_cmd() -> Optional[str]:
            import shutil
            return shutil.which("tesseract")
        def ensure_tesseract_installed() -> bool:
            return bool(get_tesseract_cmd())
        def configure_tessdata_prefix() -> Optional[str]:
            return None
        def start_background_provisioning() -> None:
            pass

# Start background provisioning asynchronously so uvicorn binds to $PORT immediately
try:
    start_background_provisioning()
except Exception as _tess_err:
    logging.getLogger("pdf-to-mindmap-backend").warning(f"Initial Tesseract trigger note: {_tess_err}")

# Module-level worker function for parallel OCR processing
def ocr_image_bytes(img_data: bytes) -> str:
    import pytesseract
    from PIL import Image
    import io
    try:
        tess_bin = get_tesseract_cmd()
        if not tess_bin:
            ensure_tesseract_installed()
            tess_bin = get_tesseract_cmd()
        if tess_bin:
            pytesseract.pytesseract.tesseract_cmd = tess_bin
        image = Image.open(io.BytesIO(img_data))
        text = pytesseract.image_to_string(image, lang="eng", config="--psm 3")
        return text.strip()
    except Exception as e:
        return f"[OCR Error: {str(e)}]"

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pdf-to-mindmap-backend")

# Load environment variables (already loaded at import)

app = FastAPI(title="PDF-to-Mindmap Backend API")

# Enable CORS for the React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify frontend origin
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def rewrite_api_path(request: Request, call_next):
    path = request.scope["path"]
    # If Vercel stripped /api, prepend it back so FastAPI's routes match
    if not path.startswith("/api") and (
        path.startswith("/auth") or 
        path.startswith("/documents") or 
        path.startswith("/upload-pdf") or 
        path.startswith("/generate-mindmap") or 
        path == "/health"
    ):
        request.scope["path"] = "/api" + path
    response = await call_next(request)
    return response

def clean_extracted_text(text: str) -> str:
    """
    Cleans OCR output, joins hyphenated line breaks, removes page markers and excessive whitespace
    to maximize LLM prompt density and prevent noise in mindmap node generation.
    """
    if not text:
        return ""
    # Join hyphenated words split across lines (e.g. "gov- \n ernment" -> "government")
    text = re.sub(r'(\b\w+)-\s*\n\s*(\w+\b)', r'\1\2', text)
    # Remove repetitive page numbering patterns (e.g., "Page 1 of 10", "Page 2/15", "- 3 -")
    text = re.sub(r'(?i)\bpage\s+\d+\s*(?:of\s*\d+|/\s*\d+)?\b', '', text)
    text = re.sub(r'\n\s*[-—]\s*\d+\s*[-—]\s*\n', '\n', text)
    # Remove excessive unprintable/control OCR artifacts
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    # Normalize excessive spaces and blank lines
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def split_text_into_chunks(text: str, chunk_size: int = 14000, overlap: int = 1500) -> list[str]:
    """
    Splits long text using a contiguous overlapping sliding window to preserve context
    across section boundaries, guaranteeing zero text loss or skipped characters.
    """
    cleaned = clean_extracted_text(text)
    if not cleaned:
        return []
    if len(cleaned) <= chunk_size:
        return [cleaned]

    chunks = []
    start = 0

    while start < len(cleaned):
        end = min(start + chunk_size, len(cleaned))
        if end == len(cleaned):
            chunk_text = cleaned[start:].strip()
            if chunk_text:
                chunks.append(chunk_text)
            break

        # Seek logical paragraph, line, or sentence boundary near 'end'
        search_start = max(start + (chunk_size * 2 // 3), start + 1000)
        boundary = cleaned.rfind('\n\n', search_start, end)
        if boundary == -1:
            boundary = cleaned.rfind('\n', search_start, end)
        if boundary == -1:
            boundary = cleaned.rfind('. ', search_start, end)
        if boundary == -1:
            boundary = end

        chunk_text = cleaned[start:boundary].strip()
        if chunk_text:
            chunks.append(chunk_text)

        # Advance with overlap BEFORE boundary to ensure zero content is ever dropped
        next_start = max(0, boundary - overlap)
        if next_start <= start:
            next_start = boundary
        start = next_start

    return [c for c in chunks if c]

def make_ids_unique(node: dict, suffix: str) -> dict:
    # Suffix the node ID to prevent duplicate keys in React Flow
    if node.get("id") == "root":
        node["id"] = f"root_{suffix}"
    else:
        node["id"] = f"{node.get('id')}_{suffix}"
    
    # Recursively update children
    for child in node.get("children", []):
        make_ids_unique(child, suffix)
    return node

def consolidate_summaries(sub_maps: list[dict]) -> str:
    """
    Synthesizes a clean executive overview from multiple sub-mindmap sections
    without boilerplate placeholders.
    """
    key_points = []
    for idx, sub_map in enumerate(sub_maps):
        label = sub_map.get("label", f"Section {idx+1}")
        summary = sub_map.get("summary", "")
        # Extract main thesis, definition, or primary concept
        thesis_match = re.search(r"\*\*(?:Key Inquiry & Definition|Key Principle / Theme|Key Principle|Main Thesis|Mathematical Principle|Physical Principle|Historical Thesis|Geographical Thesis|Overview)\*\*:\s*(.*?)(?=\n-|\n###|$)", summary, re.DOTALL)
        if thesis_match:
            content = thesis_match.group(1).strip()
            key_points.append(f"- **{label}**: {content}")
        elif summary.strip():
            first_line = summary.strip().split('\n')[0].replace('#', '').strip()
            key_points.append(f"- **{label}**: {first_line}")

    if not key_points:
        return "### Core Concept & Overview\n- Comprehensive revision guide synthesizing all chapters and principles from the document."

    points_str = "\n".join(key_points)
    return (
        f"### Syllabus Overview\n{points_str}\n\n"
        f"### Document Structure\n- Comprehensive curriculum integrating all study modules into detailed child nodes below."
    )

class MindmapGenerateRequest(BaseModel):
    text: str
    model: Optional[str] = "openai/gpt-oss-120b"
    subject: Optional[str] = "general"


def clean_json_string(response_text: str) -> str:
    """
    Extracts and cleans a JSON block from the model's text response,
    handling <think> reasoning tags, unclosed code fences, bolded JSON keys,
    trailing commas, and preambles/postambles.
    """
    if not response_text:
        return ""
    s = response_text.strip()

    # 1. Strip reasoning <think>...</think> tags if present
    if "</think>" in s.lower():
        s = re.sub(r"<think>[\s\S]*?</think>", "", s, flags=re.IGNORECASE).strip()
    elif "<think>" in s.lower():
        first_brace = s.find('{')
        if first_brace != -1:
            s = s[first_brace:]
        else:
            s = ""
    
    # 2. Match standard markdown json fences with closing ```
    markdown_match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", s)
    if markdown_match:
        s = markdown_match.group(1).strip()
    else:
        # Strip leading markdown code fence ```json or ``` if unclosed
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*```$", "", s)
    
    # 3. Find the outermost JSON object starting from the first '{'
    start = s.find('{')
    if start != -1:
        s = s[start:]
        end = s.rfind('}')
        if end != -1:
            s = s[:end+1]

    # 4. Clean bold/italic markdown formatting around JSON keys
    s = re.sub(r'\*\*"([^"]+)"\*\*\s*:', r'"\1":', s)
    s = re.sub(r'"\*\*([^*"]+)\*\*"\s*:', r'"\1":', s)
    s = re.sub(r'\*\*([a-zA-Z0-9_]+)\*\*\s*:', r'"\1":', s)

    # 5. Remove trailing commas before closing braces/brackets
    s = re.sub(r',\s*([\]}])', r'\1', s)
            
    return s.strip()

def sanitize_json_latex(s: str) -> str:
    """
    Escapes unescaped LaTeX backslashes inside JSON strings so json.loads
    does not fail or corrupt LaTeX commands into ASCII control characters.
    """
    if not s:
        return s

    # 0. Replace raw ASCII control bytes that may have been parsed or injected
    s = s.replace('\x0c', r'\\f').replace('\x08', r'\\b').replace('\x0b', r'\\v')

    # 1. Fix LaTeX macros starting with standard JSON escape letters (b, f, n, r, t, u)
    latex_escaped_keywords = [
        # b
        "beta", "bar", "begin", "mathbf", "boldsymbol", "bmod", "binom", "bullet", "bmatrix", "bbox",
        # f
        "frac", "forall", "flat",
        # n
        "nabla", "neq", "nu", "notin", "norm", "not", "natural",
        # r
        "rho", "right", "rangle", "root", "rightarrow", "Rightarrow", "Re",
        # t
        "theta", "times", "tau", "text", "tan", "tanh", "to", "tilde", "tag", "triangle", "top", "textbf", "textit", "therefore",
        # u
        "upsilon", "underbrace", "underline", "uparrow", "Uparrow"
    ]
    for kw in latex_escaped_keywords:
        # Match single backslash followed by keyword
        s = re.sub(r"(?<!\\)\\" + kw + r"\b", r"\\\\" + kw, s)

    # 2. Escape any unescaped backslash before any letter not part of a valid JSON escape sequence (\" \\ \/ \b \f \n \r \t \u[0-9a-fA-F]{4})
    s = re.sub(r"(?<!\\)\\(?![\"\\/bfnrt]|u[0-9a-fA-F]{4})([a-zA-Z]+)", r"\\\\\1", s)

    # 3. Escape LaTeX symbol commands that are invalid JSON escapes: \, \; \! \{ \} \_ \^ \% \& \|
    s = re.sub(r"(?<!\\)\\([,;!#$%&~_^|(){}[\]])", r"\\\\\1", s)

    return s

def fix_inner_unescaped_quotes(s: str) -> str:
    """
    Finds string fields like "summary": "..." or "label": "..." and escapes
    any raw inner quotes that are not already escaped with \\.
    """
    def sanitize_field(match):
        key = match.group(1)
        val = match.group(2)
        escaped_val = re.sub(r'(?<!\\)"', r'\"', val)
        return f'{key}: "{escaped_val}"'

    s = re.sub(
        r'("summary"|"label")\s*:\s*"([\s\S]*?)"(?=\s*,\s*"\w+"|\s*,\s*\}|\s*\}\s*,\s*\{|\s*\]|\s*\})',
        sanitize_field,
        s
    )
    return s

def repair_and_parse_json(response_text: str) -> dict:
    """
    Cleans, repairs, and parses LLM JSON responses into a Python dict.
    If the response was truncated mid-sentence or mid-object, it auto-repairs
    unclosed strings, quotes, arrays, and braces, and extracts all child nodes
    so mindmap generation never crashes or drops child branches.
    """
    cleaned = clean_json_string(response_text)
    quote_fixed = fix_inner_unescaped_quotes(cleaned)
    sanitized = sanitize_json_latex(quote_fixed)
    
    # 1. Try direct parsing first
    try:
        data = json.loads(sanitized, strict=False)
        if isinstance(data, dict) and "id" in data and "label" in data and "children" in data:
            if isinstance(data["children"], list) and len(data["children"]) > 0:
                return data
            elif isinstance(data, dict) and data.get("label"):
                return data
    except Exception:
        pass

    # 2. Attempt JSON auto-repair for truncated output
    repaired = sanitized
    
    # Check if string ends inside a quoted literal by tracking unescaped quotes
    in_string = False
    escaped = False
    stack = []
    
    for char in repaired:
        if escaped:
            escaped = False
            continue
        if char == '\\':
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if not in_string:
            if char in '{[':
                stack.append(char)
            elif char == '}' and stack and stack[-1] == '{':
                stack.pop()
            elif char == ']' and stack and stack[-1] == '[':
                stack.pop()

    # If truncated inside a string literal, close the string quote
    if in_string:
        repaired += '"'

    # Close any unclosed objects or arrays in reverse order
    for char in reversed(stack):
        if char == '{':
            repaired += '}'
        elif char == '[':
            repaired += ']'

    try:
        data = json.loads(repaired, strict=False)
        if isinstance(data, dict):
            if "id" not in data: data["id"] = "root"
            if "children" not in data: data["children"] = []
            if isinstance(data["children"], list) and len(data["children"]) > 0:
                return data
            elif isinstance(data, dict) and data.get("label") and data.get("label").strip().lower() not in ["study module", "study topic", "document overview"]:
                return data
    except Exception as e:
        logger.warning(f"JSON auto-repair parsing warning: {str(e)}")

    # 3. Robust Flexible Regex Block Extractor (Extracts root node AND all child objects regardless of key order)
    root_label_match = re.search(r'"label"\s*:\s*"([^"]+)"', cleaned)
    root_summary_match = re.search(r'"summary"\s*:\s*"((?:[^"\\]|\\.)*)"', cleaned)

    extracted_children = []
    # Match any JSON object chunk containing at least "label" and "summary"
    obj_matches = re.finditer(r'\{([^{}]+)\}', cleaned)
    seen_ids = set()
    for m in obj_matches:
        block = m.group(1)
        id_m = re.search(r'"id"\s*:\s*"([^"]+)"', block)
        lbl_m = re.search(r'"label"\s*:\s*"([^"]+)"', block)
        sum_m = re.search(r'"summary"\s*:\s*"((?:[^"\\]|\\.)*)"', block)
        if lbl_m and sum_m:
            clabel = lbl_m.group(1).strip()
            cid = id_m.group(1) if id_m else f"node-{len(extracted_children)+1}"
            if cid == "root" or cid in seen_ids or not clabel or clabel.lower() in ["study module", "study topic", "document overview"]:
                continue
            seen_ids.add(cid)
            clean_sum = sum_m.group(1).replace('\\n', '\n').replace('\\"', '"').replace('\\\\', '\\')
            extracted_children.append({
                "id": cid,
                "label": clabel,
                "summary": clean_sum,
                "children": []
            })

    if root_label_match and root_label_match.group(1).strip().lower() not in ["study module", "study topic", "document overview"]:
        root_label = root_label_match.group(1).strip()
    elif extracted_children:
        root_label = extracted_children[0]["label"]
    else:
        return None

    if root_summary_match and len(root_summary_match.group(1).strip()) > 30:
        root_summary = root_summary_match.group(1).replace('\\n', '\n').replace('\\"', '"').replace('\\\\', '\\')
    elif extracted_children:
        root_summary = f"### Core Concept & Overview\n- **Key Principle**: In-depth syllabus module covering {len(extracted_children)} key concepts from the notes."
    else:
        return None

    return {
        "id": "root",
        "label": root_label,
        "summary": root_summary,
        "children": extracted_children
    }

def repair_math_syntax_backend(text: str) -> str:
    """
    Validates and repairs LaTeX math formatting, step markers, and stray mid-formula delimiters
    in mindmap summaries and labels on the backend before returning JSON to the client.
    """
    if not text:
        return text

    s = text

    # 0. Strip zero-width characters and invisible OCR artifacts
    s = re.sub(r'[\u200b\u200c\u200d\ufeff]', '', s)

    # 1. Normalize Unicode symbols & ASCII control characters
    s = s.replace('∗', '*')
    s = s.replace('−', '-')
    s = s.replace('\x0c', '\\f').replace('\x08', '\\b').replace('\x0b', '\\v')
    s = re.sub(r'\r(ight|ho|angle|oot|ightarrow|e)(?=[^a-zA-Z]|$)', r'\\r\1', s)
    s = s.replace('\r\n', '\n').replace('\r', '\n')
    s = s.replace('±', r'\pm ')
    s = s.replace('×', r'\times ')
    s = s.replace('÷', r'\div ')
    s = s.replace('≠', r'\neq ')
    
    # 2. Fix corrupted \left where \le was converted to ≤: ≤ft -> \left
    s = re.sub(r'\\?≤ft\b', r'\\left', s)
    s = re.sub(r'≤ft\(', r'\\left(', s)
    s = s.replace('≤', r'\le ')
    s = s.replace('≥', r'\ge ')
    s = s.replace('≈', r'\approx ')
    s = s.replace('→', r' $\to$ ')
    s = s.replace('⇒', r' $\implies$ ')
    s = s.replace('²', '^2').replace('³', '^3')

    # 3. Repair vertical multiline fractions inside parentheses (e.g. from vertical PDF extraction):
    # \left( \n y \n x \n \right) or ( \n 5 \n 25 \n )
    def fix_parens_fraction(m):
        top = m.group(1).strip()
        bot = m.group(2).strip()
        return f'\\left(\\frac{{{top}}}{{{bot}}}\\right)'

    s = re.sub(
        r'(?:\\left\s*\(|\()\s*([a-zA-Z0-9_+*-]+)\s*\n+\s*([a-zA-Z0-9_+*-]+)\s*(?:\\right\s*\)|\))',
        fix_parens_fraction,
        s
    )

    # 4. Invert inverted quotient fractions if matched with subtraction (symbolic):
    # \log_a \left(\frac{y}{x}\right) = \log_a x - \log_a y -> \frac{x}{y}
    pat_sym = r"\\log_\{?([a-zA-Z0-9]+)\}?\s*\\left\(\\frac\{([a-zA-Z0-9]+)\}\{([a-zA-Z0-9]+)\}\\right\)\s*=\s*\\log_\{?(?:[a-zA-Z0-9]+)\}?\s*([a-zA-Z0-9]+)\s*-\s*\\log_\{?(?:[a-zA-Z0-9]+)\}?\s*([a-zA-Z0-9]+)"
    s = re.sub(
        pat_sym,
        lambda m: f"\\log_{{{m.group(1)}}} \\left(\\frac{{{m.group(4)}}}{{{m.group(5)}}}\\right) = \\log_{{{m.group(1)}}} {m.group(4)} - \\log_{{{m.group(1)}}} {m.group(5)}",
        s
    )

    # 5. Collapse broken multiline log subscripts: "log \n 5" -> "\log_5"
    s = re.sub(r'(?<![\\a-zA-Z])\b(log|ln)\s*\n+\s*([0-9a-zA-Z]+)', r'\\\1_{\2}', s)
    s = re.sub(r'=\s*\\?log_?\{?(\w+)\}?\s*\n+\s*(\w+)', r'= \\log_{\1} \2', s)
    s = re.sub(r'-\s*\\?log_?\{?(\w+)\}?\s*\n+\s*(\w+)', r'- \\log_{\1} \2', s)
    s = re.sub(r'(?<![\\a-zA-Z])\b(log|ln)\s+(\d+)\s+(\d+)\b', r'\\\1_{\2} \3', s)

    # 6. Ensure math commands have backslashes outside math when followed by subscript, parens, or number
    s = re.sub(r'(?<![a-zA-Z\\])\b(log|ln|sin|cos|tan|cot|sec|csc)\b(?=[_(\d]|\s+\d)', r'\\\1', s)

    # 7. Clean multiline breaks between math tokens
    s = re.sub(r'(\\log_[a-zA-Z0-9{}]+)\s*\n+\s*(\\left|\()', lambda m: f"{m.group(1)} {m.group(2)}", s)
    s = re.sub(r'(\\right\)?|\))\s*\n*=\s*\n*', r'\1 = ', s)
    s = re.sub(r'=\s*\n*(\\log_[a-zA-Z0-9{}]+)', r'= \1', s)
    s = re.sub(r'-\s*\n*(\\log_[a-zA-Z0-9{}]+)', r'- \1', s)
    s = re.sub(r'([0-9a-zA-Z])\s*-\s*(\\log_)', r'\1 - \2', s)

    # 7b. Invert numerical inverted fractions now that log subscripts and operators are normalized:
    # \log_5 \left(\frac{5}{25}\right) = \log_5 25 - \log_5 5 -> \frac{25}{5}
    pat_num = r"\\log_\{?([a-zA-Z0-9]+)\}?\s*\\left\(\\frac\{(\d+)\}\{(\d+)\}\\right\)\s*=\s*\\log_\{?(?:[a-zA-Z0-9]+)\}?\s*(\d+)\s*-\s*\\log_\{?(?:[a-zA-Z0-9]+)\}?\s*(\d+)"
    s = re.sub(
        pat_num,
        lambda m: f"\\log_{{{m.group(1)}}} \\left(\\frac{{{m.group(4)}}}{{{m.group(5)}}}\\right) = \\log_{{{m.group(1)}}} {m.group(4)} - \\log_{{{m.group(1)}}} {m.group(5)}",
        s
    )

    # 8. Clean step prefixes like {2}:$$ -> \n- **Step 2**: $$
    s = re.sub(r'(?:^|\n)\s*[\{\[\(](\d+)[\}\]\)]\s*:\s*', r'\n- **Step \1**: ', s)

    # 9. Fix squeezed headers e.g. -**GoverningIdentity**: -> - **Governing Identity**:
    s = re.sub(r'(\*{2})([A-Z][a-z]+)([A-Z][a-z]+)(\*{2})', r'\1\2 \3\4', s)
    s = re.sub(r'^[−-]?\s*(\*\*[^*]+\*\*)\s*:\s*', r'- \1: ', s, flags=re.MULTILINE)
    s = re.sub(r'^[−-]?\s*(?:\*\*)?(Governing Identity|Governing Formula|Problem Walkthrough|Calculation Walkthrough|Key Principle|Step-by-Step Method|Exam Pitfalls & Conditions)(?:\*\*)?\s*:\s*', r'- **\1**: ', s, flags=re.MULTILINE)

    # 10. Heal leading LaTeX command immediately outside inline math: e.g. \Delta$(k)>0$ -> $\Delta(k)>0$
    s = re.sub(r'(\\[a-zA-Z]+)\s*\$([^$]+)\$', r'$\1 \2$', s)
    s = re.sub(r'\$(\\[a-zA-Z]+)\s+([(\[{])', r'$\1\2', s)

    # 11. Heal standalone LaTeX command outside $ followed by operators or arguments:
    s = re.sub(r'(?<!\$|\\)(\\Delta|\\alpha|\\beta|\\gamma|\\theta|\\pi|\\sigma|\\lambda|\\mu|\\omega)(?:\(([a-zA-Z0-9_,+-]+)\))?\s*([><=≠≤≥≈])\s*([a-zA-Z0-9_+-]+|\\[a-zA-Z]+)(?!\$)',
               r'$\1\2 \3 \4$', s)

    # 12. Heal trailing argument or operator outside closing $:
    s = re.sub(r'\$([^$]+)\$\s*(\([a-zA-Z0-9_,+-]+\))(?!\$)', r'$\1\2$', s)
    s = re.sub(r'\$([^$]+)\$\s*([><=≠≤≥≈])\s*([a-zA-Z0-9_+-]+|\\[a-zA-Z]+)(?!\$)', r'$\1 \2 \3$', s)

    # 13. Heal adjacent or split math blocks: e.g. $\Delta$$(k)>0$ -> $\Delta(k)>0$
    s = re.sub(r'\$([^$]+)\$\s*\$([^$]+)\$', r'$\1 \2$', s)

    # 14. Repair stray mid-formula closing $$ before an exponent:
    s = re.sub(r'\\frac\{([^{}]+)\}\{([^{}]+)\}\$\$[\^](\d+|\{[^{}]+\})', r'\\frac{\1}{\2}\\bigr)^\3', s)
    s = re.sub(r'([a-zA-Z0-9)\]])\$\$[\^](\d+|\{[^{}]+\})', r'\1^\2', s)

    # 15. Repair broken completing the square clauses: "a[x 2 + a/b x]" -> "a\left[x^2 + \frac{b}{a}x\right]"
    s = re.sub(r'a\[x\s*2\s*\+\s*([ab])\/([ab])\s*x\]', r'a\\left[x^2 + \\frac{b}{a}x\\right]', s)
    s = re.sub(r'ax\+\\frac\{b\}\{2a\}\s*\\bigr\)\^2', r'a\\left(x + \\frac{b}{2a}\\right)^2', s)
    s = re.sub(r'ax\+\\frac\{b\}\{2a\}\s*\^2', r'a\\left(x + \\frac{b}{2a}\\right)^2', s)
    s = re.sub(r'a\[x\+\\frac\{b\}\{2a\}\s*\\bigr\)\^2', r'a\\left[\\left(x + \\frac{b}{2a}\\right)^2', s)

    # 16. Fix unparenthesized linear+fraction before exponent: x+\frac{b}{2a}^2 -> \left(x+\frac{b}{2a}\right)^2
    s = re.sub(r'((?:[a-zA-Z0-9]|\\[a-zA-Z]+)\s*[+-]\s*\\frac\{[^{}]*\}\{[^{}]*\})\s*\^(\d+|\{[^{}]*\})', r'\\left(\1\\right)^\2', s)

    # 17. Repair truncated/unclosed fraction in conjugate rationalization:
    s = re.sub(r'For\s+p\s*\+\s*q\s*A\s*,?\s*multiply\s+by\s*(?:\\frac\{)?(?:\\sqrt\{p\})?\$?',
               r'For $\\frac{A}{\\sqrt{p} + \\sqrt{q}}$, multiply numerator and denominator by $\\frac{\\sqrt{p} - \\sqrt{q}}{\\sqrt{p} - \\sqrt{q}}$',
               s, flags=re.IGNORECASE)
    s = re.sub(r'\\frac\{([^{}]+)\}\$', r'\\frac{\1}{\\sqrt{p} - \\sqrt{q}}$', s)

    # 18. Repair sizing macros missing opening or closing parentheses
    s = re.sub(r'\\bigl([a-zA-Z0-9])', r'\\bigl(\1', s)
    s = re.sub(r'\\bigr(?=[^)\\]|$)', r'\\bigr)', s)

    # 19. Wrap unwrapped Governing Identity formulas in $$ ... $$
    def wrap_identity(m):
        prefix = m.group(1)
        formula = m.group(2).strip()
        if not formula.startswith('$'):
            formula = f'$${formula}$$'
        return f'{prefix}{formula}'
    s = re.sub(r'(- \*\*Governing (?:Identity|Formula)\*\*:\s*)([^\n$]+)', wrap_identity, s)

    # 20. Wrap inline equations in Worked Exam Example walkthroughs if unwrapped:
    def wrap_walkthrough(m):
        prefix = m.group(1)
        content = m.group(2).strip()
        if '=' in content and '$' not in content:
            lead_match = re.match(r'^(.*?(?:Expand|Evaluate|Solve|Simplify|Calculate|For)\s+)(.+)$', content, re.IGNORECASE)
            if lead_match:
                lead = lead_match.group(1)
                eq = lead_match.group(2).rstrip('.')
                dot = '.' if content.endswith('.') else ''
                return f'{prefix}{lead}${eq}${dot}'
            else:
                return f'{prefix}${content}$'
        return m.group(0)

    s = re.sub(r'(- \*\*(?:Problem|Calculation) Walkthrough\*\*:\s*)([^\n]+)', wrap_walkthrough, s)

    # 21. Clean excessive blank lines
    s = re.sub(r'\n{3,}', '\n\n', s)

    return s

def sanitize_node_label(label: str) -> str:
    """
    Cleans up redundant chapter/section prefixes to prevent double labels.
    """
    if not label:
        return label
    s = label.strip()
    # 1. Clean repetitive chapter prefixes: "Chapter 1: Chapter 1: Foo" -> "Chapter 1: Foo"
    s = re.sub(r'^(Chapter\s+\d+)\s*[:\-–—]\s*\1\s*[:\-–—]\s*', r'\1: ', s, flags=re.IGNORECASE)
    # 2. Clean repetitive section prefixes: "1.1: 1.1 Foo" -> "1.1 Foo"
    s = re.sub(r'^(\d+\.\d+)\s*[:\-–—]?\s*\1\s*[:\-–—]?\s*', r'\1 ', s)
    # 3. Clean repetitive Issue prefixes: "Issue 1: Issue 1: Foo" -> "Issue 1: Foo"
    s = re.sub(r'^(Issue\s+\d+)\s*[:\-–—]\s*\1\s*[:\-–—]\s*', r'\1: ', s, flags=re.IGNORECASE)
    return s.strip()

def ensure_chapter_numbering(root: dict) -> dict:
    """
    Deterministically ensures top-level child nodes have 'Chapter X: ' or 'Issue X: Chapter Y: ' prefixes
    and child sub-nodes have 'X.Y ' section prefixes, preserving existing chapter/issue numbers if present.
    """
    if not isinstance(root, dict):
        return root

    children = root.get("children", [])
    if not children or not isinstance(children, list):
        return root

    for ch_idx, ch_node in enumerate(children):
        if not isinstance(ch_node, dict):
            continue
        
        label = ch_node.get("label", "").strip()
        has_chapter_prefix = bool(re.match(r'^(Chapter\s+\d+|Issue\s+\d+|Unit\s+\d+|Theme\s+\d+)\b', label, re.IGNORECASE))
        
        ch_num = ch_idx + 1
        num_match = re.search(r'^(?:Chapter|Issue|Unit|Theme)\s+(\d+)', label, re.IGNORECASE)
        if num_match:
            ch_num = int(num_match.group(1))
        elif not has_chapter_prefix:
            label = f"Chapter {ch_num}: {label}"
            ch_node["label"] = label
        
        # Process sub-children for section numbering "X.Y "
        sub_children = ch_node.get("children", [])
        if isinstance(sub_children, list):
            for sec_idx, sec_node in enumerate(sub_children):
                if not isinstance(sec_node, dict):
                    continue
                sec_label = sec_node.get("label", "").strip()
                has_sec_num = bool(re.match(r'^\d+\.\d+\b', sec_label))
                if not has_sec_num:
                    sec_label = f"{ch_num}.{sec_idx+1} {sec_label}"
                    sec_node["label"] = sec_label

    return root

def sanitize_mindmap_math(node: dict) -> dict:
    """
    Recursively validates and repairs LaTeX syntax and node labels across all nodes in the mindmap tree.
    """
    if not isinstance(node, dict):
        return node

    if "label" in node and isinstance(node["label"], str):
        cleaned_label = sanitize_node_label(node["label"])
        node["label"] = repair_math_syntax_backend(cleaned_label)

    if "summary" in node and isinstance(node["summary"], str):
        node["summary"] = repair_math_syntax_backend(node["summary"])

    if "children" in node and isinstance(node["children"], list):
        for idx, child in enumerate(node["children"]):
            node["children"][idx] = sanitize_mindmap_math(child)

    return node

# Wikimedia Commons image fetch service

async def fetch_wikimedia_image(query: str) -> Optional[dict]:
    """
    Queries Wikimedia Commons API for open-access educational images matching the query.
    Returns dict with imageUrl, imageCaption, imageAspectRatio, or None.
    """
    if not query or len(query.strip()) < 3:
        return None

    clean_query = re.sub(r'^(?:Part\s*\d+:?|[0-9]+\.|\d+\))\s*', '', query, flags=re.IGNORECASE).strip()
    if not clean_query or clean_query.lower() in ["root", "central topic", "section summary", "overview"]:
        return None

    search_url = "https://commons.wikimedia.org/w/api.php"
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": f"{clean_query} filetype:bitmap|drawing",
        "gsrnamespace": "6",
        "gsrlimit": "8",
        "prop": "imageinfo",
        "iiprop": "url|size|extmetadata",
        "format": "json"
    }

    # MediaWiki compliant User-Agent format
    headers = {
        "User-Agent": "MindmapStudyTool/2.0 (https://github.com/Arjundevjha/pdf-to-mindmap; dev@example.com)"
    }

    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.get(search_url, params=params, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                pages = data.get("query", {}).get("pages", {})
                for _, page_info in pages.items():
                    image_info_list = page_info.get("imageinfo", [])
                    if not image_info_list:
                        continue

                    info = image_info_list[0]
                    url = info.get("url", "")
                    width = info.get("width", 0)
                    height = info.get("height", 0)

                    # Skip tiny icons / logos / non-image media (<120px)
                    if not url or width < 120 or height < 100:
                        continue

                    # Exclude non-browser media formats (.webm, .ogv, .tif) and common meta icons
                    url_lower = url.lower()
                    if any(bad in url_lower for bad in ['.webm', '.ogv', '.ogg', '.tif', '.tiff', 'commons-logo', 'symbol', 'flag', 'icon', 'button']):
                        continue

                    extmeta = info.get("extmetadata", {})
                    caption_obj = extmeta.get("ObjectName", {}) or extmeta.get("ImageDescription", {})
                    caption = caption_obj.get("value", clean_query) if isinstance(caption_obj, dict) else clean_query

                    # Clean up HTML tags in Wikimedia captions
                    caption_clean = re.sub(r'<[^>]+>', '', str(caption)).strip()
                    if len(caption_clean) > 80:
                        caption_clean = caption_clean[:77] + "..."

                    aspect_ratio = round(width / height, 2) if height > 0 else 1.33

                    return {
                        "imageUrl": url,
                        "imageCaption": caption_clean or clean_query,
                        "imageAspectRatio": aspect_ratio
                    }
    except Exception as e:
        logger.warning(f"Wikimedia API fetch warning for query '{query}': {str(e)}")

ENABLE_WIKIMEDIA_IMAGES = os.environ.get("ENABLE_WIKIMEDIA_IMAGES", "false").lower() in ("true", "1")

async def enrich_mindmap_with_images(node: dict, max_images: int = 8, count: int = 0) -> int:
    """
    Attaches educational images only if explicitly enabled via ENABLE_WIKIMEDIA_IMAGES=true.
    Keeps default mindmaps clean, focused, and distraction-free.
    """
    if not ENABLE_WIKIMEDIA_IMAGES or count >= max_images:
        return count

    label = node.get("label", "")
    should_enrich = (node.get("id") == "root" or len(node.get("children", [])) > 0 or random.random() < 0.5)

    if should_enrich and label and not node.get("imageUrl"):
        image_data = await fetch_wikimedia_image(label)
        if image_data:
            node["imageUrl"] = image_data["imageUrl"]
            node["imageCaption"] = image_data["imageCaption"]
            node["imageAspectRatio"] = image_data["imageAspectRatio"]
            count += 1
            await asyncio.sleep(0.5)

    for child in node.get("children", []):
        if count >= max_images:
            break
        count = await enrich_mindmap_with_images(child, max_images=max_images, count=count)

    return count



def detect_subject_from_text(text: str) -> str:
    """
    Intelligently auto-detects academic discipline from text content
    when subject is set to 'general' or auto-detect.
    """
    lower = text[:12000].lower()

    # Humanities / Social Studies / Governance / SRQ
    humanities_keywords = [
        "citizenship", "governance", "srq", "peel", "civics", "society", 
        "singapore", "constitution", "meritocracy", "skillsfuture", "cmio", 
        "assimilation", "integration", "national service", "parliament", 
        "public policy", "treaty", "democracy", "healthcare system", "medisave",
        "central provident fund", "cpf", "trade-off", "stake in society",
        "shared values", "ethnic integration", "hdb"
    ]
    if sum(1 for kw in humanities_keywords if kw in lower) >= 2:
        return "humanities"

    # Mathematics
    math_keywords = [
        "quadratic", "discriminant", "surd", "conjugate", "polynomial", 
        "binomial", "derivative", "integral", "trigonometry", "differentiation",
        "completing the square", "roots of equation", "partial fraction"
    ]
    if sum(1 for kw in math_keywords if kw in lower) >= 2:
        return "math"

    # Physics
    physics_keywords = [
        "kinematics", "velocity", "acceleration", "newton", "gravitational",
        "kinetic energy", "potential energy", "resistor", "voltage", "current",
        "electromagnetism", "thermal physics", "m/s", "joule", "watt"
    ]
    if sum(1 for kw in physics_keywords if kw in lower) >= 2:
        return "physics"

    # History
    history_keywords = [
        "treaty of versailles", "world war", "cold war", "authoritarian", 
        "hitler", "stalin", "mussolini", "league of nations", "ussr", "axis powers"
    ]
    if sum(1 for kw in history_keywords if kw in lower) >= 2:
        return "history"

    # Geography
    geography_keywords = [
        "plate tectonics", "lithosphere", "subduction", "volcano", "earthquake",
        "monsoon", "drainage basin", "coastal erosion", "weather and climate"
    ]
    if sum(1 for kw in geography_keywords if kw in lower) >= 2:
        return "geography"

    return "general"

# Subject-specific system prompts tailored for Secondary / O-Level Revision Notes
def get_system_prompt(subject: str) -> str:
    if subject == "math":
        return """You are a master Mathematics tutor and curriculum specialist.
Your objective is to analyze the student revision notes and transform them into an exhaustive, exam-focused, highly structured hierarchical mindmap with clear chapter and section numbering.

CRITICAL TOPOLOGY, CHAPTER NUMBERING & LABEL RULES:
1. SPECIFIC ROOT TOPIC NAME:
   - The root node "label" MUST BE the overarching mathematical subject/topic (e.g. "Algebraic Foundations & Quadratic Functions", "Exponential & Logarithmic Functions", "Trigonometry & Circular Measure").
   - NEVER write "O-Level", "Document Overview", "Study Guide", or generic headings in the root label.
2. CHAPTER & SECTION NUMBERING IN LABELS:
   - Top-Level Child Nodes: MUST be formatted with chapter numbering (e.g., "Chapter 1: Quadratic Functions & Completing the Square", "Chapter 2: The Discriminant & Nature of Roots", "Chapter 3: Surds & Conjugate Rationalization"). If the text has explicit chapter/topic numbers, preserve them; if unnumbered, assign sequential "Chapter 1", "Chapter 2", etc.
   - Sub-Child Nodes: MUST be formatted with hierarchical section numbering matching the parent (e.g., "1.1 Converting to Vertex Form", "1.2 Maximum/Minimum Turning Points", "2.1 Real and Distinct Roots Condition", "2.2 Tangent and Intersection Rules").
   - Clean Titles: Do not repeat prefixes (never write "Chapter 1: Chapter 1:").
3. MANDATORY MULTI-NODE HIERARCHY:
   - The root node MUST ONLY contain the topic title and a concise 2-sentence syllabus overview.
   - The root node MUST HAVE 4 to 8 distinct child nodes in its "children" array, one for EACH topic/chapter.
   - Each major child node SHOULD contain 2 to 4 sub-child nodes in its own "children" array for specific formulas, proofs, or worked techniques.
4. STRICT DEPTH & COMPLETENESS INVARIANT:
   - Every node MUST be exhaustive, rigorous, and fully detailed with complete intermediate algebraic steps and conditions.
5. LaTeX Formula Standard (MANDATORY DELIMITERS & PURITY):
   - Every formula, equation, variable, and operator MUST be wrapped in standard dollar-sign LaTeX delimiters ($...$ inline, $$...$$ block display).
   - STRICT DELIMITER PURITY: NEVER put English text inside '$$ ... $$' or '$ ... $'.
   - ZERO CORRUPTED OCR ARTIFACTS: NEVER output corrupted symbols or OCR misreads such as '≤ft(' (always use '\\left(') or split lines inside formulas.
   - FRACTION RECONSTRUCTION: If source notes contain vertical multiline fraction text (e.g. separate lines for numerator and denominator inside parentheses), reconstruct them into standard LaTeX fractions '\\left(\\frac{numerator}{denominator}\\right)'.
   - LOGARITHM LAWS: Quotient law must always preserve subtraction order: \\log_a \\left(\\frac{x}{y}\\right) = \\log_a x - \\log_a y.
   - GOVERNING IDENTITY: Must ALWAYS be on its own line preceded by '- **Governing Identity**: $$[LaTeX Formula]$$'.
   - WORKED EXAMPLES: In Problem Walkthrough, all mathematical expressions, substitutions, and equalities MUST be wrapped in '$...$'.
6. Summary Structure (Use rich multi-bullet markdown format for EVERY node):
   ### Core Concept & Exam Rule
   - **Key Principle**: [Clear intuition of the rule or formula for exams]
   - **Step-by-Step Method**: [Step-by-step algebraic technique with LaTeX $...$ notation]
   - **Exam Pitfalls & Conditions**: [Sign traps, discriminant conditions $\\Delta < 0$, domain restrictions]

   ### Formulas & Identities
   - **Governing Identity**: $$[Block LaTeX Formula]$$
   - **Variable Definitions**: [Symbols, coefficients, constants, and domain]

   ### Worked Exam Example
   - **Problem Walkthrough**: [Concrete numerical problem with step-by-step substitution and solution, e.g. Expand $\\log_{5} \\left(\\frac{25}{5}\\right) = \\log_{5} 25 - \\log_{5} 5 = 2 - 1 = 1$.]

JSON OUTPUT SCHEMA:
Output ONLY a single valid JSON object strictly matching this multi-level hierarchy:
{
  "id": "root",
  "label": "Algebraic Foundations & Quadratic Functions",
  "summary": "### Core Concept & Exam Rule\\n- **Key Principle**: Comprehensive syllabus overview covering quadratic equations, surds, polynomials, partial fractions, and binomial expansions.\\n- **Step-by-Step Method**: Master canonical transformations from standard forms to algebraic solutions.",
  "children": [
    {
      "id": "node-1",
      "label": "Chapter 1: Quadratic Functions & Completing the Square",
      "summary": "### Core Concept & Exam Rule\\n- **Key Principle**: Converting $y = ax^2 + bx + c$ to vertex form $y = a(x-h)^2 + k$ identifies the maximum/minimum turning point $\\\\bigl(-\\\\frac{b}{2a}, c - \\\\frac{b^2}{4a}\\\\bigr)$.\\n- **Step-by-Step Method**: Factor leading coefficient $a$ from $x^2$ and $x$ terms, then add and subtract $\\\\bigl(\\\\frac{b}{2a}\\\\bigr)^2$: $ax^2 + bx + c = a\\\\left(x + \\\\frac{b}{2a}\\\\right)^2 + \\\\left(c - \\\\frac{b^2}{4a}\\\\right)$.\\n- **Exam Pitfalls & Conditions**: If $a > 0$, parabola opens upwards (minimum); if $a < 0$, parabola opens downwards (maximum).\\n\\n### Formulas & Identities\\n- **Governing Identity**: $$y = a\\\\left(x + \\\\frac{b}{2a}\\\\right)^2 + \\\\left(c - \\\\frac{b^2}{4a}\\\\right)$$\\n\\n### Worked Exam Example\\n- **Problem Walkthrough**: For $y = 2x^2 - 8x + 3 = 2(x^2 - 4x) + 3 = 2(x-2)^2 - 8 + 3 = 2(x-2)^2 - 5$, minimum point is $(2, -5)$.",
      "children": [
        {
          "id": "node-1-1",
          "label": "1.1 The Discriminant & Nature of Roots",
          "summary": "### Core Concept & Exam Rule\\n- **Key Principle**: The discriminant $\\\\Delta = b^2 - 4ac$ determines the number and type of real intersections with the x-axis.\\n\\n### Formulas & Identities\\n- **Governing Identity**: $$\\Delta = b^2 - 4ac$$\\n- **Variable Definitions**: $\\\\Delta > 0 \\\\implies$ two distinct real roots; $\\\\Delta = 0 \\\\implies$ two equal real roots (tangent to axis); $\\\\Delta < 0 \\\\implies$ no real roots (curve lies entirely above or below x-axis).",
          "children": []
        }
      ]
    },
    {
      "id": "node-2",
      "label": "Chapter 2: Surds & Conjugate Rationalization",
      "summary": "### Core Concept & Exam Rule\\n- **Key Principle**: To rationalize a denominator of the form $\\\\sqrt{p} + \\\\sqrt{q}$, multiply both numerator and denominator by the conjugate $\\\\sqrt{p} - \\\\sqrt{q}$.\\n- **Step-by-Step Method**: Use difference of squares $(\\\\sqrt{p} + \\\\sqrt{q})(\\\\sqrt{p} - \\\\sqrt{q}) = p - q$.\\n\\n### Formulas & Identities\\n- **Governing Identity**: $$\\frac{A}{\\\\sqrt{p} + \\\\sqrt{q}} \\\\times \\\\frac{\\\\sqrt{p} - \\\\sqrt{q}}{\\\\sqrt{p} - \\\\sqrt{q}} = \\\\frac{A(\\\\sqrt{p} - \\\\sqrt{q})}{p - q}$$",
      "children": []
    }
  ]
}"""

    elif subject == "physics":
        return """You are a master Physics tutor and exam specialist.
Your objective is to analyze student physics notes and transform them into an exhaustive, exam-focused hierarchical mindmap with clear chapter and section numbering.

CRITICAL TOPOLOGY, CHAPTER NUMBERING & LABEL RULES:
1. SPECIFIC ROOT TOPIC NAME: The root node "label" MUST BE the specific physics theme (e.g. "Kinematics & Newtonian Mechanics", "Thermal Physics & Heat Transfer", "Current Electricity & DC Circuits").
2. CHAPTER & SECTION NUMBERING IN LABELS:
   - Top-Level Child Nodes: MUST be formatted with chapter numbering (e.g., "Chapter 1: Physical Quantities & Measurement", "Chapter 2: Kinematics & Motion Graphs", "Chapter 3: Dynamics & Newton's Laws"). Preserve explicit numbers from text; if unnumbered, assign sequential "Chapter 1", "Chapter 2", etc.
   - Sub-Child Nodes: MUST be formatted with hierarchical section numbering (e.g., "2.1 Velocity-Time Graphs & Acceleration", "2.2 Equations of Uniformly Accelerated Motion").
3. MANDATORY MULTI-NODE HIERARCHY:
   - Root node MUST contain 4 to 8 distinct child nodes in its "children" array, one for EACH chapter or mechanism.
4. LaTeX Equations with SI Units: Every physical law and formula MUST use standard LaTeX ($...$ inline and $$...$$ block) with SI units ($m/s^2$, $N$, $J$, $W$, $V$, $\\Omega$).
5. Summary Structure:
   ### Core Concept & Physical Law
   - **Key Definition**: [Concise, exam-accurate definition of the law or concept]
   - **Physical Mechanism**: [Force interactions, energy transfers, or field properties]
   - **Exam Pitfalls & Sign Conventions**: [Direction conventions, scalar vs vector distinctions]

   ### Equations & Units
   - **Governing Formula**: $$[Block LaTeX Formula]$$
   - **Variable Definitions & SI Units**: [Symbols, physical constants ($g = 9.81\\text{ m/s}^2$), and explicit SI units]

   ### Worked Exam Problem
   - **Calculation Walkthrough**: [Concrete numerical calculation showing step-by-step substitution and final answer with units]

JSON OUTPUT SCHEMA:
Output ONLY a single valid JSON object strictly matching this multi-level hierarchy:
{
  "id": "root",
  "label": "Kinematics & Newtonian Mechanics",
  "summary": "### Core Concept & Physical Law\\n- **Key Definition**: Comprehensive revision guide for kinematics, dynamics, energy, and work.\\n- **Physical Mechanism**: Gravitational and contact force interactions governing motion.",
  "children": [
    {
      "id": "node-1",
      "label": "Chapter 1: Kinematics & Motion Graphs",
      "summary": "### Core Concept & Physical Law\\n- **Key Definition**: Kinematics describes motion without considering the forces causing it.\\n- **Physical Mechanism**: Velocity is rate of change of displacement; acceleration is rate of change of velocity.\\n- **Exam Pitfalls & Sign Conventions**: Gradient of displacement-time graph gives velocity; area under velocity-time graph gives displacement.\\n\\n### Equations & Units\\n- **Governing Formula**: $$v = u + at, \\\\quad s = ut + \\\\frac{1}{2}at^2, \\\\quad v^2 = u^2 + 2as$$\\n\\n### Worked Exam Problem\\n- **Calculation Walkthrough**: A car accelerates from rest ($u = 0$) at $a = 3\\\\text{ m/s}^2$ for $t = 4\\\\text{ s}$. Final velocity $v = 0 + (3)(4) = 12\\\\text{ m/s}$.",
      "children": [
        {
          "id": "node-1-1",
          "label": "1.1 Graphical Analysis of Motion",
          "summary": "### Core Concept & Physical Law\\n- **Key Definition**: Techniques for deriving kinematic quantities from displacement-time and velocity-time curves.",
          "children": []
        }
      ]
    }
  ]
}"""

    elif subject == "history":
        return """You are a master History tutor.
Your objective is to analyze historical revision notes and map out causal chronologies, key turning points, and exam takeaways with clear chapter and section numbering.

CRITICAL TOPOLOGY, CHAPTER NUMBERING & LABEL RULES:
1. SPECIFIC ROOT TOPIC NAME: The root node "label" MUST BE the specific historical period or unit (e.g. "Causes of World War I & The Alliance System", "The Rise of Authoritarian Regimes").
2. CHAPTER & SECTION NUMBERING:
   - Top-Level Child Nodes: Format with chapter/unit numbering (e.g., "Chapter 1: Outbreak & Causes of World War I", "Chapter 2: The Paris Peace Conference & Treaties", "Chapter 3: The League of Nations"). Preserve source numbers or assign sequential "Chapter 1", "Chapter 2", etc.
   - Sub-Child Nodes: Format with sub-section numbering (e.g., "1.1 The Militarism & Alliance System", "1.2 The Assassination in Sarajevo & July Crisis").
3. Summary Structure:
   ### Core Historical Event & Context
   - **Key Event / Overview**: [Concise exam-focused summary of what occurred]
   - **Causal Factor**: [Root causes and triggers]

   ### Key Details & Turning Points
   - **Key Turning Point & Year**: [Year + decisive event + outcome]
   - **Key Figures & Factions**: [Motivations, actions, and policies]

   ### Historical Impact & Exam Significance
   - **Long-Term Impact**: [Exam significance and historical consequences]

JSON OUTPUT SCHEMA:
Output ONLY a single valid JSON object strictly matching this schema:
{
  "id": "root",
  "label": "Causes of World War I & The Alliance System",
  "summary": "### Core Historical Event & Context\\n- **Key Event / Overview**: Comprehensive syllabus revision guide covering core historical developments and causal timelines.",
  "children": [
    {
      "id": "node-1",
      "label": "Chapter 1: Outbreak & Systemic Causes of Conflict",
      "summary": "### Core Historical Event & Context\\n- **Key Event / Overview**: Systemic alliances and geopolitical tensions leading to mobilization.\\n\\n### Key Details & Turning Points\\n- **Key Turning Point & Year**: 1914 Assassination of Archduke Franz Ferdinand.",
      "children": [
        {
          "id": "node-1-1",
          "label": "1.1 The European Alliance Framework",
          "summary": "### Core Historical Event & Context\\n- **Key Event / Overview**: The Triple Entente and Triple Alliance creating rigid mutual defense obligations.",
          "children": []
        }
      ]
    }
  ]
}"""

    elif subject == "geography":
        return """You are a master Geography tutor.
Your objective is to analyze geographical revision notes and map out physical processes, landforms, and case studies with clear chapter and section numbering.

CRITICAL TOPOLOGY, CHAPTER NUMBERING & LABEL RULES:
1. SPECIFIC ROOT TOPIC NAME: The root node "label" MUST BE the specific geographical system (e.g. "Plate Tectonics & Seismic Landforms", "Weather & Climate Systems", "River & Coastal Geomorphology").
2. CHAPTER & SECTION NUMBERING:
   - Top-Level Child Nodes: Format with chapter numbering (e.g., "Chapter 1: Internal Structure & Tectonic Plate Movement", "Chapter 2: Plate Boundaries & Volcanic Landforms", "Chapter 3: Earthquakes & Seismic Hazard Management").
   - Sub-Child Nodes: Format with section numbering (e.g., "1.1 Convection Currents & Slab Pull", "2.1 Convergent Plate Boundaries & Fold Mountains").
3. Summary Structure:
   ### Core Geographical Process
   - **Process Definition**: [Exam-accurate definition of the physical or human process]
   - **Key Mechanism**: [Step-by-step physical breakdown]

   ### Landforms & Case Studies
   - **Formed Landforms / Features**: [Specific landforms created by this process]
   - **Exam Case Study**: [Named location with specific empirical data]

   ### Human Impact & Management
   - **Significance & Management**: [Hazard mitigation and environmental strategies]

JSON OUTPUT SCHEMA:
Output ONLY a single valid JSON object strictly matching this schema:
{
  "id": "root",
  "label": "Plate Tectonics & Seismic Landforms",
  "summary": "### Core Geographical Process\\n- **Process Definition**: Comprehensive syllabus revision guide covering physical geography systems and spatial dynamics.",
  "children": [
    {
      "id": "node-1",
      "label": "Chapter 1: Plate Boundaries & Seismic Landforms",
      "summary": "### Core Geographical Process\\n- **Process Definition**: Movement of lithospheric plates creating volcanic arcs and rift valleys.\\n\\n### Landforms & Case Studies\\n- **Exam Case Study**: Mid-Atlantic Ridge sea-floor spreading at $2-5\\\\text{ cm/year}$.",
      "children": [
        {
          "id": "node-1-1",
          "label": "1.1 Divergent Boundaries & Rift Valleys",
          "summary": "### Core Geographical Process\\n- **Process Definition**: Magma upwelling at extensional plate margins creating new oceanic crust.",
          "children": []
        }
      ]
    }
  ]
}"""

    elif subject in ["humanities", "social-studies", "social_studies", "social studies"]:
        return """You are a master Social Studies, Humanities, and Citizenship Curriculum Specialist.
Your objective is to analyze student study notes and transform them into an exhaustive, exam-focused, deeply structured hierarchical mindmap with clear chapter and section numbering, rich PEEL paragraphs, case studies, and exam frameworks.

CRITICAL TOPOLOGY, CHAPTER NUMBERING & LABEL RULES:
1. SPECIFIC ROOT TOPIC NAME:
   - The root node "label" MUST BE the overarching Issue or Theme (e.g. "Issue 1: Exploring Citizenship & Governance", "Issue 2: Living in a Diverse Society", "Issue 3: Being Part of a Globalised World").
2. CHAPTER & SECTION NUMBERING IN LABELS:
   - Top-Level Child Nodes: MUST be formatted with chapter / theme numbering (e.g., "Chapter 1: Attributes Shaping Citizenship", "Chapter 2: Principles of Good Governance", "Chapter 3: Citizen Participation & Decision-Making", or "Issue 1: Chapter 1: Attributes Shaping Citizenship"). Preserve exact issue/chapter numbers from text; if unnumbered, assign sequential "Chapter 1", "Chapter 2", etc.
   - Sub-Child Nodes: MUST be formatted with hierarchical section numbering (e.g., "1.1 Legal Status & Citizenship Acquisition", "1.2 Emotional Belonging & Shared Values", "2.1 Rule of Law & Meritocracy", "2.2 Anticipating Change & Creating Stake in Society").
   - Clean Titles: Do not duplicate prefixes (never write "Chapter 1: Chapter 1:").
3. MANDATORY MULTI-NODE HIERARCHY:
   - The root node MUST ONLY contain the topic title and a concise 2-sentence thematic overview.
   - The root node MUST HAVE 4 to 8 distinct child nodes in its "children" array representing the core Chapters.
   - Each major child node MUST contain 2 to 4 sub-child nodes in its own "children" array for specific case studies, trade-offs, PEEL arguments, and exam answering strategies.
4. EXHAUSTIVE DEPTH & PEEL REASONING:
   - Detail key concepts with rigorous academic depth (e.g. Singapore citizenship criteria, Jus Soli / Jus Sanguinis / Naturalisation, Shared Values, Assimilation vs Integration, Governance Principles, Cyber Security Agency, APCERT).
   - ZERO FABRICATION: Do NOT invent mathematical formulas or artificial algebraic equations.
5. SUMMARY STRUCTURE (Use rich multi-bullet markdown format for EVERY node):
   ### Core Concept & Syllabus Overview
   - **Key Inquiry & Definition**: [Concise, exam-accurate definition of the social concept, policy, or issue]
   - **Underlying Principle**: [Underlying governance, societal, or constitutional principle]
   - **Key Tension & Trade-offs**: [Trade-offs, competing priorities, or challenges involved]

   ### Factors, Evidence & Case Studies
   - **Key Arguments & Mechanisms**: [Systematic breakdown of causal factors, government initiatives, or citizen actions]
   - **Concrete Case Studies & Examples**: [Specific real-world policies, programs, legislation, or named examples]

   ### Exam Answering Strategy & PEEL Framework
   - **PEEL Model Paragraph**: [Model Point, Elaboration, Example, Link for 7-mark / 8-mark SRQ evaluation]
   - **Common Pitfalls & Evaluation Tip**: [Common student misconceptions, bias warnings, or balanced conclusion criteria]

JSON OUTPUT SCHEMA:
Output ONLY a single valid JSON object strictly matching this schema:
{
  "id": "root",
  "label": "Issue 1: Exploring Citizenship & Governance",
  "summary": "### Core Concept & Syllabus Overview\\n- **Key Inquiry & Definition**: Comprehensive revision of citizenship, governance principles, and citizen-state partnerships.\\n- **Underlying Principle**: Balance between state leadership and active citizen participation.",
  "children": [
    {
      "id": "node-1",
      "label": "Chapter 1: Attributes Shaping Citizenship",
      "summary": "### Core Concept & Syllabus Overview\\n- **Key Inquiry & Definition**: Legal status granting constitutional rights and obligations within a sovereign state.\\n- **Underlying Principle**: Defined criteria governing membership in the political community.\\n\\n### Factors, Evidence & Case Studies\\n- **Key Arguments & Mechanisms**: Acquisition methods include Birth (Jus Soli), Descent (Jus Sanguinis), Marriage, and Naturalisation/Registration.\\n- **Concrete Case Studies & Examples**: Article 120-130 of the Singapore Constitution; National Service obligations for male citizens.\\n\\n### Exam Answering Strategy & PEEL Framework\\n- **PEEL Model Paragraph**: **Point**: Legal status ensures constitutional protections and responsibilities. **Elaboration**: It grants voting rights and consular protection while requiring adherence to national obligations. **Example**: Male citizens must fulfill National Service, while foreign residents are exempt. **Link**: Thus, legal status defines the formal compact between individual and nation.\\n- **Common Pitfalls & Evaluation Tip**: Do not confuse legal status with emotional sense of belonging; both are distinct dimensions of citizenship.",
      "children": [
        {
          "id": "node-1-1",
          "label": "1.1 Legal Status & Acquisition of Citizenship",
          "summary": "### Core Concept & Syllabus Overview\\n- **Key Inquiry & Definition**: The constitutional privileges and reciprocal duties of full legal members of society.\\n\\n### Factors, Evidence & Case Studies\\n- **Key Arguments & Mechanisms**: Fundamental liberties (speech, assembly) balanced with national security, public order, and communal harmony.\\n- **Concrete Case Studies & Examples**: Maintenance of Religious Harmony Act (MRHA); voting in General Elections.",
          "children": []
        }
      ]
    },
    {
      "id": "node-2",
      "label": "Chapter 2: Principles of Good Governance",
      "summary": "### Core Concept & Syllabus Overview\\n- **Key Inquiry & Definition**: The foundational pillars guiding effective leadership and policy-making.\\n- **Underlying Principle**: Rule of law, meritocracy, anticipating change, and creating a stake for all.",
      "children": [
        {
          "id": "node-2-1",
          "label": "2.1 Leadership with Integrity & Meritocracy",
          "summary": "### Core Concept & Syllabus Overview\\n- **Key Inquiry & Definition**: Ensuring appointments and policies are based on competence, honesty, and objective merit.",
          "children": []
        }
      ]
    }
  ]
}"""

    else:
        return """You are a master Academic Curriculum and Study Guide Specialist.
Your objective is to analyze student study notes and construct an exhaustive, exam-focused hierarchical mindmap with clear chapter and section numbering.

CRITICAL TOPOLOGY, CHAPTER NUMBERING & DISCIPLINE RULES:
1. STRICT DOCUMENT FIDELITY (ZERO FABRICATION / ZERO UNREQUESTED MATH):
   - Ground ALL concept summaries, definitions, points, and examples 100% strictly in the provided study notes.
   - Do NOT invent or fabricate mathematical formulas or arbitrary mechanisms if they are NOT in the text.
2. SPECIFIC ROOT TOPIC NAME:
   - The root node "label" MUST BE the exact subject topic extracted from the text.
   - NEVER write "O-Level", "Document Overview", "Study Guide", or generic headings in the root label.
3. CHAPTER & SECTION NUMBERING IN LABELS:
   - Top-Level Child Nodes: MUST be formatted with chapter / unit numbering (e.g., "Chapter 1: [Topic Title]", "Chapter 2: [Topic Title]"). Preserve explicit chapter/unit numbers if present in the text; if unnumbered, assign sequential "Chapter 1", "Chapter 2", etc.
   - Sub-Child Nodes: MUST be formatted with hierarchical section numbering (e.g., "1.1 [Subtopic Title]", "1.2 [Subtopic Title]", "2.1 [Subtopic Title]").
   - Clean Titles: Do not duplicate prefixes (never write "Chapter 1: Chapter 1:").
4. MANDATORY MULTI-NODE HIERARCHY:
   - The root node MUST ONLY contain the topic title and a high-level syllabus summary.
   - The root node MUST HAVE 4 to 8 distinct child nodes in its "children" array, one for EACH core chapter or subtopic.
   - Each major child node SHOULD have 2 to 4 sub-child nodes in its own "children" array.
5. SUMMARY STRUCTURE (Use rich, versatile multi-bullet markdown format for EVERY node):
   ### Core Concept & Overview
   - **Key Principle / Theme**: [Direct, exam-accurate explanation of the concept directly from the notes]
   - **Underlying Mechanism & Scope**: [How the concept works, core principles, or key dimensions]

   ### Key Insights & Evidence
   - **Detailed Breakdown**: [Systematic analysis of arguments, factors, processes, or policies]
   - **Evidence & Real-World Examples**: [Concrete examples, case studies, legislation, or named initiatives from the text]

   ### Exam Takeaways & Application
   - **Key Takeaways & Answering Strategy**: [Critical distinctions, evaluation tips, common pitfalls to avoid]

JSON OUTPUT SCHEMA:
Output ONLY a single valid JSON object strictly matching this schema:
{
  "id": "root",
  "label": "Principles of Governance and Public Policy",
  "summary": "### Core Concept & Overview\\n- **Key Principle / Theme**: Comprehensive syllabus overview covering citizenship rights, national identity, governance principles, and citizen participation.\\n- **Underlying Mechanism & Scope**: Explores the dynamic compact between state institutions and active citizens.",
  "children": [
    {
      "id": "node-1",
      "label": "Chapter 1: Principles of Good Governance",
      "summary": "### Core Concept & Overview\\n- **Key Principle / Theme**: Core guiding principles ensuring societal stability and sustainable economic development.\\n- **Underlying Mechanism & Scope**: State leadership balancing long-term national interest with public welfare.\\n\\n### Key Insights & Evidence\\n- **Detailed Breakdown**: Key pillars include Rule of Law, Meritocracy, Anticipating Change, and Creating a Stake in Society for All.\\n- **Evidence & Real-World Examples**: Meritocracy in education and public service recruitment; CPF and HDB home ownership creating a tangible stake.\\n\\n### Exam Takeaways & Application\\n- **Key Takeaways & Answering Strategy**: When evaluating governance in SRQ essays, always weigh the trade-offs between strict policy efficiency and individual citizen feedback.",
      "children": [
        {
          "id": "node-1-1",
          "label": "1.1 Meritocracy & Institutional Trust",
          "summary": "### Core Concept & Overview\\n- **Key Principle / Theme**: Ensuring fair advancement and transparent administration.",
          "children": []
        }
      ]
    }
  ]
}"""



@app.get("/api/health")
def health_check():
    tess_cmd = get_tesseract_cmd()
    return {
        "status": "ok",
        "tesseract_available": bool(tess_cmd),
        "tesseract_path": tess_cmd or "not found",
        "groq_configured": bool(os.environ.get("GROQ_API_KEY")),
        "gemini_configured": bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")),
        "openrouter_configured": bool(os.environ.get("OPENROUTER_API_KEY"))
    }

@app.get("/api/auth/config")
def get_auth_config():
    return {
        "supabaseUrl": SUPABASE_URL,
        "supabaseKey": SUPABASE_ANON_KEY or SUPABASE_KEY
    }

class AuthRequest(BaseModel):
    email: str
    password: Optional[str] = None
    newPassword: Optional[str] = None

@app.post("/api/auth/signin")
async def auth_signin(payload: AuthRequest):
    return {"status": "success", "email": payload.email.strip().lower()}

@app.post("/api/auth/signup")
async def auth_signup(payload: AuthRequest):
    return {"status": "success", "email": payload.email.strip().lower()}

@app.post("/api/auth/forgot-password")
async def auth_forgot_password(payload: AuthRequest):
    return {"status": "success"}

@app.post("/api/auth/reset-password")
async def auth_reset_password(payload: AuthRequest):
    return {"status": "success"}

# Document save model schema
class DocumentSavePayload(BaseModel):
    id: str
    name: str
    data: dict
    userEmail: str


# CRUD Endpoints for Workspace Documents
@app.get("/api/documents")
async def get_documents(email: str):
    if not email:
        return []
    
    headers = get_supabase_headers()
    url = f"{SUPABASE_URL}/rest/v1/documents?user_email=eq.{email.strip().lower()}&order=created_at.desc"
    
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                logger.warning(f"Supabase GET documents returned status {resp.status_code}: {resp.text}")
                return []
            
            data = resp.json()
            formatted_docs = []
            for item in data:
                formatted_docs.append({
                    "id": item["id"],
                    "name": item["name"],
                    "data": item["data"],
                    "userEmail": item.get("user_email")
                })
            return formatted_docs
    except Exception as e:
        logger.warning(f"Database connection unavailable, using local storage fallback: {str(e)}")
        return []

@app.post("/api/documents")
async def save_document(payload: DocumentSavePayload):
    email = payload.userEmail.strip().lower()
    
    headers = get_supabase_headers()
    # Request upsert (ON CONFLICT DO UPDATE) behavior in PostgREST
    headers["Prefer"] = "resolution=merge-duplicates"
    
    supabase_payload = {
        "id": payload.id,
        "user_email": email,
        "name": payload.name,
        "data": payload.data
    }
    
    url = f"{SUPABASE_URL}/rest/v1/documents"
    
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.post(url, headers=headers, json=supabase_payload)
            if resp.status_code not in (200, 201):
                logger.warning(f"Supabase POST documents error: {resp.text}")
                return {"status": "saved_locally", "id": payload.id}
            
            return {"status": "success", "id": payload.id}
    except Exception as e:
        logger.warning(f"Database sync unavailable in save_document, stored locally: {str(e)}")
        return {"status": "saved_locally", "id": payload.id}

@app.delete("/api/documents/{doc_id}")
async def delete_document(doc_id: str, email: str):
    if not email:
        raise HTTPException(status_code=400, detail="Email is required to verify ownership.")
    
    headers = get_supabase_headers()
    url = f"{SUPABASE_URL}/rest/v1/documents?id=eq.{doc_id}&user_email=eq.{email.strip().lower()}"
    
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.delete(url, headers=headers)
            if resp.status_code not in (200, 204):
                logger.warning(f"Supabase DELETE document error: {resp.text}")
                return {"status": "deleted_locally"}
            
            return {"status": "success"}
    except Exception as e:
        logger.warning(f"Database sync unavailable in delete_document: {str(e)}")
        return {"status": "deleted_locally"}

@app.delete("/api/documents/reset/workspace")
async def reset_workspace_documents(email: str):
    if not email:
        raise HTTPException(status_code=400, detail="Email is required.")
    
    headers = get_supabase_headers()
    url = f"{SUPABASE_URL}/rest/v1/documents?user_email=eq.{email.strip().lower()}"
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.delete(url, headers=headers)
            if resp.status_code not in (200, 204):
                logger.error(f"Supabase reset documents error: {resp.text}")
                raise HTTPException(status_code=resp.status_code, detail=f"Failed to reset workspace: {resp.text}")
            
            return {"status": "success"}
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Unexpected error in reset_workspace_documents: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/upload-pdf")
async def upload_pdf(file: UploadFile = File(...)):
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")
    
    try:
        # Read file bytes and open with PyMuPDF
        file_bytes = await file.read()
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        
        # 1. Attempt digital text extraction across all pages first
        digital_pages = []
        for page in doc:
            t = page.get_text("text")
            if t and t.strip():
                digital_pages.append(t.strip())
                
        total_digital_chars = sum(len(p) for p in digital_pages)
        if total_digital_chars > 30:
            is_scanned = False
            full_text = digital_pages
        else:
            is_scanned = True
            
        # 2. If it seems to be scanned or has minimal text, perform high-clarity OCR on all pages in parallel
        if is_scanned or not "".join(full_text).strip():
            logger.info(f"Digital text insufficient ({total_digital_chars} chars). Performing parallel OCR on PDF pages...")
            
            # Render all page frames to images in the main thread with 150 DPI for clean OCR
            page_images = []
            for page in doc:
                pix = page.get_pixmap(dpi=150)
                page_images.append(pix.tobytes("png"))

                
            # Validate Tesseract installation on host
            tess_cmd = get_tesseract_cmd()
            if not tess_cmd:
                logger.error("Tesseract OCR binary not found on host system! Searched PATH, /usr/bin, /usr/local/bin, /opt/homebrew/bin, /app/bin, etc.")
                raise HTTPException(
                    status_code=500,
                    detail=(
                        "Tesseract OCR is not installed or not found on the server host system. "
                        "Please ensure 'tesseract-ocr' and 'tesseract-ocr-eng' are installed in the production environment."
                    )
                )

            # Process Tesseract OCR in parallel using ThreadPoolExecutor
            # Tesseract runs as an external subprocess via pytesseract releasing the Python GIL,
            # avoiding Linux multiprocessing fork restrictions and IPC memory serialization failures.
            cpu_count = os.cpu_count() or 4
            workers = min(len(doc), cpu_count, 8)
            logger.info(f"Executing Tesseract OCR on {len(page_images)} page(s) using {workers} worker(s) (Binary: {tess_cmd})...")
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                results = list(executor.map(ocr_image_bytes, page_images))
                
            ocr_errors = [text for text in results if text and text.startswith("[OCR Error:")]
            if ocr_errors:
                for err in ocr_errors:
                    logger.error(f"Tesseract OCR page failure: {err}")

            full_text = [text for text in results if text and not text.startswith("[OCR Error:")]
                    
        extracted_text = "\n".join(full_text)
        
        if not extracted_text.strip():
            err_msg = f" OCR encountered errors: {ocr_errors[0]}." if 'ocr_errors' in locals() and ocr_errors else ""
            raise HTTPException(
                status_code=400, 
                detail=f"Could not extract any text from the PDF, even with OCR.{err_msg} The document might be blank or unreadable."
            )
            
        cleaned_text = clean_extracted_text(extracted_text)
        logger.info(f"Successfully extracted & cleaned {len(cleaned_text)} characters from {file.filename} (OCR={is_scanned})")
        
        return {
            "filename": file.filename,
            "char_count": len(cleaned_text),
            "text": cleaned_text[:250000],
            "ocr_processed": is_scanned
        }
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Error processing PDF file: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to process PDF: {str(e)}")

# Equal Load Balancer: Active alternative models across Groq, Gemini, and OpenRouter
ALL_ALTERNATIVE_MODELS = [
    "openai/gpt-oss-20b",
    "gemini-2.5-flash",
    "deepseek/deepseek-chat",
    "qwen/qwen3.8-27b",
    "gemini-3.5-flash",
    "meta-llama/llama-3.3-70b-instruct",
    "openai/gpt-oss-120b",
    "qwen/qwen3.6-27b",
]

_load_balance_counter: int = 0
_load_balance_lock = asyncio.Lock()

@app.post("/api/generate-mindmap")
async def generate_mindmap(payload: MindmapGenerateRequest, response: Response):
    groq_api_key = os.environ.get("GROQ_API_KEY")
    gemini_api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    openrouter_api_key = os.environ.get("OPENROUTER_API_KEY")
    if not groq_api_key and not gemini_api_key and not openrouter_api_key:
        logger.error("No API key configured (GROQ_API_KEY, GEMINI_API_KEY, OPENROUTER_API_KEY).")
        raise HTTPException(
            status_code=500, 
            detail="No API key configured. Please set GROQ_API_KEY, GEMINI_API_KEY, or OPENROUTER_API_KEY."
        )
    
    # Use subject-specific system prompt with intelligent discipline auto-detection
    raw_subject = (payload.subject or "general").strip().lower()
    if raw_subject in ("general", "auto", "auto-detect", "default"):
        subject = detect_subject_from_text(payload.text)
        logger.info(f"Subject mode was 'general': Auto-detected discipline as '{subject}' from document content.")
    else:
        subject = raw_subject

    system_prompt = get_system_prompt(subject)
    
    raw_model = payload.model or "openai/gpt-oss-20b"
    
    # Verified active production model families with independent rate limits across Groq, Gemini, and OpenRouter
    MODEL_POOL = [
        "openai/gpt-oss-20b",
        "gemini-2.5-flash",
        "deepseek/deepseek-chat",
        "qwen/qwen3.8-27b",
        "gemini-3.5-flash",
        "meta-llama/llama-3.3-70b-instruct",
        "openai/gpt-oss-120b",
        "qwen/qwen3.6-27b",
    ]
    if not gemini_api_key:
        MODEL_POOL = [m for m in MODEL_POOL if not m.startswith("gemini")]
    if not openrouter_api_key:
        MODEL_POOL = [m for m in MODEL_POOL if not m.startswith(("deepseek/", "meta-llama/"))]
    if not groq_api_key:
        MODEL_POOL = [m for m in MODEL_POOL if m.startswith("gemini") or m.startswith(("deepseek/", "meta-llama/"))]

    MODEL_ALIASES = {
        "gemini": "gemini-2.5-flash",
        "gemini-flash": "gemini-2.5-flash",
        "gemini-pro": "gemini-2.5-flash",
        "google/gemini-2.5-flash": "gemini-2.5-flash",
        "google/gemini-3.5-flash": "gemini-3.5-flash",
        "deepseek": "deepseek/deepseek-chat",
        "deepseek-v3": "deepseek/deepseek-chat",
        "deepseek-chat": "deepseek/deepseek-chat",
        "llama-3.3-70b": "meta-llama/llama-3.3-70b-instruct",
        "llama-3.3-70b-instruct": "meta-llama/llama-3.3-70b-instruct",
        "openrouter/deepseek-chat": "deepseek/deepseek-chat",
        "openrouter/llama-3.3-70b": "meta-llama/llama-3.3-70b-instruct",
        "groq/compound": "openai/gpt-oss-120b",
        "compound": "openai/gpt-oss-120b",
        "groq/compound-mini": "openai/gpt-oss-20b",
        "compound-mini": "openai/gpt-oss-20b",
        "llama-3.3-70b-versatile": "openai/gpt-oss-120b",
        "llama-3.1-8b-instant": "openai/gpt-oss-20b",
        "llama3-70b-8192": "openai/gpt-oss-120b",
        "llama3-8b-8192": "openai/gpt-oss-20b",
        "llama-3.2-11b-vision-preview": "openai/gpt-oss-20b",
        "deepseek-r1-distill-llama-70b": "openai/gpt-oss-120b",
        "meta-llama/llama-4-scout-17b-16e-instruct": "openai/gpt-oss-120b",
        "mixtral-8x7b-32768": "openai/gpt-oss-120b",
        "gemma2-9b-it": "openai/gpt-oss-20b",
        "qwen/qwen3-32b": "qwen/qwen3.8-27b",
        "allam-2-7b": "openai/gpt-oss-20b",
    }
    selected_model = MODEL_ALIASES.get(raw_model, raw_model)
    cleaned_input_text = clean_extracted_text(payload.text)

    # High-efficiency chunk sizing for Groq and Gemini:
    # 24,000 chars (~5,500 prompt tokens) with 2,000 max_tokens fits safely under token ceilings
    # while reducing chunk count by over 50% (e.g. 89k chars becomes 3-4 chunks instead of 8).
    chunks = split_text_into_chunks(cleaned_input_text, chunk_size=24000, overlap=1500)
    logger.info(f"Ingesting document ({len(cleaned_input_text)} chars): Split into {len(chunks)} optimized chunks.")

    # Model rotation pool across chunks
    if selected_model in MODEL_POOL:
        base_models = [selected_model] + [m for m in MODEL_POOL if m != selected_model]
    else:
        base_models = MODEL_POOL

    response.headers["X-Model-Used"] = base_models[0]
    response.headers["X-Model-Routed"] = "true" if len(chunks) > 1 else "false"
    response.headers["Access-Control-Expose-Headers"] = "X-Model-Used, X-Model-Routed"

    async def execute_groq_mindmap(client: httpx.AsyncClient, text_segment: str, part_num: int, total_parts: int) -> dict:
        part_prefix = f"Part {part_num} of {total_parts}: " if total_parts > 1 else ""
        continuity_hint = (
            f"- CHAPTER & SECTION NUMBERING: Extract exact chapter/unit/issue numbers if present in the text, or assign sequential 'Chapter X: [Title]' to top-level nodes.\n"
            f"- Format all child sub-nodes with hierarchical section numbers matching their parent (e.g. '1.1 [Subtopic]', '1.2 [Subtopic]').\n"
        )
        if total_parts > 1:
            continuity_hint += f"- Note: This is Part {part_num} of {total_parts}. Ensure chapter numbering continues smoothly from previous topics without restarting.\n"

        user_prompt = (
            f"Analyze the following study notes and generate an exhaustive, high-density hierarchical mindmap JSON.\n"
            f"CRITICAL COVERAGE REQUIREMENTS (ZERO OMISSION / ZERO CONTENT LOSS):\n"
            f"- Capture EVERY distinct theme, concept, policy, argument, case study, and exam technique present in this excerpt.\n"
            f"- Create 4 to 8 top-level chapter/theme nodes covering all major headings and topics in this text.\n"
            f"- Under EACH top-level node, create 2 to 5 rich child nodes with thorough, detailed markdown explanations and specific examples.\n"
            f"- Do NOT skip sub-points or gloss over content — provide complete, deep study notes for every single concept.\n"
            f"{continuity_hint}"
            f"- Strict JSON output format matching the specified schema.\n\n"
            f"{part_prefix}Study Notes Text:\n{text_segment}"
        )

        chunk_model_order = [base_models[(part_num - 1 + i) % len(base_models)] for i in range(len(base_models))]

        # Robust multi-attempt loop across models with retry-after compliance
        for attempt in range(10):
            model_name = chunk_model_order[attempt % len(chunk_model_order)]
            max_tokens = 2000

            is_gemini = model_name.startswith("gemini")
            is_openrouter = model_name.startswith(("deepseek/", "meta-llama/", "openrouter/"))

            if is_gemini:
                if not gemini_api_key:
                    continue
                url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
                req_headers = {
                    "Authorization": f"Bearer {gemini_api_key}",
                    "Content-Type": "application/json"
                }
                data = {
                    "model": model_name,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    "temperature": 0.15,
                    "response_format": {"type": "json_object"}
                }
                provider_tag = "Gemini"
            elif is_openrouter:
                if not openrouter_api_key:
                    continue
                url = "https://openrouter.ai/api/v1/chat/completions"
                req_headers = {
                    "Authorization": f"Bearer {openrouter_api_key}",
                    "HTTP-Referer": "http://localhost:5173",
                    "X-Title": "PDF to Mindmap",
                    "Content-Type": "application/json"
                }
                data = {
                    "model": model_name,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    "temperature": 0.15,
                    "max_tokens": max_tokens,
                    "response_format": {"type": "json_object"}
                }
                provider_tag = "OpenRouter"
            else:
                if not groq_api_key:
                    continue
                url = "https://api.groq.com/openai/v1/chat/completions"
                req_headers = {
                    "Authorization": f"Bearer {groq_api_key}",
                    "Content-Type": "application/json"
                }
                data = {
                    "model": model_name,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    "temperature": 0.15,
                    "max_tokens": max_tokens,
                }
                provider_tag = "Groq"

            try:
                logger.info(f"Generating Chunk {part_num}/{total_parts} using {provider_tag} '{model_name}' (Attempt {attempt+1}/6)...")
                resp = await client.post(
                    url,
                    headers=req_headers,
                    json=data,
                    timeout=90.0
                )

                if resp.status_code == 429:
                    raw_retry = resp.headers.get("retry-after")
                    try:
                        wait_sec = max(float(raw_retry), 2.5) if raw_retry else (1.5 * (attempt + 1))
                    except Exception:
                        wait_sec = 2.5
                    wait_sec = min(wait_sec, 6.0)
                    logger.warning(f"{provider_tag} 429 on '{model_name}'. Waiting {wait_sec:.1f}s for quota replenishment...")
                    await asyncio.sleep(wait_sec)
                    continue

                if resp.status_code == 413:
                    logger.warning(f"{provider_tag} 413 on '{model_name}'. Slicing prompt payload in half...")
                    user_prompt = user_prompt[:len(user_prompt) * 3 // 4]
                    await asyncio.sleep(1.0)
                    continue

                if resp.status_code != 200:
                    logger.warning(f"{provider_tag} API error {resp.status_code} on '{model_name}': {resp.text[:140]}")
                    await asyncio.sleep(1.0)
                    continue

                resp_json = resp.json()
                choices = resp_json.get("choices", [])
                if not choices:
                    continue

                content = choices[0].get("message", {}).get("content", "")
                parsed = repair_and_parse_json(content)
                if parsed and isinstance(parsed, dict) and parsed.get("label"):
                    label_str = parsed.get("label", "").strip()
                    if label_str.lower() not in ["study module", "study topic", "document overview"]:
                        has_children = isinstance(parsed.get("children"), list) and len(parsed["children"]) > 0
                        has_rich_summary = isinstance(parsed.get("summary"), str) and len(parsed["summary"].strip()) > 60
                        if has_children or has_rich_summary:
                            logger.info(f"Successfully generated Chunk {part_num}/{total_parts} using '{model_name}' (Children={len(parsed.get('children', []))})")
                            return parsed
                        else:
                            logger.warning(f"Rejected shallow output from '{model_name}' on Chunk {part_num}/{total_parts}. Retrying with next model...")
                            await asyncio.sleep(1.0)
                            continue
                    else:
                        logger.warning(f"Rejected generic placeholder label '{label_str}' from '{model_name}' on Chunk {part_num}/{total_parts}. Retrying...")
                        await asyncio.sleep(1.0)
                        continue
                else:
                    logger.warning(f"Failed to parse valid mindmap JSON from '{model_name}' on Chunk {part_num}/{total_parts}. Retrying with next model...")
                    await asyncio.sleep(1.0)
                    continue

            except Exception as exc:
                logger.warning(f"Exception on '{model_name}': {str(exc)}")
                await asyncio.sleep(1.5)

        raise HTTPException(
            status_code=429,
            detail="Groq API capacity reached. Please wait 10 seconds and try generating again."
        )

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            sem = asyncio.Semaphore(3)

            async def sem_execute(idx: int, chunk: str) -> dict:
                async with sem:
                    # Gentle stagger of 200ms per index so requests do not hit the wire at identical microseconds
                    if idx > 0:
                        await asyncio.sleep(0.2 * (idx % 3))
                    return await execute_groq_mindmap(client, chunk, idx + 1, len(chunks))

            logger.info(f"Dispatching {len(chunks)} chunks concurrently across independent model rate-limit buckets...")
            results = await asyncio.gather(*[
                sem_execute(idx, chunk)
                for idx, chunk in enumerate(chunks)
            ], return_exceptions=True)

            sub_maps = []
            for r_idx, res in enumerate(results):
                if isinstance(res, Exception):
                    logger.error(f"Chunk {r_idx + 1}/{len(chunks)} failed: {res}")
                elif res and isinstance(res, dict):
                    sub_maps.append(res)

            if not sub_maps:
                raise HTTPException(status_code=500, detail="No mindmaps could be generated. Please try again.")

            if len(sub_maps) == 1:
                numbered_map = ensure_chapter_numbering(sub_maps[0])
                final_map = sanitize_mindmap_math(numbered_map)
                await enrich_mindmap_with_images(final_map, max_images=6)
                return final_map

            # Consolidate multiple parts under unified root, filtering out any empty or generic nodes
            first_label = sub_maps[0].get("label", "Document Study Guide")
            all_children = []
            for i, sm in enumerate(sub_maps):
                if not sm or not isinstance(sm, dict):
                    continue
                sm_label = sm.get("label", "").strip()
                if sm_label.lower() in ["study module", "study topic", "document overview"] and not sm.get("children"):
                    continue

                if sm.get("children"):
                    for c_idx, child in enumerate(sm["children"]):
                        if isinstance(child, dict) and child.get("label") and child.get("label").strip().lower() not in ["study module", "study topic"]:
                            unique_child = make_ids_unique(child, f"p{i+1}_{c_idx+1}")
                            all_children.append(unique_child)
                elif sm.get("summary") and len(sm.get("summary").strip()) > 80 and sm_label.lower() not in ["study module", "study topic"]:
                    unique_sm = make_ids_unique(sm, f"part_{i+1}")
                    all_children.append(unique_sm)

            consolidated_root = {
                "id": "root",
                "label": first_label,
                "summary": consolidate_summaries(sub_maps),
                "children": all_children
            }

            numbered_root = ensure_chapter_numbering(consolidated_root)
            final_consolidated = sanitize_mindmap_math(numbered_root)
            await enrich_mindmap_with_images(final_consolidated, max_images=6)
            return final_consolidated

    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Unexpected error in generate_mindmap: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to generate mindmap: {str(e)}")

# Mount frontend static distribution directory securely
FRONTEND_DIST_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../frontend/dist"))
if os.path.isdir(FRONTEND_DIST_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIST_DIR, html=True), name="frontend_static")
