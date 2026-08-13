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

def repair_and_parse_json(response_text: str) -> dict:
    """
    Cleans, repairs, and parses LLM JSON responses into a Python dict.
    If the response was truncated mid-sentence or mid-object, it auto-repairs
    unclosed strings, quotes, arrays, and braces so mindmap generation never crashes.
    """
    cleaned = clean_json_string(response_text)
    
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

    clean_query = re.sub(r'^[0-9]+\.\s*', '', query).strip()
    search_url = "https://commons.wikimedia.org/w/api.php"
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": f"{clean_query} filetype:bitmap|drawing",
        "gsrnamespace": "6",
        "gsrlimit": "4",
        "prop": "imageinfo",
        "iiprop": "url|size|extmetadata",
        "format": "json"
    }

    headers = {
        "User-Agent": "GenAIResearchMindmap/1.0 (educational research tool; contact@example.com)"
    }

    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.get(search_url, params=params, headers=headers)
            if resp.status_code != 200:
                return None

            data = resp.json()
            pages = data.get("query", {}).get("pages", {})
            if not pages:
                return None

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

                # Exclude common meta icons and non-media files
                if any(bad in url.lower() for bad in ['.ogv', '.webm', '.ogg', 'commons-logo', 'symbol', 'flag', 'icon', 'button']):
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
        logger.warning(f"Wikimedia API fetch failed for query '{query}': {str(e)}")

    return None

async def enrich_mindmap_with_images(node: dict, max_images: int = 6, count: int = 0) -> int:
    """
    Recursively attaches Wikimedia Commons educational images to key mindmap nodes.
    """
    if count >= max_images:
        return count

    label = node.get("label", "")
    # Enrich root, major subtopics, or key concepts
    should_enrich = (node.get("id") == "root" or len(node.get("children", [])) > 0 or random.random() < 0.35)

    if should_enrich and label:
        image_data = await fetch_wikimedia_image(label)
        if image_data:
            node["imageUrl"] = image_data["imageUrl"]
            node["imageCaption"] = image_data["imageCaption"]
            node["imageAspectRatio"] = image_data["imageAspectRatio"]
            count += 1

    for child in node.get("children", []):
        if count >= max_images:
            break
        count = await enrich_mindmap_with_images(child, max_images=max_images, count=count)

    return count

# Subject-specific system prompts
def get_system_prompt(subject: str) -> str:
    base_prompt = (
        "You are an expert educational designer specializing in cognitive accessibility, ADHD-friendly learning, and data visualization.\n"
        "Your task is to analyze the provided text and structure it into a hierarchical mindmap representation.\n"
        "To make this ADHD-friendly, scannable, and high-substance for research, you MUST adhere to the following rules:\n"
        "1. Node Labels: Must be extremely scannable, flat summaries (maximum of 3 to 5 words per label).\n"
        "2. Node Summaries (SCANNABLE BULLET FORMAT): The 'summary' field for each node MUST be a single, flat JSON string value (enclosed in double quotes). It must NOT be a nested JSON object or list. Use clean, scannable BULLET POINTS with bolded key terms/dates/entities instead of wall-of-text paragraphs, while ensuring ALL key points, mechanisms, data, and details from the text are thoroughly covered. It must follow this exact Markdown structure inside the string (using escaped newlines \\n):\n"
        "   \"summary\": \"### Core Concept\\n- **Main Thesis**: [Deep 1-2 sentence core concept explanation]\\n- **Key Mechanism**: [Step-by-step or core process breakdown from text]\\n- **Significance**: [Why this matters in context]\\n\\n### Key Details\\n- **Key Term 1**: [Definition + significance from text]\\n- **Key Term 2**: [Definition + significance from text]\\n- **Core Components**: [Detailed breakdown from text]\\n\\n### Evidence & Case Studies\\n- **[Example Name]**: [Concrete case from text with data: place names, statistics, years, outcomes]\\n- **[Example Name]**: [Second case from text with different context/region]\\n\\n### Connection\\n- **Link to Context**: [1-2 sentences: How this links to parent, why it matters for the big picture]\\n\\n### Memory Hook\\n- **Visual Anchor**: [ONE vivid analogy, mnemonic, visual image, or 'aha!' insight from text]\")\n"
        "   CRITICAL: Do NOT make 'summary' a JSON object or omit the double quotes around its value. Use scannable bullet points for ALL sections.\n"
        "3. COVERAGE: Every distinct concept, stage, step, phase, component, argument, event, or case study in the source text MUST appear as a node. Do not summarize away content. Do not skip stages. Do not merge distinct ideas.\n"
        "4. HIERARCHY: The tree structure must mirror the document's logical organization. If the text presents a cycle with 7 stages → 7 child nodes. If it presents 3 causes → 3 child nodes. If it presents a causal chain → chain structure.\n"
        "5. SOURCE FIDELITY: Use ONLY information from the provided text. Do not add external knowledge. Do not invent examples not in the text.\n"
        "6. Output format: Respond with a single valid JSON object containing no other text.\n\n"
        "The JSON object must strictly conform to this recursive structure:\n"
        "{\n"
        "  \"id\": \"root\",\n"
        "  \"label\": \"Central Topic\",\n"
        "  \"summary\": \"### Core Concept\\n- **Main Thesis**: [Overall document thesis from text]\\n- **Scope**: [Key themes covered]\\n\\n### Key Details\\n- **Key Term**: [Definition + why it matters from text]\\n\\n### Evidence & Case Studies\\n- **[Example]**: [Concrete case from text with data]\\n\\n### Connection\\n- **Significance**: [Main scope and significance]\\n\\n### Memory Hook\\n- **Anchor**: [One vivid anchor for the entire topic]\",\n"
        "  \"children\": [\n"
        "    {\n"
        "      \"id\": \"child-id-1\",\n"
        "      \"label\": \"Subtopic Label\",\n"
        "      \"summary\": \"### Core Concept\\n- **Core Process**: [Deep explanation with mechanism from text]\\n- **Context**: [Nuances and implications]\\n\\n### Key Details\\n- **Key Term**: [Definition + significance from text]\\n\\n### Evidence & Case Studies\\n- **[Example]**: [Concrete case from text with data]\\n\\n### Connection\\n- **Parent Link**: [Link to parent and big picture]\\n\\n### Memory Hook\\n- **Mnemonic**: [Vivid analogy or mnemonic]\",\n"
        "      \"children\": []\n"
        "    }\n"
        "  ]\n"
        "}\n"
        "Ensure all children are formatted similarly, and nested hierarchies are created where logical.\n"
        "JSON formatting safety guidelines:\n"
        "- The 'summary' field MUST be a plain text string. Do NOT output it as an object with keys like '### Core Concept'.\n"
        "- All text inside the 'summary' string must have its newlines escaped as \\n.\n"
        "- Ensure the entire response is a single, valid JSON object matching the schema."
    )
    
    if subject == "geography":
        return base_prompt + """

SPECIALIZATION: GEOGRAPHY — Processes, Cycles, Systems & Spatial Patterns

Geography IS concepts. Every model, cycle, process, theory, pattern, and relationship IS a concept. Dropping a concept = broken understanding.

MANDATORY JSON STRUCTURE:
- Use numbered stage child nodes for cycles/models.
- Ensure summaries use scannable bullet points (- **Key Concept**: explanation) with bolded terms.
- Cover all case studies, statistics, and place names from the text."""

    elif subject == "history":
        return base_prompt + """

SPECIALIZATION: HISTORY — Causal Chains, Rationale, Interconnected Narrative

History is understanding HOW one thing leads to another — the reasoning behind actions, decisions, and ripple effects.

CORE PRINCIPLES FOR HISTORY (ADHD-FRIENDLY & SCANNABLE):
1. SCANNABLE BULLETS OVER PARAGRAPHS: Do NOT write long paragraphs. Structure all narrative explanations into scannable bullet points (- **Year/Event/Decision**: Rationale and consequences).
2. CAUSAL BACKBONE: Hierarchy follows causality (Root Cause → Trigger → Event → Consequence → Next Trigger).
3. RATIONALE IS NON-NEGOTIABLE: Explain actor reasoning clearly in bite-sized bullet points (- **Actor Rationale**: Strategic/ideological reason).
4. COMPLETE COVERAGE: Include ALL key dates, treaties, figures, and outcomes from the text without omitting critical details."""

    else:
        return base_prompt


@app.get("/api/health")
def health_check():
    return {"status": "ok", "openrouter_configured": bool(os.environ.get("OPENROUTER_API_KEY"))}

@app.get("/api/auth/config")
def get_auth_config():
    return {
        "supabaseUrl": SUPABASE_URL,
        "supabaseKey": SUPABASE_ANON_KEY or SUPABASE_KEY
    }

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
        raise HTTPException(status_code=400, detail="Email is required.")
    
    headers = get_supabase_headers()
    url = f"{SUPABASE_URL}/rest/v1/documents?user_email=eq.{email.strip().lower()}&order=created_at.desc"
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                logger.error(f"Supabase GET documents error: {resp.text}")
                raise HTTPException(status_code=resp.status_code, detail=f"Failed to fetch documents: {resp.text}")
            
            docs = resp.json()
            result = []
            for doc in docs:
                result.append({
                    "id": doc.get("id"),
                    "name": doc.get("name"),
                    "data": doc.get("data"),
                    "userEmail": doc.get("user_email")
                })
            return result
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Unexpected error in get_documents: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

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
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers=headers, json=supabase_payload)
            if resp.status_code not in (200, 201):
                logger.error(f"Supabase POST documents error: {resp.text}")
                raise HTTPException(status_code=resp.status_code, detail=f"Failed to save document: {resp.text}")
            
            return {"status": "success", "id": payload.id}
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Unexpected error in save_document: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/documents/{doc_id}")
async def delete_document(doc_id: str, email: str):
    if not email:
        raise HTTPException(status_code=400, detail="Email is required to verify ownership.")
    
    headers = get_supabase_headers()
    url = f"{SUPABASE_URL}/rest/v1/documents?id=eq.{doc_id}&user_email=eq.{email.strip().lower()}"
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.delete(url, headers=headers)
            if resp.status_code not in (200, 204):
                logger.error(f"Supabase DELETE document error: {resp.text}")
                raise HTTPException(status_code=resp.status_code, detail=f"Failed to delete document: {resp.text}")
            
            return {"status": "success"}
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Unexpected error in delete_document: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

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
        
        full_text = []
        is_scanned = True
        
        # 1. Attempt digital text extraction first
        for page in doc:
            text = page.get_text()
            if text and len(text.strip()) > 50:
                is_scanned = False
                full_text.append(text)
                
        # 2. If it seems to be scanned, perform OCR on all pages in parallel
        if is_scanned or not "".join(full_text).strip():
            logger.info("No digital text found. Performing parallel OCR on PDF pages...")
            
            # Render all page frames to images in the main thread (takes <1s total)
            page_images = []
            for page in doc:
                pix = page.get_pixmap(dpi=120)  # Optimized DPI for faster Tesseract processing
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
        "openai/gpt-oss-20b": "llama-3.1-8b-instant",
        "openai/gpt-oss-120b": "llama-3.3-70b-versatile",
        "llama-3.2-11b-vision-preview": "llama-3.1-8b-instant",
    }
    selected_model = MODEL_ALIASES.get(raw_model, raw_model)
    word_count = len(payload.text.split())
    
    # Adjust chunk size to 8,000 - 12,000 characters so completions stay safely within token limits
    if selected_model in ["llama-3.1-8b-instant", "auto-smart-routing"] or len(payload.text) > 24000:
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
    
    LARGE_POOL = [
        "llama-3.3-70b-versatile",
        "deepseek-r1-distill-llama-70b",
        "llama3-70b-8192",
    ]
    SMALL_POOL = [
        "llama-3.1-8b-instant",
        "gemma2-9b-it",
        "mixtral-8x7b-32768",
        "llama3-8b-8192",
    ]

    primary_model = selected_model
    is_routed = False
    
    if selected_model == "auto-smart-routing":
        is_routed = True
        if word_count < 1500:
            primary_model = "llama-3.1-8b-instant"
        else:
            primary_model = "llama-3.3-70b-versatile"

    def assess_chunk_complexity(text: str) -> float:
        """Assess chunk complexity (0-1) based on heuristics: density of proper nouns, numbers, technical terms, sentence complexity."""
        import re
        words = text.split()
        if not words:
            return 0.5
        
        # Heuristics for complexity
        proper_nouns = len(re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', text))
        numbers = len(re.findall(r'\b\d+(?:[.,]\d+)?\b', text))
        technical_terms = len(re.findall(r'\b(?:model|theory|cycle|process|stage|phase|mechanism|framework|hypothesis|law|principle|concept|pattern|system|structure|function|relationship|interaction|distribution|correlation|causality|significance|implication|factor|variable|parameter|indicator|metric|statistic|data|evidence|case study|example|instance|application|implementation)\b', text, re.IGNORECASE))
        long_sentences = len([s for s in re.split(r'[.!?]+', text) if len(s.split()) > 25])
        avg_word_len = sum(len(w) for w in words) / len(words)
        
        # Normalize and combine
        score = (
            min(proper_nouns / max(len(words) * 0.05, 1), 1.0) * 0.25 +
            min(numbers / max(len(words) * 0.03, 1), 1.0) * 0.2 +
            min(technical_terms / max(len(words) * 0.02, 1), 1.0) * 0.25 +
            min(long_sentences / max(len(words) * 0.01, 1), 1.0) * 0.15 +
            min((avg_word_len - 4) / 4, 1.0) * 0.15
        )
        return max(0.0, min(1.0, score))

    is_small_tier = primary_model in SMALL_POOL
    target_pool = SMALL_POOL if is_small_tier else LARGE_POOL

    # Assess complexity for each chunk
    chunk_complexities = [assess_chunk_complexity(chunk) for chunk in chunks]
    avg_complexity = sum(chunk_complexities) / len(chunk_complexities) if chunk_complexities else 0.5

    chunk_models = []
    for idx, complexity in enumerate(chunk_complexities):
        if idx == 0:
            # First chunk gets primary model
            chunk_models.append(primary_model)
        else:
            # Route based on complexity: high complexity -> large pool, low -> small pool
            if complexity > avg_complexity:
                # More complex: use large pool
                others = [m for m in LARGE_POOL if m != primary_model]
                if not others:
                    others = LARGE_POOL
            else:
                # Less complex: use small pool
                others = [m for m in SMALL_POOL]
            model_to_use = others[(idx - 1) % len(others)]
            chunk_models.append(model_to_use)

    # Track last request time per model to space out requests and avoid rate limits
    model_last_request: dict[str, float] = {}
    MIN_REQUEST_INTERVAL = 3.0  # seconds between requests to same model (increased for free tier)

    unique_models_used = []
    for m in chunk_models:
        if m not in unique_models_used:
            unique_models_used.append(m)
            
    models_used_str = ", ".join(unique_models_used)

    response.headers["X-Model-Used"] = models_used_str
    response.headers["X-Model-Routed"] = "true" if (is_routed or len(chunks) > 1) else "false"
    response.headers["Access-Control-Expose-Headers"] = "X-Model-Used, X-Model-Routed"

    async def process_chunk(client: httpx.AsyncClient, chunk_text: str, index: int) -> dict:
        chunk_model = chunk_models[index]
        
        # Space out requests to the same model
        import time
        now = time.monotonic()
        if chunk_model in model_last_request:
            elapsed = now - model_last_request[chunk_model]
            if elapsed < MIN_REQUEST_INTERVAL:
                wait_time = MIN_REQUEST_INTERVAL - elapsed
                logger.info(f"Spacing request for model {chunk_model}: waiting {wait_time:.2f}s")
                await asyncio.sleep(wait_time)
        model_last_request[chunk_model] = time.monotonic()
        
        user_prompt = f"Here is the text extracted from Part {index+1} of the document to turn into a mindmap:\n\n{chunk_text}"
        
        # Try with JSON mode first, fallback to non-JSON if model doesn't support it
        use_json_mode = True
        
        max_retries = 3
        backoff_factor = 3.0
        
        for attempt in range(max_retries + 1):
            try:
                data = {
                    "model": chunk_model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    "temperature": 0.2,
                    "max_tokens": 4096,
                }
                if use_json_mode:
                    data["response_format"] = {"type": "json_object"}
                
                logger.info(f"Sending Groq API request for Chunk {index+1} (Attempt {attempt+1}/{max_retries+1}) using model: {chunk_model}, json_mode={use_json_mode}")
                response = await client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=data)
                
                # Handle standard HTTP 429 Too Many Requests
                if response.status_code == 429:
                    if attempt < max_retries:
                        retry_after = response.headers.get("retry-after")
                        sleep_seconds = float(retry_after) if retry_after else (backoff_factor ** attempt + random.uniform(5.0, 10.0))
                        logger.warning(f"Rate limit hit (429) on chunk {index+1} for model {chunk_model}. Retrying in {sleep_seconds:.2f}s...")
                        await asyncio.sleep(sleep_seconds)
                        continue
                    else:
                        logger.error(f"Rate limit retries exhausted for chunk {index+1}: {response.text}")
                        raise HTTPException(status_code=429, detail=f"Rate limit exceeded on chunk {index+1}. Please try again later.")
                
                # Check for other error codes
                if response.status_code != 200:
                    resp_json = {}
                    try:
                        resp_json = response.json()
                    except Exception:
                        pass
                    
                    error_msg = resp_json.get("error", {}).get("message", "")
                    error_code = resp_json.get("error", {}).get("code", "")
                    
                    # Handle JSON validation failure - fallback to non-JSON mode
                    if error_code == "json_validate_failed" and use_json_mode:
                        logger.warning(f"JSON validation failed for chunk {index+1} with model {chunk_model}. Falling back to non-JSON mode.")
                        use_json_mode = False
                        if attempt < max_retries:
                            await asyncio.sleep(1.0)
                            continue
                    
                    # Sometimes rate limits return as 400 or other codes on some Gateways
                    if "rate limit" in error_msg.lower() or "tpm" in error_msg.lower() or "rpm" in error_msg.lower():
                        if attempt < max_retries:
                            sleep_seconds = backoff_factor ** attempt + random.uniform(8.0, 15.0)
                            logger.warning(f"Rate limit error message detected on chunk {index+1} for model {chunk_model}. Retrying in {sleep_seconds:.2f}s...")
                            await asyncio.sleep(sleep_seconds)
                            continue
                    
                    logger.error(f"Groq API returned error status {response.status_code} for chunk {index+1}: {response.text}")
                    raise HTTPException(status_code=response.status_code, detail=f"Groq API Error on chunk {index+1}: {response.text}")
                
                response_json = response.json()
                choices = response_json.get("choices", [])
                if not choices:
                    raise HTTPException(status_code=500, detail=f"Groq response for chunk {index+1} is missing choices.")
                
                content = choices[0].get("message", {}).get("content", "")
                mindmap_data = repair_and_parse_json(content)
                return mindmap_data

                        
            except httpx.HTTPError as http_err:
                if attempt < max_retries:
                    sleep_seconds = backoff_factor ** attempt + random.uniform(3.0, 6.0)
                    logger.warning(f"HTTP error on chunk {index+1} (Attempt {attempt+1}): {str(http_err)}. Retrying in {sleep_seconds:.2f}s...")
                    await asyncio.sleep(sleep_seconds)
                    continue
                else:
                    raise HTTPException(status_code=500, detail=f"HTTP connection error on chunk {index+1}: {str(http_err)}")

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
