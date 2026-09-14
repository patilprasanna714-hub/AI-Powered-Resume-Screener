"""
textract_processor.py
Owner: Virajith — S3 Ingestion & Textract

Responsibilities:
    - S3 resume key validation & parsing (jobs/{job_id}/resumes/{candidate_id}/source.ext)
    - File metadata & size validation (PDF, DOCX, TXT <= 10MB)
    - Text extraction from Amazon Textract blocks with section line-break preservation
    - DOCX text extraction via standard XML parsing (zero external dependencies)
    - Formatting of extracted_text.json payload for S3 persistence and downstream NLP handoff
"""

import io
import json
import logging
import os
import re
import time
import zipfile
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger("textract-processor")
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# Allowed file extensions and maximum upload size (10 MB default)
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".doc", ".txt"}
ALLOWED_MIME_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
    "text/plain",
    "application/octet-stream",
}
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB

# Common resume section header keywords for boundary detection
SECTION_HEADER_PATTERNS = [
    r"^(?:professional\s+)?experience\b",
    r"^work\s+history\b",
    r"^employment\s+history\b",
    r"^education\b",
    r"^(?:technical\s+)?skills\b",
    r"^core\s+competencies\b",
    r"^projects?\b",
    r"^certifications?\b",
    r"^(?:professional\s+)?summary\b",
    r"^profile\b",
    r"^objective\b",
    r"^awards?\b",
    r"^publications?\b",
    r"^contact(?:\s+information)?\b",
    r"^languages?\b",
    r"^interests?\b",
    r"^references?\b",
]

SECTION_REGEX = re.compile(
    "|".join(f"(?:{p})" for p in SECTION_HEADER_PATTERNS),
    re.IGNORECASE
)


# ---------------------------------------------------------------------------
# S3 Key Parsing & Validation
# ---------------------------------------------------------------------------

def parse_s3_resume_key(s3_key: str) -> Dict[str, str]:
    """
    Parses S3 key following pattern: jobs/{job_id}/resumes/{candidate_id}/{filename}
    Also supports root-level keys or legacy paths.
    
    Returns:
        Dict with keys: 'job_id', 'candidate_id', 'filename', 'extension', 'valid_structure'
    """
    if not s3_key or not isinstance(s3_key, str):
        raise ValueError("s3_key must be a non-empty string")

    clean_key = s3_key.strip().lstrip("/")
    parts = clean_key.split("/")

    # Expected: jobs/{job_id}/resumes/{candidate_id}/{filename}
    if len(parts) >= 4 and parts[0] == "jobs" and parts[2] == "resumes":
        job_id = parts[1]
        candidate_id = parts[3]
        filename = parts[4] if len(parts) > 4 else "source.pdf"
        _, ext = os.path.splitext(filename)
        return {
            "job_id": job_id,
            "candidate_id": candidate_id,
            "filename": filename,
            "extension": ext.lower(),
            "valid_structure": True,
            "source_s3_key": clean_key,
        }

    # Fallback for alternative or legacy key structures
    filename = parts[-1]
    _, ext = os.path.splitext(filename)
    job_id = parts[0] if len(parts) > 1 else "default_job"
    candidate_id = parts[1] if len(parts) > 2 else "default_candidate"

    return {
        "job_id": job_id,
        "candidate_id": candidate_id,
        "filename": filename,
        "extension": ext.lower(),
        "valid_structure": False,
        "source_s3_key": clean_key,
    }


def validate_file_metadata(
    file_size_bytes: int,
    filename_or_key: str,
    mime_type: Optional[str] = None,
    max_size_bytes: int = MAX_FILE_SIZE_BYTES,
) -> Tuple[bool, Optional[str]]:
    """
    Validates file size and file extension / MIME type.
    
    Returns:
        (is_valid, error_message)
    """
    if file_size_bytes <= 0:
        return False, "File is empty (0 bytes)."

    if file_size_bytes > max_size_bytes:
        max_mb = max_size_bytes / (1024 * 1024)
        actual_mb = file_size_bytes / (1024 * 1024)
        return False, f"File size ({actual_mb:.2f} MB) exceeds maximum allowed limit ({max_mb:.2f} MB)."

    _, ext = os.path.splitext(filename_or_key.lower())
    if ext not in ALLOWED_EXTENSIONS:
        return False, f"Unsupported file extension '{ext}'. Allowed extensions: {sorted(ALLOWED_EXTENSIONS)}"

    if mime_type and mime_type not in ALLOWED_MIME_TYPES:
        logger.warning("Unrecognized MIME type '%s' for file '%s', extension valid.", mime_type, filename_or_key)

    return True, None


# ---------------------------------------------------------------------------
# Textract Block Processing (Preserving Section Line Breaks)
# ---------------------------------------------------------------------------

def is_probable_section_header(line_text: str) -> bool:
    """Checks if a line of text represents a resume section heading."""
    cleaned = line_text.strip()
    if not cleaned or len(cleaned) > 50:
        return False
    # Check regex match or all-uppercase heading (e.g., 'EXPERIENCE', 'EDUCATION')
    if SECTION_REGEX.match(cleaned):
        return True
    if cleaned.isupper() and len(cleaned.split()) <= 4 and any(c.isalpha() for c in cleaned):
        return True
    return False


def extract_text_from_textract_blocks(blocks: List[Dict[str, Any]]) -> str:
    """
    Extracts text from Textract Blocks array preserving reading order and section breaks.
    
    Key rules:
    - Filters BlockType == 'LINE'
    - Orders by Page number (if multi-page) and vertical layout coordinates
    - Inserts '\n\n' between section headers and paragraphs to preserve entity boundaries
    - Concatenates lines cleanly so NLP parser receives structured text
    """
    if not blocks:
        return ""

    line_blocks = [b for b in blocks if b.get("BlockType") == "LINE"]
    if not line_blocks:
        return ""

    # Sort blocks by Page (default 1), then Top position (vertical), then Left position
    def sort_key(b: Dict[str, Any]) -> Tuple[int, float, float]:
        page = b.get("Page", 1)
        geometry = b.get("Geometry", {}).get("BoundingBox", {})
        top = geometry.get("Top", 0.0)
        left = geometry.get("Left", 0.0)
        return (page, top, left)

    sorted_lines = sorted(line_blocks, key=sort_key)

    output_lines: List[str] = []
    prev_page = 1
    prev_bottom = 0.0

    for block in sorted_lines:
        text = block.get("Text", "").strip()
        if not text:
            continue

        curr_page = block.get("Page", 1)
        geometry = block.get("Geometry", {}).get("BoundingBox", {})
        curr_top = geometry.get("Top", 0.0)
        curr_height = geometry.get("Height", 0.0)
        curr_bottom = curr_top + curr_height

        # New page transition -> Add double newline
        if curr_page > prev_page:
            output_lines.append("\n" + text)
            prev_page = curr_page
            prev_bottom = curr_bottom
            continue

        # Significant vertical gap or section header -> Add section break
        vertical_gap = curr_top - prev_bottom if prev_bottom > 0 else 0
        is_header = is_probable_section_header(text)

        if (vertical_gap > 0.035 or is_header) and output_lines:
            output_lines.append("\n" + text)
        else:
            output_lines.append(text)

        prev_bottom = curr_bottom

    result_text = "\n".join(output_lines)
    # Normalize excessive consecutive newlines (max 2 consecutive)
    result_text = re.sub(r"\n{3,}", "\n\n", result_text).strip()
    return result_text


# ---------------------------------------------------------------------------
# DOCX Extraction Helper (Zero External Dependencies)
# ---------------------------------------------------------------------------

def extract_text_from_docx(file_bytes: bytes) -> str:
    """
    Extracts text from a DOCX file byte stream using zipfile and XML parsing.
    Preserves paragraph spacing and table cell content.
    """
    if not file_bytes:
        return ""

    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as docx_zip:
            if "word/document.xml" not in docx_zip.namelist():
                raise ValueError("Invalid DOCX file: word/document.xml missing")

            xml_content = docx_zip.read("word/document.xml")
            tree = ET.fromstring(xml_content)

            # Namespaces used in WordprocessingML
            ns = {
                "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
            }

            paragraphs: List[str] = []
            for p in tree.iter(f"{{{ns['w']}}}p"):
                texts = []
                for t in p.iter(f"{{{ns['w']}}}t"):
                    if t.text:
                        texts.append(t.text)
                for tab in p.iter(f"{{{ns['w']}}}tab"):
                    texts.append(" ")
                
                para_text = "".join(texts).strip()
                if para_text:
                    paragraphs.append(para_text)

            # Preserve section breaks between paragraphs
            joined_text = "\n\n".join(paragraphs)
            return joined_text.strip()

    except Exception as e:
        logger.error("Failed to parse DOCX content: %s", e)
        raise ValueError(f"Failed to extract text from DOCX: {e}") from e


# ---------------------------------------------------------------------------
# Unified Document Ingestion & Textract Invocation
# ---------------------------------------------------------------------------

def extract_text_from_document(
    s3_client: Any,
    textract_client: Any,
    bucket: str,
    key: str,
    is_async: bool = False,
) -> Dict[str, Any]:
    """
    Fetches a document from S3, detects file type, and runs appropriate extraction:
    - .pdf: Uses Amazon Textract (sync or async)
    - .docx / .doc: Extracts XML text
    - .txt: Reads text directly
    
    Returns:
        Dict with 'text', 'line_count', 'raw_blocks', 'file_type'
    """
    parsed_key = parse_s3_resume_key(key)
    ext = parsed_key["extension"]

    # 1. Fetch object from S3
    try:
        response = s3_client.get_object(Bucket=bucket, Key=key)
        body_bytes = response["Body"].read()
        content_length = response.get("ContentLength", len(body_bytes))
    except Exception as e:
        logger.error("Failed to read s3://%s/%s: %s", bucket, key, e)
        raise

    # 2. Validate file metadata
    is_valid, err = validate_file_metadata(content_length, key)
    if not is_valid:
        raise ValueError(f"File validation failed: {err}")

    # 3. Handle TXT
    if ext == ".txt":
        text = body_bytes.decode("utf-8", errors="replace").strip()
        return {
            "text": text,
            "line_count": len(text.splitlines()),
            "raw_blocks": [],
            "file_type": "TXT",
        }

    # 4. Handle DOCX
    if ext in (".docx", ".doc"):
        text = extract_text_from_docx(body_bytes)
        return {
            "text": text,
            "line_count": len(text.splitlines()),
            "raw_blocks": [],
            "file_type": "DOCX",
        }

    # 5. Handle PDF via Textract
    if ext == ".pdf":
        if not textract_client:
            raise ValueError("Textract client required for PDF processing")

        blocks: List[Dict[str, Any]] = []

        if is_async:
            # Asynchronous Textract for multi-page documents
            logger.info("Starting async Textract for s3://%s/%s", bucket, key)
            start_resp = textract_client.start_document_text_detection(
                DocumentLocation={"S3Object": {"Bucket": bucket, "Name": key}}
            )
            job_id = start_resp["JobId"]

            # Poll for completion
            while True:
                time.sleep(2)
                get_resp = textract_client.get_document_text_detection(JobId=job_id)
                status = get_resp.get("JobStatus")
                if status == "SUCCEEDED":
                    blocks.extend(get_resp.get("Blocks", []))
                    next_token = get_resp.get("NextToken")
                    while next_token:
                        get_resp = textract_client.get_document_text_detection(
                            JobId=job_id, NextToken=next_token
                        )
                        blocks.extend(get_resp.get("Blocks", []))
                        next_token = get_resp.get("NextToken")
                    break
                elif status == "FAILED":
                    err_status = get_resp.get("StatusMessage", "Textract async job failed")
                    raise RuntimeError(f"Textract async job {job_id} failed: {err_status}")
                elif status in ("IN_PROGRESS", "INITIALIZED"):
                    continue
                else:
                    raise RuntimeError(f"Unknown Textract status '{status}' for job {job_id}")
        else:
            # Synchronous Textract
            logger.info("Calling sync Textract detect_document_text for s3://%s/%s", bucket, key)
            resp = textract_client.detect_document_text(
                Document={"S3Object": {"Bucket": bucket, "Name": key}}
            )
            blocks = resp.get("Blocks", [])

        # Extract text with section line-break preservation
        extracted_text = extract_text_from_textract_blocks(blocks)
        return {
            "text": extracted_text,
            "line_count": len(extracted_text.splitlines()),
            "raw_blocks": blocks,
            "file_type": "PDF",
        }

    raise ValueError(f"Unsupported file format '{ext}'")


# ---------------------------------------------------------------------------
# S3 Extracted JSON Payload Builder
# ---------------------------------------------------------------------------

def build_extracted_text_payload(
    job_id: str,
    candidate_id: str,
    text: str,
    source_s3_key: str,
    correlation_id: Optional[str] = None,
    raw_blocks: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Constructs the canonical extracted_text.json payload to be saved in S3:
    s3://{BUCKET}/jobs/{job_id}/resumes/{candidate_id}/extracted_text.json
    """
    return {
        "job_id": job_id,
        "candidate_id": candidate_id,
        "source_s3_key": source_s3_key,
        "correlation_id": correlation_id or f"corr_{job_id}_{candidate_id}",
        "text": text,
        "line_count": len(text.splitlines()),
        "character_count": len(text),
        "extracted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "Blocks": raw_blocks or [],
    }
