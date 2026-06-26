import os
import json
import logging
import re
from typing import Optional, List
from fastapi import FastAPI, UploadFile, File, HTTPException, Body
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

class MindmapGenerateRequest(BaseModel):
    text: str
    model: Optional[str] = "meta-llama/llama-4-scout-17b-16e-instruct"

def clean_json_string(response_text: str) -> str:
    """
    Extracts and cleans a JSON block from the model's text response.
    Models sometimes surround the JSON output with markdown block markers (```json ... ```)
    or conversational text.
    Uses fast string index operations to avoid catastrophic regex backtracking on long JSON outputs.
    """
    # 1. Search for markdown code block markers
    markdown_match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", response_text)
    if markdown_match:
        return markdown_match.group(1).strip()
    
    # 2. Otherwise find the first '{' and the last '}' using fast string methods
    start = response_text.find('{')
    end = response_text.rfind('}')
    if start != -1 and end != -1 and end > start:
        return response_text[start:end+1].strip()
        
    return response_text.strip()

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
async def generate_mindmap(payload: MindmapGenerateRequest):
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        logger.error("GROQ_API_KEY is not set in the environment variables.")
        raise HTTPException(
            status_code=500, 
            detail="Groq API Key is not configured. Please set the GROQ_API_KEY environment variable."
        )
    
    # Prepare the prompt structure
    system_prompt = (
        "You are an expert educational designer specializing in cognitive accessibility, ADHD-friendly learning, and data visualization.\n"
        "Your task is to analyze the provided text and structure it into a hierarchical mindmap representation.\n"
        "To make this ADHD-friendly and high-substance for research, you MUST adhere to the following rules:\n"
        "1. Node Labels: Must be extremely scannable, flat summaries (maximum of 3 to 5 words per label).\n"
        "2. Node Summaries: The 'summary' field for each node MUST be a single, flat JSON string value (enclosed in double quotes). It must NOT be a nested JSON object or list. It must follow this exact Markdown structure inside the string (using escaped newlines \\n):\n"
        "   \"summary\": \"### Core Concept\\n- [1-2 sentences explaining the core factual concept in depth]\\n\\n### Examples\\n- **[Example Name]**: [1 concrete, specific example or case study from the text]\\n- **[Example Name]**: [A second concrete, specific example from the text]\\n\\n### Connection\\n- [1 sentence explaining how this subtopic links back to its parent subtopic and helps support the main central topic]\"\n"
        "   CRITICAL: Do NOT make 'summary' a JSON object or omit the double quotes around its value. It must be a plain JSON string containing the markdown text.\n"
        "3. Coherent Hierarchy: Build a clear hierarchy from themes to concepts to specific details.\n"
        "4. Output format: Respond with a single valid JSON object containing no other text.\n\n"
        "The JSON object must strictly conform to this recursive structure:\n"
        "{\n"
        "  \"id\": \"root\",\n"
        "  \"label\": \"Central Topic\",\n"
        "  \"summary\": \"### Core Concept\\n- [Overall document summary]\\n\\n### Examples\\n- **[Example]**: [Doc example]\\n\\n### Connection\\n- [Main scope]\",\n"
        "  \"children\": [\n"
        "    {\n"
        "      \"id\": \"child-id-1\",\n"
        "      \"label\": \"Subtopic Label\",\n"
        "      \"summary\": \"### Core Concept\\n- [Subtopic concept]\\n\\n### Examples\\n- **[Example]**: [Subtopic example]\\n\\n### Connection\\n- [Relation to parent and root]\",\n"
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
    
    # Split full text into chunks (limit to maximum 5 chunks)
    chunks = split_text_into_chunks(payload.text, chunk_size=30000)[:5]
    logger.info(f"Splitting document into {len(chunks)} chunks for parallel Groq processing.")
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    model_name = payload.model
    
    async def process_chunk(client: httpx.AsyncClient, chunk_text: str, index: int) -> dict:
        user_prompt = f"Here is the text extracted from Part {index+1} of the document to turn into a mindmap:\n\n{chunk_text}"
        
        data = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"}
        }
        
        logger.info(f"Sending Groq API request for Chunk {index+1} using model: {model_name}")
        response = await client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=data)
        
        if response.status_code != 200:
            logger.error(f"Groq API returned error status {response.status_code} for chunk {index+1}: {response.text}")
            raise HTTPException(status_code=response.status_code, detail=f"Groq API Error on chunk {index+1}: {response.text}")
            
        response_json = response.json()
        choices = response_json.get("choices", [])
        if not choices:
            raise HTTPException(status_code=500, detail=f"Groq response for chunk {index+1} is missing choices.")
            
        content = choices[0].get("message", {}).get("content", "")
        
        cleaned_content = clean_json_string(content)
        try:
            mindmap_data = json.loads(cleaned_content)
            if "id" not in mindmap_data or "label" not in mindmap_data or "children" not in mindmap_data:
                raise ValueError("JSON is missing required mindmap properties (id, label, children).")
            return mindmap_data
        except Exception as parse_error:
            logger.error(f"Failed to parse Groq response into valid mindmap JSON for chunk {index+1}. Error: {str(parse_error)}")
            raise HTTPException(
                status_code=500, 
                detail=f"The model's output for chunk {index+1} could not be parsed into a valid mindmap. Raw: {content[:200]}"
            )

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            # Process all chunks concurrently
            tasks = [process_chunk(client, chunk, i) for i, chunk in enumerate(chunks)]
            sub_maps = await asyncio.gather(*tasks)
            
            if not sub_maps:
                raise HTTPException(status_code=500, detail="No mindmaps could be generated.")
                
            # If there is only one chunk, return it directly
            if len(sub_maps) == 1:
                return sub_maps[0]
                
            # Otherwise, consolidate multiple mindmaps under a parent root
            first_label = sub_maps[0].get("label", "Document Study Guide")
            if first_label == "Central Topic":
                first_label = "Document Study Guide"
                
            consolidated_root = {
                "id": "root",
                "label": first_label,
                "summary": "### Core Concept\n- Consolidated study map merging key concepts across all pages of the document.\n\n### Examples\n- **Multi-Part Processing**: Structured sections processed concurrently via parallel prompts.\n\n### Connection\n- Master study map.",
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
                
            return consolidated_root
            
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")
