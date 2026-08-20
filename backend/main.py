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
    core_concepts = []
    examples = []
    
    for i, sub_map in enumerate(sub_maps):
        part_name = f"Part {i+1}"
        summary_text = sub_map.get("summary", "")
        
        # Extract Core Concept section
        concept_match = re.search(r"### Core Concept\s*\n(.*?)(?=\n###|$)", summary_text, re.DOTALL)
        if concept_match:
            concept_content = concept_match.group(1).strip()
            # Clean up leading dashes or bullet points and format nicely
            clean_lines = []
            for line in concept_content.split('\n'):
                line = line.strip()
                if line.startswith('-'):
                    line = line[1:].strip()
                if line:
                    clean_lines.append(line)
            if clean_lines:
                core_concepts.append(f"- **{part_name}**: {'; '.join(clean_lines)}")
        
        # Extract Examples section
        examples_match = re.search(r"### Examples\s*\n(.*?)(?=\n###|$)", summary_text, re.DOTALL)
        if examples_match:
            examples_content = examples_match.group(1).strip()
            clean_lines = []
            for line in examples_content.split('\n'):
                line = line.strip()
                if line.startswith('-'):
                    line = line[1:].strip()
                if line:
                    clean_lines.append(line)
            if clean_lines:
                examples.append(f"- **{part_name}**: {'; '.join(clean_lines)}")
                
    # If we couldn't parse anything, return a fallback
    if not core_concepts:
        return (
            "### Core Concept\n- Consolidated study guide covering all parts of the document.\n\n"
            "### Examples\n- Multi-part document processing.\n\n"
            "### Connection\n- Master consolidated topic map."
        )
        
    core_concept_str = "\n".join(core_concepts)
    examples_str = "\n".join(examples) if examples else "- Key examples are detailed in the respective sub-sections."
    
    return (
        f"### Core Concept\n{core_concept_str}\n\n"
        f"### Examples\n{examples_str}\n\n"
        f"### Connection\n- Merges all sections into a comprehensive study overview."
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
            if "label" not in data: data["label"] = "Document Overview"
            if "summary" not in data: data["summary"] = "### Core Concept\n- **Overview**: Comprehensive concept breakdown."
            if "children" in data and isinstance(data["children"], list) and len(data["children"]) > 0:
                return data
    except Exception as e:
        logger.warning(f"JSON auto-repair parsing warning: {str(e)}")

    # 3. Robust Regex Block Extractor (Extracts root node AND all child objects so children are never lost)
    root_label_match = re.search(r'"label"\s*:\s*"([^"]+)"', cleaned)
    root_label = root_label_match.group(1) if root_label_match else "Document Overview & Core Themes"

    root_summary_match = re.search(r'"summary"\s*:\s*"((?:[^"\\]|\\.)*)"', cleaned)
    root_summary = root_summary_match.group(1).replace('\\n', '\n').replace('\\"', '"').replace('\\\\', '\\') if root_summary_match else "Overview of core concepts and mechanisms."

    # Extract all child nodes by scanning for node objects
    extracted_children = []
    child_pattern = re.compile(
        r'\{\s*"id"\s*:\s*"([^"]+)"\s*,\s*"label"\s*:\s*"([^"]+)"\s*,\s*"summary"\s*:\s*"((?:[^"\\]|\\.)*)"',
        re.DOTALL
    )

    seen_ids = set()
    for match in child_pattern.finditer(cleaned):
        cid, clabel, csummary = match.groups()
        if cid == "root" or cid in seen_ids or clabel == root_label:
            continue
        seen_ids.add(cid)
        clean_sum = csummary.replace('\\n', '\n').replace('\\"', '"').replace('\\\\', '\\')
        extracted_children.append({
            "id": cid,
            "label": clabel,
            "summary": clean_sum,
            "children": []
        })

    return {
        "id": "root",
        "label": root_label,
        "summary": root_summary,
        "children": extracted_children
    }

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
def get_system_prompt(subject: str) -> str:
    if subject == "math":
        return """You are a distinguished Mathematics Professor and master curriculum designer.
Your objective is to analyze the mathematical text and transform it into an in-depth, rigorous, highly comprehensive hierarchical mindmap.

CRITICAL MATHEMATICAL & TOPOLOGY RULES:
1. MANDATORY MULTI-NODE HIERARCHY (NEVER COLLAPSE INTO A SINGLE NODE):
   - The root node MUST ONLY contain the overarching document title and a high-level thesis summary.
   - The root node MUST HAVE 4 to 8 distinct child nodes in its "children" array, one for EACH core topic, chapter, technique, or formula group.
   - Each major child node in turn SHOULD contain 2 to 4 sub-child nodes in its own "children" array for specific proofs, formulas, or applications.
   - STRICTLY FORBIDDEN: Cramming multiple topics into the root summary or outputting an empty "children": [] array.
2. STRICT DEPTH & COMPLETENESS INVARIANT (NEVER COMPROMISE ON CONTENT):
   - Every node MUST be exhaustive, rigorous, and fully detailed. Do NOT output shallow, abbreviated, or single-sentence summaries.
   - NEVER cut short, compress, or omit mathematical steps, intermediate algebra, derivations, proofs, boundary conditions, or worked calculations.
   - Thoroughly explain underlying derivations, geometric intuition, algebraic conditions (e.g. domain restrictions, discriminant $\\Delta = b^2 - 4ac$, asymptotes, boundary cases), and step-by-step calculation workflows from the text.
   - Include complete concrete numerical worked examples showing every intermediate algebraic step and substitution.
3. LaTeX Formula Standard (MANDATORY DELIMITERS & PURITY):
   - Every formula, equation, rule, function, variable, derivative, and operator MUST be wrapped in standard dollar-sign LaTeX delimiters:
     * Inline expressions: $f(x) = a^x$, $a > 1$, $0 < a < 1$, $a^x = e^{x\\ln a}$, $\\frac{d}{dx}[a^x] = a^x\\ln a$, $e^{rt}$, $\\to$, $\\log_a(x/y) = \\log_a x - \\log_a y$, $\\int_a^b f(x)dx$, $\\Delta = b^2 - 4ac$
     * Block display equations: $$\\log_a\\left(\\frac{x}{y}\\right) = \\log_a x - \\log_a y$$ or $$x = \\frac{-b \\pm \\sqrt{b^2 - 4ac}}{2a}$$
   - STRICT DELIMITER PURITY RULE:
     * NEVER put English sentences, commentary, or step descriptions inside '$$ ... $$' or '$ ... $'. Mathematical blocks must ONLY contain pure LaTeX syntax. English commentary must be written outside math delimiters as regular markdown text (e.g. write 'Take square roots and solve for $x$:\n$$x = \\frac{-b \\pm \\sqrt{b^2-4ac}}{2a}$$').
   - STRICT FORBIDDEN PATTERNS:
     * NEVER wrap math formulas in regular parentheses like '(f(x)=a^x)' or '((e^{rt}))' or '(a>1)' — ALWAYS write '$f(x)=a^x$', '$e^{rt}$', '$a>1$'.
     * NEVER leave raw LaTeX commands like '\\to', '\\frac', '\\ln' without '$' delimiters — ALWAYS write '$\\to$', '$\\frac{...}{...}$', '$\\ln a$'.
4. Summary Structure (Use rich multi-bullet markdown format for EVERY node):
   ### Core Mathematical Concept
   - **Mathematical Principle**: [Comprehensive 2-3 sentence intuition of the theorem, logarithm rule, or principle]
   - **Key Mechanism & Derivation**: [Step-by-step mathematical logic, algebraic derivation, or proof breakdown with $...$ delimiters]
   - **Conditions & Edge Cases**: [Domain restrictions, singular points, discriminant conditions]

   ### Formulas, Derivations & Variables
   - **Primary Formulation**: $$[Block LaTeX Formula]$$
   - **Variable & Symbol Definitions**: [Detailed breakdown of symbols: $x$, $y$, base $a$, coefficients, constants, domain]
   - **Key Identities & Equivalent Forms**: [Alternative forms, quotient/product rules, change of base, factored representations]

   ### Worked Problem & Practical Application
   - **Step-by-Step Worked Example**: [Concrete numerical walkthrough showing algebraic substitution and final solution]
   - **Real-World / Scientific Application**: [Concrete engineering, optimization, computing, or geometric application]

JSON OUTPUT SCHEMA:
Output ONLY a single valid JSON object strictly matching this multi-level hierarchy:
{
  "id": "root",
  "label": "Algebraic Structures & Advanced Methods",
  "summary": "### Core Concept\\n- **Main Thesis**: Comprehensive framework covering quadratic analysis, polynomial algebra, rational decomposition, and series expansions.\\n- **Key Mechanism**: Systematic algebraic transformation from canonical forms to analytical solutions.",
  "children": [
    {
      "id": "node-1",
      "label": "Quadratic Functions & Completing the Square",
      "summary": "### Core Mathematical Concept\\n- **Mathematical Principle**: Converting $y = ax^2 + bx + c$ to vertex form $y = a(x-h)^2 + k$ reveals parabolic symmetry and extremum.\\n- **Key Mechanism & Derivation**: Factor leading coefficient and complete square: $ax^2 + bx = a\\\\bigl(x + \\\\frac{b}{2a}\\\\bigr)^2 - \\\\frac{b^2}{4a}$.\\n\\n### Formulas, Derivations & Variables\\n- **Primary Formulation**: $$y = a\\\\left(x + \\\\frac{b}{2a}\\\\right)^2 + \\\\left(c - \\\\frac{b^2}{4a}\\\\right)$$\\n- **Variable & Symbol Definitions**: $a \\\\neq 0$ controls curvature, vertex at $\\\\bigl(-\\\\frac{b}{2a}, c - \\\\frac{b^2}{4a}\\\\bigr)$.",
      "children": [
        {
          "id": "node-1-1",
          "label": "Discriminant & Nature of Roots",
          "summary": "### Core Mathematical Concept\\n- **Mathematical Principle**: Discriminant $\\\\Delta = b^2 - 4ac$ categorizes polynomial roots without full solution.\\n\\n### Formulas, Derivations & Variables\\n- **Primary Formulation**: $$\\Delta = b^2 - 4ac$$\\n- **Variable & Symbol Definitions**: $\\\\Delta > 0 \\\\implies$ two real roots; $\\\\Delta = 0 \\\\implies$ repeated root; $\\\\Delta < 0 \\\\implies$ complex roots.",
          "children": []
        }
      ]
    },
    {
      "id": "node-2",
      "label": "Surds & Conjugate Rationalization",
      "summary": "### Core Mathematical Concept\\n- **Mathematical Principle**: Eliminating irrational denominators by multiplying numerator and denominator by algebraic conjugates.\\n- **Key Mechanism & Derivation**: Utilizing the difference of squares identity $(a - b)(a + b) = a^2 - b^2$.\\n\\n### Formulas, Derivations & Variables\\n- **Primary Formulation**: $$\\frac{1}{\\\\sqrt{a} + \\\\sqrt{b}} = \\\\frac{\\\\sqrt{a} - \\\\sqrt{b}}{a - b}$$",
      "children": []
    },
    {
      "id": "node-3",
      "label": "Polynomial Long Division",
      "summary": "### Core Mathematical Concept\\n- **Mathematical Principle**: Decomposing rational expressions into polynomial quotient plus proper fractional remainder.\\n\\n### Formulas, Derivations & Variables\\n- **Primary Formulation**: $$\\frac{P(x)}{D(x)} = Q(x) + \\\\frac{R(x)}{D(x)}$$\\n- **Variable & Symbol Definitions**: $\\\\text{deg}(R) < \\\\text{deg}(D)$.",
      "children": []
    },
    {
      "id": "node-4",
      "label": "Partial Fraction Decomposition",
      "summary": "### Core Mathematical Concept\\n- **Mathematical Principle**: Splitting complex rational functions into sums of simpler linear/quadratic denominators for integration and control theory.\\n\\n### Formulas, Derivations & Variables\\n- **Primary Formulation**: $$\\frac{P(x)}{(x-a)(x-b)} = \\\\frac{A}{x-a} + \\\\frac{B}{x-b}$$",
      "children": []
    },
    {
      "id": "node-5",
      "label": "The Binomial Theorem & Series",
      "summary": "### Core Mathematical Concept\\n- **Mathematical Principle**: Expanding powers of binomial expressions into finite polynomial series or infinite Newton series.\\n\\n### Formulas, Derivations & Variables\\n- **Primary Formulation**: $$(a+b)^n = \\\\sum_{k=0}^n \\\\binom{n}{k} a^{n-k} b^k$$",
      "children": []
    }
  ]
}"""

    elif subject == "physics":
        return """You are an elite Physics Professor and master STEM curriculum designer.
Your objective is to analyze the physics text and convert it into an in-depth, rigorous, highly comprehensive hierarchical mindmap.

CRITICAL PHYSICS & TOPOLOGY RULES:
1. MANDATORY MULTI-NODE HIERARCHY (NEVER COLLAPSE INTO A SINGLE NODE):
   - The root node MUST ONLY contain the overarching document title and a high-level thesis summary.
   - The root node MUST HAVE 4 to 8 distinct child nodes in its "children" array, one for EACH physical law, topic, or system component.
   - Each major child node SHOULD have 2 to 4 sub-child nodes in its own "children" array.
   - STRICTLY FORBIDDEN: Cramming multiple concepts into the root summary or outputting an empty "children": [] array.
2. STRICT DEPTH & COMPLETENESS INVARIANT (NEVER COMPROMISE ON CONTENT):
   - Every node MUST be exhaustive, rigorous, and fully detailed. Do NOT output shallow, abbreviated, or single-sentence summaries.
   - NEVER cut short or omit physical derivations, vector breakdowns, dimensional analyses, or worked calculation steps.
   - Thoroughly explain first principles, force interactions, energy transfers, vector directions, conservation laws, and mathematical derivations from the text.
3. LaTeX Equations with SI Units: Every physical law, kinematics equation, or field formula MUST use standard LaTeX ($inline$ and $$block$$) accompanied by explicit SI units ($m/s^2$, $N$, $J$, $W$, $V$, $\\Omega$).
   - NEVER wrap equations in regular parentheses like '(v=u+at)' — ALWAYS use '$v = u + at$'.
   - NEVER leave raw backslashed commands like '\\to', '\\approx' outside of '$' delimiters.
4. Summary Structure (Use rich multi-bullet markdown format for EVERY node):
   ### Core Physical Principle
   - **Physical Principle**: [Comprehensive 2-3 sentence intuition of the law or physical mechanism]
   - **Underlying Mechanism**: [Force interaction, momentum transfer, molecular process, or field dynamics]
   - **Conservation & Invariance**: [Energy conservation, momentum balance, frame of reference considerations]

   ### Governing Equations & Units
   - **Governing Law**: $$[Block LaTeX Formula]$$
   - **Variable Definitions & SI Units**: [Symbols, physical constants ($g = 9.81\\text{ m/s}^2$), and explicit SI units]
   - **Dimensional Analysis**: [Dimensional consistency breakdown, e.g. $[F] = [M L T^{-2}]$]

   ### Real-World Phenomenon & Application
   - **Experimental Setup & Application**: [Real-world engineering, laboratory measurement, or technological application]
   - **Step-by-Step Worked Problem**: [Concrete numerical problem with step-by-step algebraic solution and unit analysis]

JSON OUTPUT SCHEMA:
Output ONLY a single valid JSON object strictly matching this multi-level hierarchy:
{
  "id": "root",
  "label": "Classical Mechanics & Kinematics",
  "summary": "### Core Physical Principle\\n- **Physical Principle**: Comprehensive overview of Newtonian mechanics, vector kinematics, and conservation laws.\\n- **Underlying Mechanism**: Gravitational and contact force interactions governing motion.",
  "children": [
    {
      "id": "node-1",
      "label": "Projectile Motion & Trajectory",
      "summary": "### Core Physical Principle\\n- **Physical Principle**: Projectile motion combines uniform horizontal velocity with accelerated vertical motion under gravity.\\n- **Underlying Mechanism**: Gravitational force acts downward ($F_g = mg$), producing constant downward acceleration $-g$, while horizontal acceleration is zero ($a_x = 0$).\\n\\n### Governing Equations & Units\\n- **Governing Law**: $$y(t) = v_0 t \\\\sin\\\\theta - \\\\frac{1}{2}gt^2$$\\n- **Variable Definitions & SI Units**: $v_0$ is initial velocity ($m/s$), $\\\\theta$ is launch angle ($^{\\\\circ}$), $g = 9.81\\\\text{ m/s}^2$.\\n\\n### Real-World Phenomenon & Application\\n- **Step-by-Step Worked Problem**: For $v_0 = 20\\\\text{ m/s}, \\\\theta = 45^{\\\\circ}$, $R = \\\\frac{v_0^2 \\\\sin(2\\\\theta)}{g} = \\\\frac{400}{9.81} \\\\approx 40.77\\\\text{ m}$.",
      "children": []
    },
    {
      "id": "node-2",
      "label": "Newtonian Laws of Motion",
      "summary": "### Core Physical Principle\\n- **Physical Principle**: Force produces acceleration inversely proportional to mass ($\\\\vec{F}_{net} = m\\\\vec{a}$).\\n\\n### Governing Equations & Units\\n- **Governing Law**: $$\\sum \\\\vec{F} = m\\\\frac{d^2\\\\vec{r}}{dt^2}$$",
      "children": []
    }
  ]
}"""

    elif subject == "history":
        return """You are an expert historian and educational mindmap designer.
Your objective is to analyze historical text and map out causal backbones, actor rationale, and ripple effects.

CRITICAL TOPOLOGY & HIERARCHY RULES:
1. MANDATORY MULTI-NODE HIERARCHY:
   - The root node MUST contain 4 to 8 distinct child nodes in its "children" array, one for EACH era, turning point, or thematic driver.
   - NEVER collapse multiple events into a single node or leave "children": [] empty.
2. CAUSAL LOGIC: Organize hierarchy chronologically and causally (Root Cause → Trigger → Event → Immediate Outcome → Long-term Impact).
3. Summary Structure:
   ### Core Concept
   - **Historical Thesis**: [Core historical takeaway and context]
   - **Causal Driver**: [Why and how key events unfolded]

   ### Key Details & Turning Points
   - **Key Turning Point**: [Year/Date + decisive event + outcome]
   - **Actors & Factions**: [Motivations and policies]

   ### Physical Meaning & Application
   - **Historical Significance**: [Long-term legacy and modern relevance]

JSON OUTPUT SCHEMA:
Output ONLY a single valid JSON object strictly matching this schema:
{
  "id": "root",
  "label": "Industrial Revolution & Modern Economic Shifts",
  "summary": "### Core Concept\\n- **Historical Thesis**: The transition to mechanized manufacturing fundamentally restructured global economics, urbanization, and labor systems.",
  "children": [
    {
      "id": "node-1",
      "label": "Technological Catalysts & Steam Power",
      "summary": "### Core Concept\\n- **Historical Thesis**: James Watt steam engine innovation catalyzed factory automation and mining expansion.\\n\\n### Key Details & Turning Points\\n- **Key Turning Point**: 1769 patent transformation of textile mills.",
      "children": []
    },
    {
      "id": "node-2",
      "label": "Urbanization & Labor Movements",
      "summary": "### Core Concept\\n- **Historical Thesis**: Rapid urban migration created dense factory towns and catalyzed early trade unionism.",
      "children": []
    }
  ]
}"""

    elif subject == "geography":
        return """You are an expert physical and human geography curriculum architect.
Your objective is to analyze geographical text and map out physical processes, spatial patterns, cycles, and systems.

CRITICAL TOPOLOGY & HIERARCHY RULES:
1. MANDATORY MULTI-NODE HIERARCHY:
   - The root node MUST contain 4 to 8 distinct child nodes in its "children" array, one for EACH process, geographical zone, or landform.
   - NEVER collapse multiple systems into a single node or leave "children": [] empty.
2. Summary Structure:
   ### Core Concept
   - **Geographical Thesis**: [Core process or environmental system]
   - **Key Mechanism**: [Step-by-step physical or spatial breakdown]

   ### Key Details
   - **Spatial Pattern / Factors**: [Distribution, landforms, climatic drivers]
   - **Case Study**: [Specific geographical location with empirical data]

   ### Physical Meaning & Application
   - **Significance**: [Environmental impact, resource management, hazard mitigation]

JSON OUTPUT SCHEMA:
Output ONLY a single valid JSON object strictly matching this schema:
{
  "id": "root",
  "label": "Plate Tectonics & Global Lithospheric Dynamics",
  "summary": "### Core Concept\\n- **Geographical Thesis**: Earth lithosphere is divided into rigid plates moving over the asthenosphere driven by mantle convection.",
  "children": [
    {
      "id": "node-1",
      "label": "Divergent Boundaries & Sea-Floor Spreading",
      "summary": "### Core Concept\\n- **Geographical Thesis**: Magma upwelling at mid-ocean ridges creates new oceanic crust.\\n\\n### Key Details\\n- **Case Study**: Mid-Atlantic Ridge expanding at $2-5\\\\text{ cm/year}$.",
      "children": []
    },
    {
      "id": "node-2",
      "label": "Convergent Subduction Zones",
      "summary": "### Core Concept\\n- **Geographical Thesis**: Dense oceanic plates subduct under continental plates forming volcanic arcs and deep oceanic trenches.",
      "children": []
    }
  ]
}"""

    else:
        return """You are an elite educational mindmap and curriculum designer.
Your objective is to analyze the document and construct an in-depth, rigorous, highly comprehensive hierarchical mindmap.

CRITICAL TOPOLOGY & FORMATTING RULES:
1. MANDATORY MULTI-NODE HIERARCHY (NEVER COLLAPSE INTO A SINGLE NODE):
   - The root node MUST ONLY contain the overarching document title and a high-level thesis summary.
   - The root node MUST HAVE 4 to 8 distinct child nodes in its "children" array, one for EACH core concept, section, or chapter.
   - Each major child node SHOULD have 2 to 4 sub-child nodes in its own "children" array.
   - STRICTLY FORBIDDEN: Cramming multiple concepts into the root summary or outputting an empty "children": [] array.
2. STRICT DEPTH & COMPLETENESS INVARIANT (NEVER COMPROMISE ON CONTENT):
   - Every node MUST be exhaustive, rigorous, and fully detailed. Do NOT output shallow, abbreviated, or single-sentence summaries.
   - NEVER cut short, compress, or omit intermediate steps, mechanisms, empirical data, or worked explanations from the text.
3. Math & Formula Delimiters:
   - Every formula, equation, variable, chemical reaction, and math symbol MUST be wrapped in standard LaTeX ($inline$ or $$block$$).
   - NEVER use plain parentheses '(f(x)=a^x)' or raw arrows '→' without LaTeX delimiters (use '$\\to$').
   - NEVER leave raw backslashed commands outside of '$' delimiters.
4. Scannable Summary Structure (Use rich multi-bullet markdown format for EVERY node):
   ### Core Concept
   - **Main Thesis**: [Comprehensive 2-3 sentence intuition of the concept or topic]
   - **Key Mechanism**: [Step-by-step breakdown of how the process/system/formula functions with LaTeX $...$ notation]

   ### Key Details & Evidence
   - **Key Principles & Data**: [Definitions, facts, equations, empirical evidence from text]
   - **Variables & Structure**: [Detailed symbol definitions and structural components]

   ### Practical Meaning & Application
   - **Significance & Application**: [Practical engineering, scientific, economic, or societal relevance]

JSON OUTPUT SCHEMA:
Output ONLY a single valid JSON object strictly matching this schema:
{
  "id": "root",
  "label": "Document Overview & Core Themes",
  "summary": "### Core Concept\\n- **Main Thesis**: Primary conceptual takeaway and overarching framework.\\n- **Key Mechanism**: High-level structural logic connecting all document sections.",
  "children": [
    {
      "id": "node-1",
      "label": "First Core Topic / Chapter",
      "summary": "### Core Concept\\n- **Main Thesis**: Comprehensive breakdown of the first major subject area.\\n- **Key Mechanism**: Step-by-step structural workflow and analysis.\\n\\n### Key Details & Evidence\\n- **Key Principles & Data**: Fundamental rules, empirical formulas in $...$, and data points.\\n\\n### Practical Meaning & Application\\n- **Significance & Application**: Practical industry and academic applications.",
      "children": []
    },
    {
      "id": "node-2",
      "label": "Second Core Topic / Chapter",
      "summary": "### Core Concept\\n- **Main Thesis**: Detailed explanation of the second major concept.\\n- **Key Mechanism**: Causal drivers and operational principles.\\n\\n### Key Details & Evidence\\n- **Key Principles & Data**: Core identities and proofs.",
      "children": []
    },
    {
      "id": "node-3",
      "label": "Third Core Topic / Chapter",
      "summary": "### Core Concept\\n- **Main Thesis**: In-depth analysis of the third key theme.",
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

        # Extract digital text as backup and for multi-page context
        all_extracted_text = ""
        for page in doc:
            all_extracted_text += page.get_text() + "\n"

        # Multi-Page Token Budget:
        # Groq's qwen/qwen3.6-27b on-demand tier has an 8,000 TPM ceiling (~2400 tokens per image).
        # We pass the primary high-clarity page as an image and append remaining page text, allowing full 4096 max_tokens completion space.
        page_images_b64 = []
        max_visual_pages = min(len(doc), 1)
        for i in range(max_visual_pages):
            page = doc[i]
            # Render at 96 DPI for crisp text with optimized token weight
            pix = page.get_pixmap(dpi=96)
            pil_img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
            
            # Downsample if dimensions exceed 1024px
            max_dim = max(pil_img.size)
            if max_dim > 1024:
                scale = 1024 / max_dim
                new_size = (int(pil_img.size[0] * scale), int(pil_img.size[1] * scale))
                pil_img = pil_img.resize(new_size, Image.Resampling.LANCZOS)
                
            buf = io.BytesIO()
            pil_img.save(buf, format="JPEG", quality=80, optimize=True)
            page_images_b64.append(base64.b64encode(buf.getvalue()).decode("utf-8"))

        system_prompt = get_system_prompt(subject)
        
        prompt_text = (
            "Analyze the visual diagrams, flowcharts, mathematical/physical formulas, graphs, tables, "
            "and text on these document page(s). Synthesize BOTH the visual diagrams and text into an "
            "exhaustive, highly structured hierarchical mindmap JSON matching the requested schema with 3 to 6 detailed child nodes.\n"
            "CRITICAL: Do NOT compress, abbreviate, or omit intermediate algebra, mechanisms, formulas, or worked explanations.\n"
            "Ensure all formulas and equations are strictly formatted in standard LaTeX delimiters ($...$ for inline, $$...$$ for block)."
        )
        
        # If document has additional pages beyond the visual image, append their text
        if len(doc) > 1 and all_extracted_text.strip():
            prompt_text += f"\n\nAdditional Document Context from Remaining Pages:\n{all_extracted_text[:6000]}"

        user_content_blocks = [
            {
                "type": "text",
                "text": prompt_text
            }
        ]
        for img_b64 in page_images_b64:
            user_content_blocks.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{img_b64}"
                }
            })

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        data = {
            "model": "qwen/qwen3.6-27b",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content_blocks}
            ],
            "temperature": 0.2,
            "max_tokens": 4096
        }

        logger.info(f"Sending Groq Vision request for {file.filename} ({len(page_images_b64)} visual pages) using model: 'qwen/qwen3.6-27b'")
        
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers=headers,
                json=data,
                timeout=90.0
            )

            # If Groq Vision hits a 413 (Payload/TPM Too Large) or 429 (Rate Limit), smoothly fallback to fast-text LPU model
            if resp.status_code in [413, 429]:
                logger.warning(f"Groq Vision returned status {resp.status_code}. Automatically falling back to text LPU model 'openai/gpt-oss-120b'.")
                fallback_payload = {
                    "model": "openai/gpt-oss-120b",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": f"Analyze the document text and generate the hierarchical mindmap JSON:\n\n{all_extracted_text[:12000]}"}
                    ],
                    "temperature": 0.2,
                    "max_tokens": 4096
                }
                fallback_resp = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers=headers,
                    json=fallback_payload,
                    timeout=90.0
                )
                if fallback_resp.status_code == 200:
                    raw_fallback = fallback_resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                    mindmap_data = repair_and_parse_json(raw_fallback)
                    response.headers["X-Model-Used"] = "openai/gpt-oss-120b (Vision Text Fallback)"
                    response.headers["X-Model-Routed"] = "true"
                    response.headers["Access-Control-Expose-Headers"] = "X-Model-Used, X-Model-Routed"
                    return mindmap_data

            if resp.status_code != 200:
                logger.error(f"Groq Vision API error: {resp.status_code} - {resp.text[:200]}")
                raise HTTPException(
                    status_code=resp.status_code,
                    detail=f"Groq Vision error ({resp.status_code}): {resp.text[:150]}"
                )

            resp_json = resp.json()
            choices = resp_json.get("choices", [])
            if not choices:
                raise HTTPException(status_code=500, detail="Groq Vision returned an empty response.")

            raw_content = choices[0].get("message", {}).get("content", "")
            mindmap_data = repair_and_parse_json(raw_content)

            response.headers["X-Model-Used"] = "qwen/qwen3.6-27b (Vision Mode)"
            response.headers["X-Model-Routed"] = "false"
            response.headers["Access-Control-Expose-Headers"] = "X-Model-Used, X-Model-Routed"

            return mindmap_data

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
        # Candidate model order: selected model first, followed by all other free tier models as fallbacks
        candidate_models = [initial_model] + [m for m in ALL_FREE_TIER_MODELS if m != initial_model]

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
                    # Comprehensive token budget (4096 tokens) so complex math derivations are never cut off
                    max_tokens = 4096
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
                        await asyncio.sleep(0.5)
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



        # Emergency Fallback Node: If every single model fails, construct a clean fallback sub-mindmap node
        logger.error(f"All model fallback candidates failed for Chunk {index+1}. Synthesizing emergency fallback node.")
        return {
            "id": f"chunk_fallback_{index+1}",
            "label": f"Part {index+1} Overview",
            "summary": f"### Core Concept\n- **Overview**: Document summary for Part {index+1}.\n\n### Key Details\n- **Note**: Section recovered for continuous mindmap viewing.",
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
                final_map = sub_maps[0]
                await enrich_mindmap_with_images(final_map, max_images=6)
                return final_map
                
            # Otherwise, consolidate multiple mindmaps under a parent root
            first_label = sub_maps[0].get("label", "Document Study Guide")
            if first_label == "Central Topic":
                first_label = "Document Study Guide"
                
            consolidated_root = {
                "id": "root",
                "label": first_label,
                "summary": consolidate_summaries(sub_maps),
                "children": []
            }
            
            for i, sub_map in enumerate(sub_maps):
                # Ensure all sub-map nodes have unique IDs to prevent React Flow crashes
                unique_sub_map = make_ids_unique(sub_map, f"part_{i+1}")
                
                # Make the root node of this chunk a child of the master root
                part_label = unique_sub_map.get("label", f"Part {i+1}")
                if part_label == f"root_part_{i+1}" or part_label == "Central Topic":
                    part_label = f"Part {i+1}"
                else:
                    part_label = f"Part {i+1}: {part_label}"
                unique_sub_map["label"] = part_label
                
                consolidated_root["children"].append(unique_sub_map)
                
            await enrich_mindmap_with_images(consolidated_root, max_images=6)
            return consolidated_root

            
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
