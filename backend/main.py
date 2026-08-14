import os
import json
import logging
import re
import random
from typing import Optional, List
from fastapi import FastAPI, UploadFile, File, HTTPException, Body, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import pypdf
import httpx
from dotenv import load_dotenv
load_dotenv() # Load environment variables immediately on module import
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
    model: Optional[str] = "llama-3.3-70b-versatile"
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
    # 1. Escape unescaped backslashes before LaTeX macro names that are not standard JSON escapes
    s = re.sub(r"(?<!\\)\\(?![\"\\/bfnrtu])([a-zA-Z]+)", r"\\\\\1", s)
    
    # 2. Specifically fix LaTeX keywords that start with JSON escape letters (f, t, b, r, n)
    latex_keywords = ["frac", "theta", "times", "tau", "text", "tan", "to", "beta", "bar", "binom", "bullet", "rho", "right", "rangle", "root", "nu", "neq", "nabla", "degree"]
    for kw in latex_keywords:
        s = re.sub(r"(?<!\\)\\" + kw + r"\b", r"\\\\" + kw, s)
        
    return s

def repair_and_parse_json(response_text: str) -> dict:
    """
    Cleans, repairs, and parses LLM JSON responses into a Python dict.
    If the response was truncated mid-sentence or mid-object, it auto-repairs
    unclosed strings, quotes, arrays, and braces so mindmap generation never crashes.
    """
    cleaned = clean_json_string(response_text)
    cleaned = sanitize_json_latex(cleaned)
    
    # 1. Try direct parsing first
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict) and "id" in data and "label" in data and "children" in data:
            return data
    except Exception:
        pass

    # 2. Attempt JSON auto-repair for truncated output
    repaired = cleaned
    
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
        data = json.loads(repaired)
        if isinstance(data, dict):
            if "id" not in data: data["id"] = "root"
            if "label" not in data: data["label"] = "Section Overview"
            if "summary" not in data: data["summary"] = "### Core Concept\n- **Overview**: Document section summary."
            if "children" not in data or not isinstance(data["children"], list): data["children"] = []
            return data
    except Exception as e:
        logger.warning(f"JSON auto-repair parsing warning: {str(e)}")


    # 3. Fallback string extraction for label and summary if parsing fails
    label_match = re.search(r'"label"\s*:\s*"([^"]+)"', cleaned)
    extracted_label = label_match.group(1) if label_match else "Section Summary"
    
    summary_match = re.search(r'"summary"\s*:\s*"([^"]*)', cleaned)
    raw_summary = summary_match.group(1) if summary_match else "Document section overview."
    clean_summary = raw_summary.replace('\\n', '\n').strip()

    return {
        "id": "root_repaired",
        "label": extracted_label,
        "summary": f"### Core Concept\n- **Overview**: {clean_summary}\n\n### Key Details\n- **Note**: Truncated chunk automatically recovered for study view.",
        "children": []
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
        return """You are a distinguished Mathematics Professor and master STEM curriculum designer.
Your objective is to analyze the mathematical text and transform it into an in-depth, rigorous, highly comprehensive hierarchical mindmap.

CRITICAL MATHEMATICAL & DEPTH RULES:
1. STRICT DEPTH INVARIANT (NO ONE-LINERS):
   - Every node MUST be exhaustive and detailed. Do NOT output shallow or single-sentence summaries.
   - Explain underlying derivations, geometric intuition, algebraic conditions (e.g., domain restrictions, discriminant $\\Delta = b^2 - 4ac$, boundary cases), and step-by-step calculation workflows from the text.
   - Include concrete numerical worked examples showing exact step-by-step algebra.
2. LaTeX Standard: Every formula, variable, and expression MUST be formatted in standard LaTeX:
   - Inline expressions: $x$, $\\theta$, $\\int_a^b f(x)dx$, $\\lim_{x \\to 0}$, $\\sqrt{b^2 - 4ac}$
   - Block equations: $$f(x) = \\frac{-b \\pm \\sqrt{b^2 - 4ac}}{2a}$$
3. Plain-English Explanation: Always accompany every equation with a breakdown of its physical/geometric meaning and variable definitions.
4. 2D Function Plotting: When a node discusses a plottable 2D function or curve (quadratic, polynomial, trigonometric, exponential, rational), include a structured "graph" object with a standard mathematical string expression in 'x' and domain bounds.
5. Equations Field: Include an "equations" array of top LaTeX formulas for that concept.
6. MANDATORY HIERARCHY: Break down the document into 3 to 6 distinct child nodes representing core subtopics, theorems, and proofs.
7. Summary Structure (Use rich multi-bullet format):
   ### Core Concept
   - **Mathematical Principle**: [Comprehensive 2-3 sentence intuition of the theorem/principle]
   - **Key Mechanism & Derivation**: [Step-by-step mathematical logic, proof outline, or algebraic derivation]
   - **Conditions & Edge Cases**: [Domain restrictions, singular points, discriminant conditions]

   ### Equations & Variables (omit if no equations)
   - **Primary Formulation**: $$[Block LaTeX Formula]$$
   - **Variable Definitions**: [Detailed breakdown of symbols: $x$, $y$, coefficients, constants, domain]
   - **Key Identities & Forms**: [Alternative forms, factored representations, vertex form]

   ### Graph & Curve Behavior (omit if no graph)
   - **Key Geometric Features**: [Roots/intercepts, turning points $(h, k)$, axis of symmetry, asymptotes, curvature]
   - **Domain & Range**: [Interval bounds and asymptotic trends]

   ### Physical Meaning & Application
   - **Real-World Application**: [Concrete engineering, optimization, or geometric problem with numerical setup]
   - **Step-by-Step Worked Example**: [Step-by-step walkthrough showing algebraic substitution and final solution]

JSON OUTPUT SCHEMA:
Output ONLY a single valid JSON object strictly matching this schema:
{
  "id": "root",
  "label": "Quadratic Functions & Equations",
  "equations": ["f(x) = a x^2 + b x + c", "x = \\\\frac{-b \\\\pm \\\\sqrt{b^2 - 4ac}}{2a}"],
  "graph": {
    "fn": "x^2 - 4",
    "domain": [-4, 4],
    "xLabel": "x",
    "yLabel": "f(x)",
    "title": "Parabola f(x) = x^2 - 4"
  },
  "summary": "### Core Concept\\n- **Mathematical Principle**: Second-order polynomial functions describe non-linear relationships with constant second differences, forming parabolic curves.\\n- **Key Mechanism & Derivation**: Deriving the quadratic formula via completing the square on $ax^2 + bx + c = 0$ yields the general solution for all real and complex roots.\\n- **Conditions & Edge Cases**: Valid for $a \\\\neq 0$. If $a > 0$, the parabola is concave upward with a global minimum; if $a < 0$, it is concave downward with a global maximum.\\n\\n### Equations & Variables\\n- **Primary Formulation**: $$x = \\\\frac{-b \\\\pm \\\\sqrt{b^2 - 4ac}}{2a}$$\\n- **Variable Definitions**: $a, b, c$ are real constants ($a \\\\neq 0$), $x$ represents the independent variable.\\n- **Discriminant Analysis**: $\\\\Delta = b^2 - 4ac$ dictates root geometry: $\\\\Delta > 0$ (two distinct real roots), $\\\\Delta = 0$ (one repeated real root at vertex), $\\\\Delta < 0$ (complex conjugate roots).\\n\\n### Graph & Curve Behavior\\n- **Key Geometric Features**: Symmetric about vertical axis $x = -\\\\frac{b}{2a}$, with vertex coordinates $\\\\left(-\\\\frac{b}{2a}, -\\\\frac{\\\\Delta}{4a}\\\\right)$ and y-intercept at $(0, c)$.\\n\\n### Physical Meaning & Application\\n- **Real-World Application**: Trajectory modeling in ballistics and revenue optimization in economics.\\n- **Step-by-Step Worked Example**: For $f(x) = x^2 - 4$, setting $f(x) = 0 \\\\implies (x-2)(x+2) = 0$, yielding roots $x = \\\\pm 2$ and minimum at $(0, -4)$.",
  "children": [
    {
      "id": "child-1",
      "label": "Discriminant & Nature of Roots",
      "equations": ["\\\\Delta = b^2 - 4 a c"],
      "summary": "### Core Concept\\n- **Mathematical Principle**: The discriminant $\\\\Delta$ determines the intersection of the parabola with the x-axis without full factorisation.\\n- **Key Mechanism & Derivation**: Originates from the radicand term $\\\\sqrt{b^2 - 4ac}$ in the quadratic formula.\\n\\n### Equations & Variables\\n- **Primary Formulation**: $$\\\\Delta = b^2 - 4 a c$$\\n- **Variable Definitions**: $b^2$ is linear coefficient squared, $-4ac$ is the cross-product term.\\n\\n### Physical Meaning & Application\\n- **Real-World Application**: Used in control theory to test stability of second-order differential systems.",
      "children": []
    }
  ]
}"""

    elif subject == "physics":
        return """You are an elite Physics Professor and master STEM curriculum designer.
Your objective is to analyze the physics text and convert it into an in-depth, rigorous, highly comprehensive hierarchical mindmap.

CRITICAL PHYSICS & DEPTH RULES:
1. STRICT DEPTH INVARIANT (NO ONE-LINERS):
   - Every node MUST be exhaustive and detailed. Do NOT output shallow or single-sentence summaries.
   - Thoroughly explain first principles, force interactions, energy transfers, vector directions, conservation laws, and mathematical derivations from the text.
   - Include concrete numerical calculations and real-world engineering setups.
2. LaTeX Equations with SI Units: Every physical law, kinematics equation, or field formula MUST use standard LaTeX ($inline$ and $$block$$) accompanied by explicit SI units ($m/s^2$, $N$, $J$, $W$, $V$, $\\Omega$).
3. Plain-English Intuition: Connect every equation to physical reality (cause, effect, energy balance).
4. Physics Curves & Dynamics: Whenever a node discusses a physical curve (trajectory, harmonic oscillator, decay, velocity-time graph), provide a "graph" object with standard expression in 'x' and domain.
5. Equations Field: Include an "equations" array containing top LaTeX formulas for the concept.
6. MANDATORY HIERARCHY: Break down the document into 3 to 6 distinct child nodes representing core subtopics, laws, and components.
7. Summary Structure (Use rich multi-bullet format):
   ### Core Concept
   - **Physical Principle**: [Comprehensive 2-3 sentence intuition of the law or physical mechanism]
   - **Underlying Mechanism**: [Force interaction, momentum transfer, molecular process, or field dynamics]
   - **Conservation & Invariance**: [Energy conservation, momentum balance, frame of reference considerations]

   ### Equations & Variables (omit if no equations)
   - **Governing Law**: $$[Block LaTeX Formula]$$
   - **Variable Definitions & Units**: [Variables, constants ($g=9.81 m/s^2$), and SI units]
   - **Dimensional Analysis**: [Dimensional consistency breakdown]

   ### Graph & Curve Behavior (omit if no graph)
   - **Curve Dynamics**: [Physical meaning of gradient, area under curve, peak values, equilibrium points]

   ### Physical Meaning & Application
   - **Experimental Setup & Application**: [Real-world engineering, laboratory measurement, or technological application]
   - **Step-by-Step Worked Problem**: [Concrete numerical problem with step-by-step algebraic solution and unit analysis]

JSON OUTPUT SCHEMA:
Output ONLY a single valid JSON object strictly matching this schema:
{
  "id": "root",
  "label": "Kinematics & Dynamics of Motion",
  "equations": ["v = u + a t", "s = u t + \\\\frac{1}{2} a t^2", "v^2 = u^2 + 2 a s"],
  "graph": {
    "fn": "-0.5 * 9.81 * x^2 + 20 * x",
    "domain": [0, 4.1],
    "xLabel": "Time t (s)",
    "yLabel": "Height y (m)",
    "title": "Vertical Projectile Motion y(t)"
  },
  "summary": "### Core Concept\\n- **Physical Principle**: Kinematics mathematically describes the motion of points, bodies, and systems without considering the forces that caused them.\\n- **Underlying Mechanism**: Position, velocity, and acceleration are related through differential calculus as successive time-derivatives of displacement: $v(t) = \\\\frac{ds}{dt}$, $a(t) = \\\\frac{dv}{dt}$.\\n- **Conservation & Invariance**: In uniform gravitational fields without aerodynamic drag, mechanical energy $E_{mech} = E_k + E_p$ is strictly conserved throughout the flight.\\n\\n### Equations & Variables\\n- **Governing Law**: $$s(t) = u t + \\\\frac{1}{2} a t^2$$\\n- **Variable Definitions & Units**: $s$ (displacement, $m$), $u$ (initial velocity, $m/s$), $v$ (final velocity, $m/s$), $a$ (acceleration, $m/s^2$), $t$ (time, $s$).\\n- **Dimensional Analysis**: $[s] = [L]$, $[u t] = [L T^{-1} \\\\cdot T] = [L]$, $[\\\\frac{1}{2} a t^2] = [L T^{-2} \\\\cdot T^2] = [L]$, confirming dimensional homogeneity.\\n\\n### Graph & Curve Behavior\\n- **Curve Dynamics**: Parabolic height-time curve with peak apogee at $t_{apex} = \\\\frac{u}{g} \\\\approx 2.04\\\\text{ s}$, where vertical velocity momentarily drops to $v_y = 0\\\\text{ m/s}$.\\n\\n### Physical Meaning & Application\\n- **Experimental Setup & Application**: Ballistics, aerospace launch trajectories, and vehicle braking distance certification.\\n- **Step-by-Step Worked Problem**: Launching at $u = 20\\\\text{ m/s}$ yields maximum height $H = \\\\frac{u^2}{2g} = \\\\frac{400}{19.62} \\\\approx 20.39\\\\text{ m}$ and total flight duration $T = \\\\frac{2u}{g} \\\\approx 4.08\\\\text{ s}$.",
  "children": [
    {
      "id": "child-1",
      "label": "Uniform Acceleration Equations",
      "equations": ["v = u + a t", "v^2 = u^2 + 2 a s"],
      "graph": { "fn": "2 * x + 5", "domain": [0, 10], "xLabel": "Time t (s)", "yLabel": "Velocity v (m/s)", "title": "Velocity-Time v(t)" },
      "summary": "### Core Concept\\n- **Physical Principle**: Uniform acceleration implies a constant rate of change of velocity over time.\\n- **Underlying Mechanism**: On a velocity-time graph, the gradient represents constant acceleration $a = \\\\frac{\\\\Delta v}{\\\\Delta t}$, and the area under the line represents total displacement $s = \\\\int v dt$.\\n\\n### Equations & Variables\\n- **Governing Law**: $$v^2 = u^2 + 2 a s$$\\n- **Variable Definitions & Units**: $v$ (final velocity, $m/s$), $u$ (initial velocity, $m/s$), $a$ (acceleration, $m/s^2$), $s$ (distance, $m$).\\n\\n### Physical Meaning & Application\\n- **Experimental Setup & Application**: Calculating runway length required for aircraft takeoff and emergency stopping zones.",
      "children": []
    },
    {
      "id": "child-2",
      "label": "Newton's Second Law & Momentum",
      "equations": ["F_{net} = m a", "p = m v"],
      "summary": "### Core Concept\\n- **Physical Principle**: Net applied force equals the time rate of change of linear momentum: $F = \\\\frac{dp}{dt}$. For constant mass, this simplifies to $F = ma$.\\n- **Underlying Mechanism**: Unbalanced forces create acceleration inversely proportional to the body's inertial mass.\\n\\n### Equations & Variables\\n- **Governing Law**: $$F_{net} = m a$$\\n- **Variable Definitions & Units**: $F$ (force, $N$ or $kg \\\\cdot m/s^2$), $m$ (mass, $kg$), $a$ (acceleration, $m/s^2$).\\n\\n### Physical Meaning & Application\\n- **Experimental Setup & Application**: Crumple zones and seatbelt tensioners engineered to maximize impact duration $\\\\Delta t$, minimizing destructive peak impact force $F = \\\\frac{\\\\Delta p}{\\\\Delta t}$.",
      "children": []
    }
  ]
}"""



    elif subject == "history":
        return """You are an expert historian and educational mindmap designer.
Your objective is to analyze historical text and map out causal backbones, actor rationale, and ripple effects.

RULES FOR HISTORY:
1. SCANNABLE FORMAT: Use structured bullet points (- **Year/Event/Decision**: Rationale and consequence).
2. CAUSAL LOGIC: Organize hierarchy chronologically and causally (Root Cause → Trigger → Event → Immediate Outcome → Long-term Impact).
3. RATIONALE & ACTORS: Highlight strategic motivations and ideological drivers.
4. SUMMARY STRUCTURE:
   ### Core Concept
   - **Historical Thesis**: [Core historical takeaway and context]
   - **Causal Driver**: [Why and how key events unfolded]

   ### Key Details & Turning Points
   - **Key Turning Point**: [Year/Date + decisive event + outcome]
   - **Actors & Factions**: [Motivations and policies]

   ### Physical Meaning & Application
   - **Historical Significance**: [Long-term legacy and modern relevance]

JSON OUTPUT SCHEMA: Output ONLY a single valid JSON object (with "id", "label", "summary", "children")."""

    elif subject == "geography":
        return """You are an expert physical and human geography curriculum architect.
Your objective is to analyze geographical text and map out physical processes, spatial patterns, cycles, and systems.

RULES FOR GEOGRAPHY:
1. PROCESS & CYCLES: Structure sequential cycles and stages using numbered child nodes.
2. SPATIAL DYNAMICS: Highlight spatial distribution, climatic zones, plate boundaries, and human interactions.
3. SUMMARY STRUCTURE:
   ### Core Concept
   - **Geographical Thesis**: [Core process or environmental system]
   - **Key Mechanism**: [Step-by-step physical or spatial breakdown]

   ### Key Details
   - **Spatial Pattern / Factors**: [Distribution, landforms, climatic drivers]
   - **Case Study**: [Specific geographical location with empirical data]

   ### Physical Meaning & Application
   - **Significance**: [Environmental impact, resource management, hazard mitigation]

JSON OUTPUT SCHEMA: Output ONLY a single valid JSON object (with "id", "label", "summary", "children")."""

    else:
        return """You are an expert educational curriculum architect.
Your objective is to analyze the document and construct a hierarchical, ADHD-friendly, scannable mindmap.

RULES:
1. Node Labels: 3-5 word concise summaries.
2. Scannable Bullet Format:
   ### Core Concept
   - **Main Thesis**: [1-2 sentence core intuition]
   - **Key Mechanism**: [Step-by-step breakdown]

   ### Key Details
   - **Key Term / Data**: [Definitions, facts, figures from text]

   ### Physical Meaning & Application
   - **Significance & Application**: [Practical takeaway and broader context]

JSON OUTPUT SCHEMA: Output ONLY a single valid JSON object (with "id", "label", "summary", "children")."""



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
    
    raw_model = payload.model or "llama-3.3-70b-versatile"
    
    # Map non-existent or deprecated model strings to valid Groq Cloud models
    MODEL_ALIASES = {
        "meta-llama/llama-4-scout-17b-16e-instruct": "llama-3.3-70b-versatile",
        "qwen/qwen3.6-27b": "llama-3.3-70b-versatile",
        "qwen/qwen3-32b": "llama-3.3-70b-versatile",
        "openai/gpt-oss-20b": "llama3-8b-8192",
        "openai/gpt-oss-120b": "llama-3.3-70b-versatile",
        "llama-3.2-11b-vision-preview": "llama3-8b-8192",
        "llama-3.1-8b-instant": "llama-3.3-70b-versatile",
    }
    selected_model = MODEL_ALIASES.get(raw_model, raw_model)
    word_count = len(payload.text.split())
    
    # Adjust chunk size to 8,000 - 12,000 characters so completions stay safely within token limits
    if selected_model in ["llama3-8b-8192", "auto-smart-routing"] or len(payload.text) > 24000:
        chunk_size = 8000
    else:
        chunk_size = 12000
        
    # Split full text into chunks (limit to maximum 5 chunks)
    chunks = split_text_into_chunks(payload.text, chunk_size=chunk_size)[:5]
    logger.info(f"Splitting document into {len(chunks)} chunks of size {chunk_size} for parallel Groq processing.")
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    ALL_FREE_TIER_MODELS = [
        "llama-3.3-70b-versatile",
        "deepseek-r1-distill-llama-70b",
        "mixtral-8x7b-32768",
        "gemma2-9b-it",
        "llama3-70b-8192",
        "llama3-8b-8192",
    ]

    primary_model = selected_model
    is_routed = False
    
    if selected_model == "auto-smart-routing":
        is_routed = True
        primary_model = "llama-3.3-70b-versatile" if word_count >= 1200 else "mixtral-8x7b-32768"


    # Distribute initial models across all available free tier models if routed, or use primary model
    chunk_models = []
    for idx in range(len(chunks)):
        if is_routed:
            chunk_models.append(ALL_FREE_TIER_MODELS[idx % len(ALL_FREE_TIER_MODELS)])
        else:
            chunk_models.append(primary_model)

    # Track last request time per model to space out requests and avoid rate limits
    model_last_request: dict[str, float] = {}
    MIN_REQUEST_INTERVAL = 2.5  # seconds between requests to same model

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

            use_json_mode = True

            for attempt in range(2):
                try:
                    # 8B and Gemma models have strict 6,000-15,000 TPM limits on free tier. Cap max_tokens to 1500 for 8B models.
                    max_tokens = 1500 if ("8b" in current_model.lower() or "gemma" in current_model.lower()) else 4096
                    data = {
                        "model": current_model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt}
                        ],
                        "temperature": 0.2,
                        "max_tokens": max_tokens,
                    }
                    if use_json_mode:
                        data["response_format"] = {"type": "json_object"}

                    logger.info(f"Sending Groq API request for Chunk {index+1} using model: '{current_model}' (max_tokens={max_tokens}, Attempt {attempt+1})")
                    resp = await client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=data)

                    # On Rate Limit (429) or Payload/TPM Limit (413): switch to NEXT fallback model immediately
                    if resp.status_code in [413, 429]:
                        logger.warning(f"Groq rate/TPM limit hit ({resp.status_code}) on model '{current_model}' for Chunk {index+1}. Switching to fallback model...")
                        await asyncio.sleep(0.5)
                        break

                    if resp.status_code != 200:
                        resp_json = resp.json() if resp.content else {}
                        error_code = resp_json.get("error", {}).get("code", "")
                        if error_code == "json_validate_failed" and use_json_mode:
                            logger.warning(f"JSON mode validation failed on model '{current_model}'. Retrying with standard text mode...")
                            use_json_mode = False
                            continue
                        logger.warning(f"Groq API error status {resp.status_code} on model '{current_model}' for Chunk {index+1}. Switching to fallback model...")
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
