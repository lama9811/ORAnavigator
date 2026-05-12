"""
Text Extraction via text-extract-api
=====================================
Sends documents (PDF, DOCX, images) to the text-extract-api Docker service
for high-quality text extraction with OCR support.

API: https://github.com/CatchTheTornado/text-extract-api
"""

import os
import time
import requests

TEXT_EXTRACT_URL = os.getenv("TEXT_EXTRACT_API_URL", "http://127.0.0.1:8001")

# File types the extraction API supports
SUPPORTED_EXTENSIONS = {'pdf', 'docx', 'doc', 'pptx', 'png', 'jpg', 'jpeg', 'gif', 'tiff', 'bmp', 'webp'}


def extract_text(filepath: str, timeout: int = 60) -> str:
    """
    Extract text from a document using text-extract-api.

    Args:
        filepath: Path to the file on disk
        timeout: Max seconds to wait for extraction (default 60)

    Returns:
        Extracted text string

    Raises:
        RuntimeError: If extraction fails or times out
    """
    filename = os.path.basename(filepath)
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''

    if ext not in SUPPORTED_EXTENSIONS:
        raise RuntimeError(f"Unsupported file type: .{ext}")

    # Step 1: Upload file to text-extract-api
    try:
        with open(filepath, 'rb') as f:
            resp = requests.post(
                f"{TEXT_EXTRACT_URL}/ocr/upload",
                files={"file": (filename, f)},
                data={
                    "strategy": "easyocr",
                    "ocr_cache": "true",
                },
                timeout=30,
            )
        resp.raise_for_status()
        result = resp.json()
    except requests.exceptions.ConnectionError:
        raise RuntimeError(
            "text-extract-api is not running. Start it with: "
            "docker-compose -f docker-compose.extract.yml up -d"
        )
    except Exception as e:
        raise RuntimeError(f"Upload failed: {e}")

    # The API may return the result directly or give us a task_id to poll
    # Check if result is already available
    if "result" in result and result["result"]:
        return _clean_text(result["result"])

    task_id = result.get("task_id")
    if not task_id:
        # Some versions return text directly in different fields
        for key in ["text", "content", "output", "markdown"]:
            if key in result and result[key]:
                return _clean_text(result[key])
        raise RuntimeError(f"No task_id or result in response: {result}")

    # Step 2: Poll for result
    poll_url = f"{TEXT_EXTRACT_URL}/ocr/result/{task_id}"
    start = time.time()
    while time.time() - start < timeout:
        try:
            poll_resp = requests.get(poll_url, timeout=10)
            poll_resp.raise_for_status()
            poll_data = poll_resp.json()

            status = poll_data.get("status", "")

            if status == "SUCCESS" or status == "done":
                text = poll_data.get("result", "") or poll_data.get("text", "")
                if text:
                    return _clean_text(text)

            if status in ("FAILURE", "failed", "error"):
                raise RuntimeError(f"Extraction failed: {poll_data.get('error', 'unknown')}")

            # Still processing, wait and retry
            time.sleep(2)

        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Polling failed: {e}")

    raise RuntimeError(f"Extraction timed out after {timeout}s")


def _clean_text(text: str) -> str:
    """Clean up extracted text - remove excessive whitespace."""
    if isinstance(text, list):
        text = "\n".join(str(item) for item in text)
    text = str(text)
    # Collapse multiple blank lines into one
    lines = text.split('\n')
    cleaned = []
    prev_blank = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if not prev_blank:
                cleaned.append('')
            prev_blank = True
        else:
            cleaned.append(stripped)
            prev_blank = False
    return '\n'.join(cleaned).strip()


def is_healthy() -> bool:
    """Check if text-extract-api is reachable."""
    try:
        resp = requests.get(f"{TEXT_EXTRACT_URL}/", timeout=3)
        return resp.status_code < 500
    except Exception:
        return False
