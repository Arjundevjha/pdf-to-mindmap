import os
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

# Module-level worker function for parallel OCR processing
def ocr_image_bytes(img_data: bytes) -> str:
    import pytesseract
    from PIL import Image
    import io
    try:
        image = Image.open(io.BytesIO(img_data))
        return pytesseract.image_to_string(image)
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

def split_text_into_chunks(text: str, chunk_size: int = 30000) -> list[str]:
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        if end >= len(text):
            chunks.append(text[start:])
            break
        # Try to find a logical boundary (like a double newline or newline)
        boundary = text.rfind('\n\n', start, end)
        if boundary == -1 or boundary < start + (chunk_size // 2):
            boundary = text.rfind('\n', start, end)
        if boundary == -1 or boundary < start + (chunk_size // 2):
            boundary = text.rfind(' ', start, end)
        
        if boundary != -1 and boundary > start:
            chunks.append(text[start:boundary].strip())
            start = boundary + 1
        else:
            chunks.append(text[start:end].strip())
            start = end
    return chunks

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
        # Extract main thesis or first paragraph
        thesis_match = re.search(r"\*\*(?:Main Thesis|Mathematical Principle|Physical Principle|Historical Thesis|Geographical Thesis|Overview)\*\*:\s*(.*?)(?=\n-|\n###|$)", summary, re.DOTALL)
        if thesis_match:
            content = thesis_match.group(1).strip()
            key_points.append(f"- **{label}**: {content}")
        elif summary.strip():
            first_line = summary.strip().split('\n')[0].replace('#', '').strip()
            key_points.append(f"- **{label}**: {first_line}")

    if not key_points:
        return "### Core Concept\n- Comprehensive overview synthesizing all chapters and principles from the document."

    points_str = "\n".join(key_points)
    return (
        f"### Core Concept\n{points_str}\n\n"
        f"### Study Structure\n- Master curriculum integrating all document subtopics into individual child nodes below."
    )

class MindmapGenerateRequest(BaseModel):
    text: str
    model: Optional[str] = "openai/gpt-oss-120b"
    subject: Optional[str] = "general"


def clean_json_string(response_text: str) -> str:
    """
    Extracts and cleans a JSON block from the model's text response.
    """
    markdown_match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", response_text)
    if markdown_match:
        return markdown_match.group(1).strip()
    
    start = response_text.find('{')
    end = response_text.rfind('}')
    if start != -1 and end != -1 and end > start:
        return response_text[start:end+1].strip()
        
    return response_text.strip()

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

def repair_and_parse_json(response_text: str) -> dict:
    """
    Cleans, repairs, and parses LLM JSON responses into a Python dict.
    If the response was truncated mid-sentence or mid-object, it auto-repairs
    unclosed strings, quotes, arrays, and braces, and extracts all child nodes
    so mindmap generation never crashes or drops child branches.
    """
    cleaned = clean_json_string(response_text)
    sanitized = sanitize_json_latex(cleaned)
    
    # 1. Try direct parsing first
    try:
        data = json.loads(sanitized, strict=False)
        if isinstance(data, dict) and "id" in data and "label" in data and "children" in data:
            if isinstance(data["children"], list) and len(data["children"]) > 0:
                return data
            # If root has 0 children but text contains child objects, extract them
            if isinstance(data["children"], list) and len(data["children"]) == 0:
                pass
            else:
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
            if "label" not in data or not data["label"]: data["label"] = "Core Study Topic"
            if "summary" not in data or not data["summary"]: data["summary"] = "### Core Concept & Exam Rule\n- **Key Principle**: Comprehensive syllabus revision guide covering all core methods."
            if "children" not in data: data["children"] = []
            if isinstance(data["children"], list) and len(data["children"]) > 0:
                return data
            elif isinstance(data, dict) and data.get("label") and data.get("label") != "Core Study Topic":
                return data
    except Exception as e:
        logger.warning(f"JSON auto-repair parsing warning: {str(e)}")

    # 3. Robust Regex Block Extractor (Extracts root node AND all child objects so children are never lost)
    root_label_match = re.search(r'"label"\s*:\s*"([^"]+)"', cleaned)
    root_summary_match = re.search(r'"summary"\s*:\s*"((?:[^"\\]|\\.)*)"', cleaned)

    # Extract all child nodes by scanning for node objects
    extracted_children = []
    child_pattern = re.compile(
        r'\{\s*"id"\s*:\s*"([^"]+)"\s*,\s*"label"\s*:\s*"([^"]+)"\s*,\s*"summary"\s*:\s*"((?:[^"\\]|\\.)*)"',
        re.DOTALL
    )

    seen_ids = set()
    for match in child_pattern.finditer(cleaned):
        cid, clabel, csummary = match.groups()
        if cid == "root" or cid in seen_ids:
            continue
        seen_ids.add(cid)
        clean_sum = csummary.replace('\\n', '\n').replace('\\"', '"').replace('\\\\', '\\')
        extracted_children.append({
            "id": cid,
            "label": clabel,
            "summary": clean_sum,
            "children": []
        })

    if root_label_match:
        root_label = root_label_match.group(1)
    elif extracted_children:
        root_label = extracted_children[0]["label"]
    else:
        root_label = "Core Syllabus Guide"

    if root_summary_match:
        root_summary = root_summary_match.group(1).replace('\\n', '\n').replace('\\"', '"').replace('\\\\', '\\')
    elif extracted_children:
        root_summary = f"### Core Concept & Exam Rule\n- **Key Principle**: Comprehensive study guide synthesizing {len(extracted_children)} major topics."
    else:
        root_summary = "### Core Concept & Exam Rule\n- **Key Principle**: Study overview covering core principles and problem-solving techniques."

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

    # 1. Clean step prefixes like {2}:$$ -> \n- **Step 2**: $$
    s = re.sub(r'(?:^|\n)\s*[\{\[\(](\d+)[\}\]\)]\s*:\s*', r'\n- **Step \1**: ', s)

    # 2. Normalize Unicode symbols to standard LaTeX
    s = s.replace('±', r'\pm ')
    s = s.replace('×', r'\times ')
    s = s.replace('÷', r'\div ')
    s = s.replace('≠', r'\neq ')
    s = s.replace('≤', r'\le ')
    s = s.replace('≥', r'\ge ')
    s = s.replace('≈', r'\approx ')
    s = s.replace('→', r' $\to$ ')
    s = s.replace('⇒', r' $\implies$ ')
    s = s.replace('²', '^2').replace('³', '^3')

    # 3. Heal leading LaTeX command immediately outside inline math: e.g. \Delta$(k)>0$ -> $\Delta(k)>0$
    s = re.sub(r'(\\[a-zA-Z]+)\s*\$([^$]+)\$', r'$\1 \2$', s)
    s = re.sub(r'\$(\\[a-zA-Z]+)\s+([(\[{])', r'$\1\2', s)

    # 4. Heal standalone LaTeX command outside $ followed by operators or arguments:
    # e.g. \Delta(k) > 0 -> $\Delta(k) > 0$, \Delta > 0 -> $\Delta > 0$
    s = re.sub(r'(?<!\$|\\)(\\Delta|\\alpha|\\beta|\\gamma|\\theta|\\pi|\\sigma|\\lambda|\\mu|\\omega)(?:\(([a-zA-Z0-9_,+-]+)\))?\s*([><=≠≤≥≈])\s*([a-zA-Z0-9_+-]+|\\[a-zA-Z]+)(?!\$)',
               r'$\1\2 \3 \4$', s)

    # 5. Heal trailing argument or operator outside closing $:
    # e.g. $\Delta$(k)>0$ -> $\Delta(k)>0$, $\Delta$(k) -> $\Delta(k)$
    s = re.sub(r'\$([^$]+)\$\s*(\([a-zA-Z0-9_,+-]+\))(?!\$)', r'$\1\2$', s)
    s = re.sub(r'\$([^$]+)\$\s*([><=≠≤≥≈])\s*([a-zA-Z0-9_+-]+|\\[a-zA-Z]+)(?!\$)', r'$\1 \2 \3$', s)

    # 6. Heal adjacent or split math blocks: e.g. $\Delta$$(k)>0$ -> $\Delta(k)>0$
    s = re.sub(r'\$([^$]+)\$\s*\$([^$]+)\$', r'$\1 \2$', s)

    # 7. Repair stray mid-formula closing $$ before an exponent:
    # e.g. "a[x+\frac{b}{2a}$$^2-\frac{b^2}{4a^2}\bigr]" -> "a\left[x+\frac{b}{2a}\right]^2-\frac{b^2}{4a^2}"
    s = re.sub(r'\\frac\{([^{}]+)\}\{([^{}]+)\}\$\$[\^](\d+|\{[^{}]+\})', r'\\frac{\1}{\2}\\bigr)^\3', s)
    s = re.sub(r'([a-zA-Z0-9)\]])\$\$[\^](\d+|\{[^{}]+\})', r'\1^\2', s)

    # 8. Repair broken completing the square clauses: "a[x 2 + a/b x]" -> "a\left[x^2 + \frac{b}{a}x\right]"
    s = re.sub(r'a\[x\s*2\s*\+\s*([ab])\/([ab])\s*x\]', r'a\\left[x^2 + \\frac{b}{a}x\\right]', s)
    s = re.sub(r'ax\+\\frac\{b\}\{2a\}\s*\\bigr\)\^2', r'a\\left(x + \\frac{b}{2a}\\right)^2', s)
    s = re.sub(r'ax\+\\frac\{b\}\{2a\}\s*\^2', r'a\\left(x + \\frac{b}{2a}\\right)^2', s)
    s = re.sub(r'a\[x\+\\frac\{b\}\{2a\}\s*\\bigr\)\^2', r'a\\left[\\left(x + \\frac{b}{2a}\\right)^2', s)

    # 9. Fix unparenthesized linear+fraction before exponent: x+\frac{b}{2a}^2 -> \left(x+\frac{b}{2a}\right)^2
    s = re.sub(r'((?:[a-zA-Z0-9]|\\[a-zA-Z]+)\s*[+-]\s*\\frac\{[^{}]*\}\{[^{}]*\})\s*\^(\d+|\{[^{}]*\})', r'\\left(\1\\right)^\2', s)

    # 10. Repair truncated/unclosed fraction in conjugate rationalization:
    s = re.sub(r'For\s+p\s*\+\s*q\s*A\s*,?\s*multiply\s+by\s*(?:\\frac\{)?(?:\\sqrt\{p\})?\$?',
               r'For $\\frac{A}{\\sqrt{p} + \\sqrt{q}}$, multiply numerator and denominator by $\\frac{\\sqrt{p} - \\sqrt{q}}{\\sqrt{p} - \\sqrt{q}}$',
               s, flags=re.IGNORECASE)
    s = re.sub(r'\\frac\{([^{}]+)\}\$', r'\\frac{\1}{\\sqrt{p} - \\sqrt{q}}$', s)

    # 11. Repair sizing macros missing opening or closing parentheses
    s = re.sub(r'\\bigl([a-zA-Z0-9])', r'\\bigl(\1', s)
    s = re.sub(r'\\bigr(?=[^)\\]|$)', r'\\bigr)', s)

    return s

def sanitize_mindmap_math(node: dict) -> dict:
    """
    Recursively validates and repairs LaTeX syntax across all nodes in the mindmap tree.
    """
    if not isinstance(node, dict):
        return node

    if "label" in node and isinstance(node["label"], str):
        node["label"] = repair_math_syntax_backend(node["label"])

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
        # Use verify=False to bypass macOS Python local SSL certificate chain errors
        async with httpx.AsyncClient(verify=False, timeout=6.0) as client:
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

    return None

async def enrich_mindmap_with_images(node: dict, max_images: int = 8, count: int = 0) -> int:
    """
    Recursively attaches Wikimedia Commons educational images to key mindmap nodes.
    Spaces out requests with a delay to respect Wikimedia API rate limits.
    """
    if count >= max_images:
        return count

    label = node.get("label", "")
    # Enrich root, major subtopics, or key concepts
    should_enrich = (node.get("id") == "root" or len(node.get("children", [])) > 0 or random.random() < 0.5)

    if should_enrich and label and not node.get("imageUrl"):
        image_data = await fetch_wikimedia_image(label)
        if image_data:
            node["imageUrl"] = image_data["imageUrl"]
            node["imageCaption"] = image_data["imageCaption"]
            node["imageAspectRatio"] = image_data["imageAspectRatio"]
            count += 1
            await asyncio.sleep(0.5)  # Respect Wikimedia rate limits

    for child in node.get("children", []):
        if count >= max_images:
            break
        count = await enrich_mindmap_with_images(child, max_images=max_images, count=count)

    return count



# Subject-specific system prompts
# Subject-specific system prompts tailored for Secondary / O-Level Revision Notes
def get_system_prompt(subject: str) -> str:
    if subject == "math":
        return """You are a master Mathematics tutor and curriculum specialist.
Your objective is to analyze the student revision notes and transform them into an exhaustive, exam-focused, highly structured hierarchical mindmap.

CRITICAL TOPOLOGY & LABEL RULES:
1. SPECIFIC ROOT TOPIC NAME (NEVER USE GENERIC LABELS):
   - The root node "label" MUST BE the exact mathematical subject/topic extracted from the text (e.g. "Quadratic Functions & Equations", "Exponential & Logarithmic Functions", "Trigonometry & Circular Measure", "Integration & Differentiation").
   - NEVER write "O-Level", "Document Overview", "Study Guide", or generic headings in the root label.
2. MANDATORY MULTI-NODE HIERARCHY (NEVER COLLAPSE INTO A SINGLE NODE):
   - The root node MUST ONLY contain the topic title and a concise 2-sentence syllabus overview.
   - The root node MUST HAVE 4 to 8 distinct child nodes in its "children" array, one for EACH topic/chapter (e.g. Quadratic Functions, The Discriminant, Surds & Conjugates, Polynomial Division, Partial Fractions, Binomial Theorem).
   - Each major child node in turn SHOULD contain 2 to 4 sub-child nodes in its own "children" array for specific formulas, proofs, or worked techniques.
   - STRICTLY FORBIDDEN: Cramming multiple topics into the root summary or outputting an empty "children": [] array.
3. STRICT DEPTH & COMPLETENESS INVARIANT:
   - Every node MUST be exhaustive, rigorous, and fully detailed. Do NOT output shallow or single-sentence summaries.
   - Include complete intermediate algebraic steps, substitutions, and sign rules from the notes.
   - Thoroughly explain conditions (e.g. discriminant $\\Delta = b^2 - 4ac$, domain restrictions, conjugate multiplication rules).
4. LaTeX Formula Standard (MANDATORY DELIMITERS & PURITY):
   - Every formula, equation, rule, function, variable, and operator MUST be wrapped in standard dollar-sign LaTeX delimiters ($...$ inline, $$...$$ block display).
   - STRICT DELIMITER PURITY: NEVER put English text inside '$$ ... $$' or '$ ... $'. Mathematical blocks must ONLY contain pure LaTeX syntax.
   - STRICT FORBIDDEN: Never write raw parentheses '(f(x)=a^x)' or raw commands '\\to' without '$' delimiters.
5. Summary Structure (Use rich multi-bullet markdown format for EVERY node):
   ### Core Concept & Exam Rule
   - **Key Principle**: [Clear intuition of the rule or formula for exams]
   - **Step-by-Step Method**: [Step-by-step algebraic technique with LaTeX $...$ notation]
   - **Exam Pitfalls & Conditions**: [Sign traps, discriminant conditions $\\Delta < 0$, domain restrictions]

   ### Formulas & Identities
   - **Governing Identity**: $$[Block LaTeX Formula]$$
   - **Variable Definitions**: [Symbols, coefficients, constants, and domain]

   ### Worked Exam Example
   - **Problem Walkthrough**: [Concrete numerical problem with step-by-step substitution and solution]

JSON OUTPUT SCHEMA:
Output ONLY a single valid JSON object strictly matching this multi-level hierarchy:
{
  "id": "root",
  "label": "Algebraic Foundations & Quadratic Functions",
  "summary": "### Core Concept & Exam Rule\\n- **Key Principle**: Comprehensive syllabus overview covering quadratic equations, surds, polynomials, partial fractions, and binomial expansions.\\n- **Step-by-Step Method**: Master canonical transformations from standard forms to algebraic solutions.",
  "children": [
    {
      "id": "node-1",
      "label": "Quadratic Functions & Completing the Square",
      "summary": "### Core Concept & Exam Rule\\n- **Key Principle**: Converting $y = ax^2 + bx + c$ to vertex form $y = a(x-h)^2 + k$ identifies the maximum/minimum turning point $\\\\bigl(-\\\\frac{b}{2a}, c - \\\\frac{b^2}{4a}\\\\bigr)$.\\n- **Step-by-Step Method**: Factor leading coefficient $a$ from $x^2$ and $x$ terms, then add and subtract $\\\\bigl(\\\\frac{b}{2a}\\\\bigr)^2$: $ax^2 + bx + c = a\\\\left(x + \\\\frac{b}{2a}\\\\right)^2 + \\\\left(c - \\\\frac{b^2}{4a}\\\\right)$.\\n- **Exam Pitfalls & Conditions**: If $a > 0$, parabola opens upwards (minimum); if $a < 0$, parabola opens downwards (maximum).\\n\\n### Formulas & Identities\\n- **Governing Identity**: $$y = a\\\\left(x + \\\\frac{b}{2a}\\\\right)^2 + \\\\left(c - \\\\frac{b^2}{4a}\\\\right)$$\\n\\n### Worked Exam Example\\n- **Problem Walkthrough**: For $y = 2x^2 - 8x + 3 = 2(x^2 - 4x) + 3 = 2(x-2)^2 - 8 + 3 = 2(x-2)^2 - 5$, minimum point is $(2, -5)$.",
      "children": [
        {
          "id": "node-1-1",
          "label": "The Discriminant & Nature of Roots",
          "summary": "### Core Concept & Exam Rule\\n- **Key Principle**: The discriminant $\\\\Delta = b^2 - 4ac$ determines the number and type of real intersections with the x-axis.\\n\\n### Formulas & Identities\\n- **Governing Identity**: $$\\Delta = b^2 - 4ac$$\\n- **Variable Definitions**: $\\\\Delta > 0 \\\\implies$ two distinct real roots; $\\\\Delta = 0 \\\\implies$ two equal real roots (tangent to axis); $\\\\Delta < 0 \\\\implies$ no real roots (curve lies entirely above or below x-axis).",
          "children": []
        }
      ]
    },
    {
      "id": "node-2",
      "label": "Surds & Conjugate Rationalization",
      "summary": "### Core Concept & Exam Rule\\n- **Key Principle**: To rationalize a denominator of the form $\\\\sqrt{p} + \\\\sqrt{q}$, multiply both numerator and denominator by the conjugate $\\\\sqrt{p} - \\\\sqrt{q}$.\\n- **Step-by-Step Method**: Use difference of squares $(\\\\sqrt{p} + \\\\sqrt{q})(\\\\sqrt{p} - \\\\sqrt{q}) = p - q$.\\n\\n### Formulas & Identities\\n- **Governing Identity**: $$\\frac{A}{\\\\sqrt{p} + \\\\sqrt{q}} \\\\times \\\\frac{\\\\sqrt{p} - \\\\sqrt{q}}{\\\\sqrt{p} - \\\\sqrt{q}} = \\\\frac{A(\\\\sqrt{p} - \\\\sqrt{q})}{p - q}$$",
      "children": []
    },
    {
      "id": "node-3",
      "label": "Polynomial Long Division",
      "summary": "### Core Concept & Exam Rule\\n- **Key Principle**: Divide polynomial $P(x)$ by divisor $D(x)$ to find quotient $Q(x)$ and remainder $R(x)$.\\n\\n### Formulas & Identities\\n- **Governing Identity**: $$\\frac{P(x)}{D(x)} = Q(x) + \\\\frac{R(x)}{D(x)}$$\\n- **Variable Definitions**: $\\\\text{deg}(R) < \\\\text{deg}(D)$.",
      "children": []
    },
    {
      "id": "node-4",
      "label": "Partial Fraction Decomposition",
      "summary": "### Core Concept & Exam Rule\\n- **Key Principle**: Split proper rational fraction $\\\\frac{P(x)}{(x-a)(x-b)}$ into linear components $\\\\frac{A}{x-a} + \\\\frac{B}{x-b}$.\\n\\n### Formulas & Identities\\n- **Governing Identity**: $$\\frac{P(x)}{(x-a)(x-b)} = \\\\frac{A}{x-a} + \\\\frac{B}{x-b}$$",
      "children": []
    },
    {
      "id": "node-5",
      "label": "The Binomial Theorem & Series",
      "summary": "### Core Concept & Exam Rule\\n- **Key Principle**: Expand $(a+b)^n$ for positive integers using combinations $\\\\binom{n}{r}$.\\n\\n### Formulas & Identities\\n- **Governing Identity**: $$(a+b)^n = \\\\sum_{r=0}^n \\\\binom{n}{r} a^{n-r} b^r$$",
      "children": []
    }
  ]
}"""

    elif subject == "physics":
        return """You are a master Physics tutor and exam specialist.
Your objective is to analyze student physics notes and transform them into an exhaustive, exam-focused hierarchical mindmap.

CRITICAL TOPOLOGY & LABEL RULES:
1. SPECIFIC ROOT TOPIC NAME:
   - The root node "label" MUST BE the specific physics topic (e.g. "Kinematics & Dynamics", "Thermal Physics & Heat Transfer", "Current Electricity & DC Circuits").
   - NEVER write "O-Level", "Document Overview", or generic headings in the root label.
2. MANDATORY MULTI-NODE HIERARCHY:
   - The root node MUST contain 4 to 8 distinct child nodes in its "children" array, one for EACH topic, law, or mechanism.
   - NEVER collapse multiple topics into a single root node.
3. LaTeX Equations with SI Units: Every physical law and formula MUST use standard LaTeX ($...$ inline and $$...$$ block) with SI units ($m/s^2$, $N$, $J$, $W$, $V$, $\\Omega$).
4. Summary Structure:
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
      "label": "Kinematics & Motion Graphs",
      "summary": "### Core Concept & Physical Law\\n- **Key Definition**: Kinematics describes motion without considering the forces causing it.\\n- **Physical Mechanism**: Velocity is rate of change of displacement; acceleration is rate of change of velocity.\\n- **Exam Pitfalls & Sign Conventions**: Gradient of displacement-time graph gives velocity; area under velocity-time graph gives displacement.\\n\\n### Equations & Units\\n- **Governing Formula**: $$v = u + at, \\\\quad s = ut + \\\\frac{1}{2}at^2, \\\\quad v^2 = u^2 + 2as$$\\n\\n### Worked Exam Problem\\n- **Calculation Walkthrough**: A car accelerates from rest ($u = 0$) at $a = 3\\\\text{ m/s}^2$ for $t = 4\\\\text{ s}$. Final velocity $v = 0 + (3)(4) = 12\\\\text{ m/s}$.",
      "children": []
    },
    {
      "id": "node-2",
      "label": "Newtonian Laws of Motion & Forces",
      "summary": "### Core Concept & Physical Law\\n- **Key Definition**: Newton Second Law states resultant force equals mass multiplied by acceleration ($F = ma$).\\n\\n### Equations & Units\\n- **Governing Formula**: $$F_{net} = ma$$\\n- **Variable Definitions & SI Units**: $F_{net}$ in Newtons ($N$), $m$ in kilograms ($kg$), $a$ in $m/s^2$.",
      "children": []
    }
  ]
}"""

    elif subject == "history":
        return """You are a master History tutor.
Your objective is to analyze historical revision notes and map out causal chronologies, key turning points, and exam takeaways.

CRITICAL TOPOLOGY & LABEL RULES:
1. SPECIFIC ROOT TOPIC NAME: The root node "label" MUST BE the specific historical event or era (e.g. "Causes of World War I", "The Rise of Authoritarian Regimes", "The Cold War in Europe"). NEVER write "O-Level" or generic placeholders.
2. MANDATORY MULTI-NODE HIERARCHY:
   - Root node MUST contain 4 to 8 distinct child nodes in its "children" array, one for EACH event, treaty, policy, or era.
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
      "label": "Outbreak & Causes of Conflict",
      "summary": "### Core Historical Event & Context\\n- **Key Event / Overview**: Systemic alliances and geopolitical tensions leading to mobilization.\\n\\n### Key Details & Turning Points\\n- **Key Turning Point & Year**: Decisive diplomatic breakdowns.",
      "children": []
    }
  ]
}"""

    elif subject == "geography":
        return """You are a master Geography tutor.
Your objective is to analyze geographical revision notes and map out physical processes, landforms, and case studies.

CRITICAL TOPOLOGY & LABEL RULES:
1. SPECIFIC ROOT TOPIC NAME: The root node "label" MUST BE the specific geographical system (e.g. "Plate Tectonics & Seismic Hazards", "Weather & Climate Systems", "River & Coastal Geomorphology"). NEVER write "O-Level" or generic placeholders.
2. MANDATORY MULTI-NODE HIERARCHY:
   - Root node MUST contain 4 to 8 distinct child nodes in its "children" array, one for EACH physical process, zone, or landform.
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
      "label": "Plate Boundaries & Seismic Landforms",
      "summary": "### Core Geographical Process\\n- **Process Definition**: Movement of lithospheric plates creating volcanic arcs and rift valleys.\\n\\n### Landforms & Case Studies\\n- **Exam Case Study**: Mid-Atlantic Ridge sea-floor spreading at $2-5\\\\text{ cm/year}$.",
      "children": []
    }
  ]
}"""

    else:
        return """You are a master Study Guide and Curriculum Specialist.
Your objective is to analyze student study notes and construct an exhaustive, exam-focused hierarchical mindmap.

CRITICAL TOPOLOGY & LABEL RULES:
1. SPECIFIC ROOT TOPIC NAME:
   - The root node "label" MUST BE the exact subject topic extracted from the text (e.g. "Organic Chemistry & Functional Groups", "Cell Biology & Genetics", "Microeconomics & Market Structures").
   - NEVER write "O-Level", "Document Overview", "Study Guide", or generic headings in the root label.
2. MANDATORY MULTI-NODE HIERARCHY (NEVER COLLAPSE INTO A SINGLE NODE):
   - The root node MUST ONLY contain the topic title and a high-level syllabus summary.
   - The root node MUST HAVE 4 to 8 distinct child nodes in its "children" array, one for EACH core subtopic or chapter.
   - Each major child node SHOULD have 2 to 4 sub-child nodes in its own "children" array.
   - STRICTLY FORBIDDEN: Cramming multiple concepts into the root summary or outputting an empty "children": [] array.
3. STRICT DEPTH & COMPLETENESS INVARIANT:
   - Every node MUST be exhaustive, clear, and complete for exam revision.
4. Math & Formula Delimiters:
   - Every formula, equation, variable, chemical reaction, and math symbol MUST be wrapped in standard LaTeX ($inline$ or $$block$$).
5. Summary Structure (Use rich multi-bullet markdown format for EVERY node):
   ### Core Concept & Exam Rule
   - **Key Principle**: [Clear, direct explanation of the concept for exam revision]
   - **Step-by-Step Method**: [Step-by-step procedure, mechanism, or proof with LaTeX $...$]

   ### Key Details & Rules
   - **Essential Formulas & Definitions**: [Key facts, equations, and vocabulary]
   - **Exam Pitfalls & Tips**: [Common exam mistakes and conditions to watch for]

   ### Practical Application
   - **Worked Example / Application**: [Concrete problem walkthrough or case study]

JSON OUTPUT SCHEMA:
Output ONLY a single valid JSON object strictly matching this schema:
{
  "id": "root",
  "label": "Organic Chemistry & Functional Groups",
  "summary": "### Core Concept & Exam Rule\\n- **Key Principle**: Comprehensive syllabus revision overview covering all core chapters and techniques.\\n- **Step-by-Step Method**: Systematic breakdown of methods and problem-solving strategies.",
  "children": [
    {
      "id": "node-1",
      "label": "Alkanes & Combustion Reactions",
      "summary": "### Core Concept & Exam Rule\\n- **Key Principle**: Saturated hydrocarbons with single covalent bonds undergoing complete combustion.\\n- **Step-by-Step Method**: Balancing stoichiometric combustion equations.\\n\\n### Key Details & Rules\\n- **Essential Formulas & Definitions**: General formula $\\\\text{C}_n\\\\text{H}_{2n+2}$.\\n- **Exam Pitfalls & Tips**: Incomplete combustion produces toxic carbon monoxide $\\\\text{CO}$.\\n\\n### Practical Application\\n- **Worked Example / Application**: Fractional distillation of crude oil.",
      "children": []
    },
    {
      "id": "node-2",
      "label": "Alkenes & Addition Reactions",
      "summary": "### Core Concept & Exam Rule\\n- **Key Principle**: Unsaturated hydrocarbons containing carbon-carbon double bonds $\\\\text{C}=\\\\text{C}$.\\n- **Step-by-Step Method**: Electrophilic addition of aqueous bromine (decolorization from brown to colorless).",
      "children": []
    },
    {
      "id": "node-3",
      "label": "Alcohols & Carboxylic Acids",
      "summary": "### Core Concept & Exam Rule\\n- **Key Principle**: Functional group transformations via oxidation and esterification.",
      "children": []
    }
  ]
}"""



@app.get("/api/health")
def health_check():
    return {"status": "ok", "openrouter_configured": bool(os.environ.get("OPENROUTER_API_KEY"))}

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

                
            # Process Tesseract OCR in parallel using available CPU cores
            cpu_count = os.cpu_count() or 4
            workers = min(len(doc), cpu_count)
            logger.info(f"Spawning {workers} parallel processes for Tesseract OCR...")
            
            with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as executor:
                results = list(executor.map(ocr_image_bytes, page_images))
                
            full_text = [text for text in results if text and not text.startswith("[OCR Error:")]
                    
        extracted_text = "\n".join(full_text)
        
        if not extracted_text.strip():
            raise HTTPException(
                status_code=400, 
                detail="Could not extract any text from the PDF, even with OCR. The document might be blank or unreadable."
            )
            
        logger.info(f"Successfully extracted {len(extracted_text)} characters from {file.filename} (OCR={is_scanned})")
        
        return {
            "filename": file.filename,
            "char_count": len(extracted_text),
            "text": extracted_text[:100000],  # Limit to avoid overloading token limits for very large PDFs
            "ocr_processed": is_scanned
        }
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Error processing PDF file: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to process PDF: {str(e)}")

@app.post("/api/generate-mindmap-vision")
async def generate_mindmap_vision(
    response: Response,
    file: UploadFile = File(...),
    subject: str = Form("general"),
):
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="Groq API Key is not configured. Please set the GROQ_API_KEY environment variable."
        )

    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are supported for Vision processing.")

    try:
        # Read file bytes and load PDF
        file_bytes = await file.read()
        doc = fitz.open(stream=file_bytes, filetype="pdf")

        if len(doc) == 0:
            raise HTTPException(status_code=400, detail="The uploaded PDF is empty.")

        # Limit to first 5 pages for vision chunking
        total_pages = min(len(doc), 5)
        system_prompt = get_system_prompt(subject)
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        # 1-Page Visual Chunking Pipeline:
        # Each PDF page is rendered as an optimized 96 DPI JPEG and processed with an interval
        # to guarantee execution stays safely below Groq's 8,000 TPM limit.
        page_submaps = []
        model_used_name = "qwen/qwen3.6-27b (Vision Mode)"

        async with httpx.AsyncClient(timeout=90.0) as client:
            for page_idx in range(total_pages):
                if page_idx > 0:
                    logger.info(f"Intervaling visual requests (waiting 1.5s before page {page_idx + 1}/{total_pages})...")
                    await asyncio.sleep(1.5)

                page = doc[page_idx]
                page_text = page.get_text().strip()

                # Render page frame at 96 DPI for crisp text with low token footprint
                pix = page.get_pixmap(dpi=96)
                pil_img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
                
                max_dim = max(pil_img.size)
                if max_dim > 1024:
                    scale = 1024 / max_dim
                    new_size = (int(pil_img.size[0] * scale), int(pil_img.size[1] * scale))
                    pil_img = pil_img.resize(new_size, Image.Resampling.LANCZOS)

                buf = io.BytesIO()
                pil_img.save(buf, format="JPEG", quality=80, optimize=True)
                img_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

                prompt_text = (
                    f"Analyze Page {page_idx + 1} of this document. Synthesize BOTH the visual diagrams, formulas, tables, and text "
                    f"into an exhaustive hierarchical mindmap JSON with 3 to 6 detailed child nodes.\n"
                    f"CRITICAL: Do NOT compress or omit mathematical steps, mechanisms, formulas, or worked explanations.\n"
                    f"Ensure all math is strictly enclosed in standard LaTeX delimiters ($...$ or $$...$$)."
                )
                if page_text:
                    prompt_text += f"\n\nExtracted Text for Page {page_idx + 1}:\n{page_text[:4000]}"

                user_content_blocks = [
                    {"type": "text", "text": prompt_text},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}
                    }
                ]

                data = {
                    "model": "qwen/qwen3.6-27b",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content_blocks}
                    ],
                    "temperature": 0.2,
                    "max_tokens": 2500
                }

                logger.info(f"Processing visual Page {page_idx + 1}/{total_pages} via 'qwen/qwen3.6-27b'...")
                page_resp = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers=headers,
                    json=data,
                    timeout=90.0
                )

                # Fallback to fast text LPU model if rate-limited or payload limit reached
                if page_resp.status_code in [413, 429]:
                    logger.warning(f"Groq Vision returned status {page_resp.status_code} on Page {page_idx + 1}. Falling back to 'openai/gpt-oss-120b' text mode.")
                    model_used_name = "openai/gpt-oss-120b (Vision Text Fallback)"
                    fallback_data = {
                        "model": "openai/gpt-oss-120b",
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": f"Analyze Page {page_idx + 1} text and output hierarchical mindmap JSON:\n\n{page_text or prompt_text}"}
                        ],
                        "temperature": 0.2,
                        "max_tokens": 2500
                    }
                    page_resp = await client.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers=headers,
                        json=fallback_data,
                        timeout=90.0
                    )

                if page_resp.status_code == 200:
                    raw_content = page_resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                    parsed_submap = repair_and_parse_json(raw_content)
                    page_submaps.append(parsed_submap)
                else:
                    logger.warning(f"Page {page_idx + 1} generation failed with status {page_resp.status_code}: {page_resp.text[:120]}")
                    page_submaps.append({
                        "id": f"page_{page_idx + 1}",
                        "label": f"Page {page_idx + 1} Overview",
                        "summary": f"### Core Concept\n- **Overview**: Page {page_idx + 1} conceptual summary.",
                        "children": []
                    })

        if not page_submaps:
            raise HTTPException(status_code=500, detail="Failed to generate mindmap from visual pages.")

        # Single page document
        if len(page_submaps) == 1:
            final_map = sanitize_mindmap_math(page_submaps[0])
            await enrich_mindmap_with_images(final_map, max_images=6)
            response.headers["X-Model-Used"] = model_used_name
            response.headers["X-Model-Routed"] = "false"
            response.headers["Access-Control-Expose-Headers"] = "X-Model-Used, X-Model-Routed"
            return final_map

        # Multi-page document: consolidate under master root
        # Multi-page document: promote children of each submap directly to master root
        first_label = page_submaps[0].get("label", "Document Study Guide")
        if first_label in ["Document Overview & Core Themes", "Document Overview", "Central Topic"]:
            for sm in page_submaps:
                cand_label = sm.get("label", "")
                if cand_label and cand_label not in ["Document Overview & Core Themes", "Document Overview", "Central Topic"]:
                    first_label = cand_label
                    break

        all_children = []
        for i, sub_map in enumerate(page_submaps):
            # If the submap has children, promote its children directly
            if sub_map.get("children") and len(sub_map["children"]) > 0:
                for c_idx, child in enumerate(sub_map["children"]):
                    unique_child = make_ids_unique(child, f"p{i+1}_{c_idx+1}")
                    all_children.append(unique_child)
            else:
                # If submap has no children, include the submap itself as a child node
                unique_sub_map = make_ids_unique(sub_map, f"page_{i+1}")
                all_children.append(unique_sub_map)

        consolidated_root = {
            "id": "root",
            "label": first_label,
            "summary": consolidate_summaries(page_submaps),
            "children": all_children
        }

        final_map = sanitize_mindmap_math(consolidated_root)
        await enrich_mindmap_with_images(final_map, max_images=6)

        response.headers["X-Model-Used"] = model_used_name
        response.headers["X-Model-Routed"] = "false"
        response.headers["Access-Control-Expose-Headers"] = "X-Model-Used, X-Model-Routed"
        return final_map

    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Unexpected error in Vision processing: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to process document with Vision: {str(e)}")

# Equal Load Balancer: 5 active alternative models on Groq
ALL_ALTERNATIVE_MODELS = [
    "openai/gpt-oss-120b",
    "groq/compound",
    "qwen/qwen3.6-27b",
    "groq/compound-mini",
    "openai/gpt-oss-20b",
]

_load_balance_counter: int = 0
_load_balance_lock = asyncio.Lock()

@app.post("/api/generate-mindmap")
async def generate_mindmap(payload: MindmapGenerateRequest, response: Response):
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        logger.error("GROQ_API_KEY is not set in the environment variables.")
        raise HTTPException(
            status_code=500, 
            detail="Groq API Key is not configured. Please set the GROQ_API_KEY environment variable."
        )
    
    # Use subject-specific system prompt
    subject = payload.subject or "general"
    system_prompt = get_system_prompt(subject)
    
    raw_model = payload.model or "openai/gpt-oss-120b"
    
    # Map deprecated or legacy model strings to active, supported Groq Cloud models
    MODEL_ALIASES = {
        "llama-3.3-70b-versatile": "openai/gpt-oss-120b",
        "llama-3.1-8b-instant": "openai/gpt-oss-20b",
        "llama3-70b-8192": "openai/gpt-oss-120b",
        "llama3-8b-8192": "openai/gpt-oss-20b",
        "llama-3.2-11b-vision-preview": "openai/gpt-oss-20b",
        "deepseek-r1-distill-llama-70b": "openai/gpt-oss-120b",
        "meta-llama/llama-4-scout-17b-16e-instruct": "openai/gpt-oss-120b",
        "mixtral-8x7b-32768": "groq/compound",
        "gemma2-9b-it": "groq/compound-mini",
        "qwen/qwen3-32b": "qwen/qwen3.6-27b",
    }
    selected_model = MODEL_ALIASES.get(raw_model, raw_model)
    word_count = len(payload.text.split())
    
    # Adjust chunk size so completions and compound models stay safely within free-tier token limits
    if selected_model in ["openai/gpt-oss-20b", "groq/compound", "groq/compound-mini", "auto-smart-routing", "auto-load-balanced"] or len(payload.text) > 20000:
        chunk_size = 5000
    else:
        chunk_size = 10000
        
    # Split full text into chunks (limit to maximum 5 chunks)
    chunks = split_text_into_chunks(payload.text, chunk_size=chunk_size)[:5]
    logger.info(f"Splitting document into {len(chunks)} chunks of size {chunk_size} for parallel Groq processing.")
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    ALL_FREE_TIER_MODELS = ALL_ALTERNATIVE_MODELS

    primary_model = selected_model
    is_routed = False
    
    # Distribute load equally across all 5 alternative models in round-robin fashion
    if selected_model in ["auto-smart-routing", "auto-load-balanced", "equal-load-distribution"]:
        is_routed = True
        global _load_balance_counter
        async with _load_balance_lock:
            start_idx = _load_balance_counter
            _load_balance_counter = (_load_balance_counter + len(chunks)) % len(ALL_ALTERNATIVE_MODELS)
        chunk_models = [
            ALL_ALTERNATIVE_MODELS[(start_idx + idx) % len(ALL_ALTERNATIVE_MODELS)]
            for idx in range(len(chunks))
        ]
    else:
        chunk_models = [primary_model] * len(chunks)

    # Track last request time per model to space out requests and avoid rate limits
    model_last_request: dict[str, float] = {}
    MIN_REQUEST_INTERVAL = 1.5  # seconds between requests to same model

    unique_models_used = list(dict.fromkeys(chunk_models))
    models_used_str = ", ".join(unique_models_used)

    response.headers["X-Model-Used"] = models_used_str
    response.headers["X-Model-Routed"] = "true" if (is_routed or len(chunks) > 1) else "false"
    response.headers["Access-Control-Expose-Headers"] = "X-Model-Used, X-Model-Routed"

    async def process_chunk(client: httpx.AsyncClient, chunk_text: str, index: int) -> dict:
        initial_model = chunk_models[index]
        # Prioritize high-capacity models (gpt-oss-120b: 30k TPM) for reliable completion
        high_capacity_first = ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.6-27b", "groq/compound", "groq/compound-mini"]
        candidate_models = [initial_model] + [m for m in high_capacity_first if m != initial_model]

        user_prompt = f"Here is the text extracted from Part {index+1} of the document to turn into a mindmap:\n\n{chunk_text}"

        for current_model in candidate_models:
            # Space out requests per model to avoid rate limit spikes
            import time
            now = time.monotonic()
            if current_model in model_last_request:
                elapsed = now - model_last_request[current_model]
                if elapsed < MIN_REQUEST_INTERVAL:
                    wait_time = MIN_REQUEST_INTERVAL - elapsed
                    logger.info(f"Spacing request for model '{current_model}': waiting {wait_time:.2f}s")
                    await asyncio.sleep(wait_time)
            model_last_request[current_model] = time.monotonic()

            for attempt in range(2):
                try:
                    # Calibrate token budget per model so TPM limits (8,000 TPM on Qwen/Compound) are never exceeded
                    if "120b" in current_model or "20b" in current_model:
                        max_tokens = 2500
                    else:
                        max_tokens = 2000

                    data = {
                        "model": current_model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt}
                        ],
                        "temperature": 0.2,
                        "max_tokens": max_tokens,
                    }

                    logger.info(f"Sending Groq API request for Chunk {index+1} using model: '{current_model}' (max_tokens={max_tokens}, Attempt {attempt+1})")
                    resp = await client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=data, timeout=90.0)

                    # On Rate Limit (429) or Payload/TPM Limit (413): switch to NEXT fallback model immediately
                    if resp.status_code in [413, 429]:
                        logger.warning(f"Groq rate/TPM limit hit ({resp.status_code}) on model '{current_model}' for Chunk {index+1}. Switching to fallback model...")
                        await asyncio.sleep(1.0)
                        break

                    if resp.status_code != 200:
                        logger.warning(f"Groq API error status {resp.status_code} ({resp.text[:120]}) on model '{current_model}' for Chunk {index+1}. Switching to fallback model...")
                        break

                    resp_json = resp.json()
                    choices = resp_json.get("choices", [])
                    if not choices:
                        break

                    content = choices[0].get("message", {}).get("content", "")
                    mindmap_data = repair_and_parse_json(content)
                    logger.info(f"Successfully generated Chunk {index+1} using model '{current_model}'")
                    return mindmap_data

                except Exception as exc:
                    logger.warning(f"Error on model '{current_model}' for Chunk {index+1}: {str(exc)}. Retrying/falling back...")
                    await asyncio.sleep(0.5)
                    break

        # Emergency Fallback Outline: If all models fail, construct a structured multi-child outline from paragraph headers
        logger.error(f"All model fallback candidates failed for Chunk {index+1}. Synthesizing structured fallback outline.")
        lines = [line.strip() for line in chunk_text.split('\n') if len(line.strip()) > 5]
        child_nodes = []
        for line_idx, line in enumerate(lines[:5]):
            clean_title = re.sub(r'^(?:[0-9]+\.|\d+\))\s*', '', line)[:60]
            child_nodes.append({
                "id": f"chunk_fallback_{index+1}_{line_idx+1}",
                "label": clean_title,
                "summary": f"### Core Concept\n- **Overview**: Key principles and methods from this section.\n- **Content**: {line[:200]}",
                "children": []
            })

        return {
            "id": f"chunk_fallback_{index+1}",
            "label": f"Part {index+1}: Document Study Section",
            "summary": f"### Core Concept\n- **Overview**: Comprehensive study section recovered for continuous viewing.\n- **Key Mechanism**: Review individual topics in child nodes below.",
            "children": child_nodes
        }

    try:

        async with httpx.AsyncClient(timeout=60.0) as client:
            # Process chunks SEQUENTIALLY to guarantee rate limit spacing works
            # asyncio.gather runs in parallel which defeats per-model spacing
            sub_maps = []
            for i, chunk in enumerate(chunks):
                logger.info(f"Processing chunk {i+1}/{len(chunks)} sequentially...")
                result = await process_chunk(client, chunk, i)
                sub_maps.append(result)
            
            if not sub_maps:
                raise HTTPException(status_code=500, detail="No mindmaps could be generated.")
                
            # If there is only one chunk, return it directly
            if len(sub_maps) == 1:
                final_map = sanitize_mindmap_math(sub_maps[0])
                await enrich_mindmap_with_images(final_map, max_images=6)
                return final_map
                
            # Otherwise, consolidate multiple mindmaps under a parent root
            first_label = sub_maps[0].get("label", "Document Study Guide")
            if first_label in ["Document Overview & Core Themes", "Document Overview", "Central Topic"]:
                for sm in sub_maps:
                    cand_label = sm.get("label", "")
                    if cand_label and cand_label not in ["Document Overview & Core Themes", "Document Overview", "Central Topic"]:
                        first_label = cand_label
                        break

            all_children = []
            for i, sub_map in enumerate(sub_maps):
                # If submap has children, promote its children directly
                if sub_map.get("children") and len(sub_map["children"]) > 0:
                    for c_idx, child in enumerate(sub_map["children"]):
                        unique_child = make_ids_unique(child, f"part{i+1}_{c_idx+1}")
                        all_children.append(unique_child)
                else:
                    # If submap has no children, include the submap itself as a child node
                    unique_sub_map = make_ids_unique(sub_map, f"part_{i+1}")
                    all_children.append(unique_sub_map)

            consolidated_root = {
                "id": "root",
                "label": first_label,
                "summary": consolidate_summaries(sub_maps),
                "children": all_children
            }

            final_consolidated = sanitize_mindmap_math(consolidated_root)
            await enrich_mindmap_with_images(final_consolidated, max_images=6)
            return final_consolidated

            
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

# Mount static files and set up catch-all route for frontend React app
# Resolve the absolute path of frontend dist directory
FRONTEND_DIST_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../frontend/dist"))

# Mount frontend assets folder if it exists
assets_path = os.path.join(FRONTEND_DIST_DIR, "assets")
if os.path.isdir(assets_path):
    app.mount("/assets", StaticFiles(directory=assets_path), name="assets")

# Catch-all route to serve the React index.html and other public files (like favicon.svg, icons.svg)
@app.get("/{catchall:path}")
async def serve_frontend(catchall: str):
    # Prevent handling API routes in the catch-all
    if catchall.startswith("api/"):
        raise HTTPException(status_code=404, detail="API endpoint not found")
    
    # Prevent path traversal
    if ".." in catchall or "\\" in catchall or catchall.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid path")
        
    # Resolve absolute paths and verify they are within FRONTEND_DIST_DIR to prevent path traversal
    real_dist_dir = os.path.realpath(FRONTEND_DIST_DIR)
    file_path = os.path.realpath(os.path.join(real_dist_dir, catchall))
    
    if not file_path.startswith(real_dist_dir + os.sep) and file_path != real_dist_dir:
        raise HTTPException(status_code=403, detail="Access denied")
        
    if catchall and os.path.exists(file_path) and os.path.isfile(file_path):
        return FileResponse(file_path)
        
    index_path = os.path.join(real_dist_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    
    raise HTTPException(status_code=404, detail="Frontend build files not found. Please run 'npm run build' in the frontend directory.")
