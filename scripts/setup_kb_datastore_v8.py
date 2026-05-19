"""
Build the v8 Vertex AI Search datastore from the restructured KB.

Differences from setup_kb_datastore.py (v7):
  - DATASTORE_ID = "oranavigator-kb-v8"
  - rglob (not 2-level glob) — picks up nested folders like
    _generated_compliance/iacuc/sops/foo.json that the old script missed
  - struct_data adds `file_path` and `playwright_verified` for downstream
    structured-filter work

Run:
    python scripts/setup_kb_datastore_v8.py
"""

import json
import os
import re
import sys
from pathlib import Path

from google.api_core.client_options import ClientOptions
from google.api_core.exceptions import NotFound
from google.cloud import discoveryengine_v1 as discoveryengine
from google.protobuf.struct_pb2 import Struct

PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "infra-vertex-494621-v1")
LOCATION = "us"
COLLECTION = "default_collection"
DATASTORE_ID = "oranavigator-kb-v8"
DISPLAY_NAME = "ORA Navigator KB v8"

KB_DIR = Path(__file__).parent.parent / "backend" / "kb_structured"
API_ENDPOINT = f"{LOCATION}-discoveryengine.googleapis.com"

PARENT_COLLECTION = (
    f"projects/{PROJECT_ID}/locations/{LOCATION}/collections/{COLLECTION}"
)
DATASTORE_NAME = f"{PARENT_COLLECTION}/dataStores/{DATASTORE_ID}"
BRANCH = f"{DATASTORE_NAME}/branches/default_branch"

client_options = ClientOptions(api_endpoint=API_ENDPOINT)


def create_datastore() -> str:
    """Create the datastore (idempotent)."""
    ds_client = discoveryengine.DataStoreServiceClient(client_options=client_options)

    try:
        existing = ds_client.get_data_store(name=DATASTORE_NAME)
        print(f"[OK] Datastore already exists: {existing.name}")
        return existing.name
    except NotFound:
        pass

    print(f"[CREATE] {DATASTORE_NAME}")
    datastore = discoveryengine.DataStore(
        display_name=DISPLAY_NAME,
        industry_vertical=discoveryengine.IndustryVertical.GENERIC,
        solution_types=[discoveryengine.SolutionType.SOLUTION_TYPE_SEARCH],
        content_config=discoveryengine.DataStore.ContentConfig.CONTENT_REQUIRED,
    )

    operation = ds_client.create_data_store(
        parent=PARENT_COLLECTION,
        data_store=datastore,
        data_store_id=DATASTORE_ID,
    )
    print("[WAIT] Creating datastore (~30s)...")
    result = operation.result(timeout=300)
    print(f"[OK] Created: {result.name}")
    return result.name


def upload_kb_files() -> tuple[int, int]:
    """Upload all KB JSON files (rglob handles nested tree)."""
    doc_client = discoveryengine.DocumentServiceClient(client_options=client_options)

    json_files = sorted(p for p in KB_DIR.rglob("*.json") if not p.name.startswith("_"))
    print(f"[UPLOAD] {len(json_files)} KB files from {KB_DIR} (recursive)")

    success = 0
    fail = 0

    for i, path in enumerate(json_files, 1):
        try:
            data = json.loads(path.read_text())
        except Exception as e:
            print(f"  [{i}/{len(json_files)}] SKIP {path.name}: parse error {e}")
            fail += 1
            continue

        doc_id = re.sub(r"[^a-zA-Z0-9_-]", "_", data.get("doc_id") or path.stem)
        content_text = data.get("content", "") or ""
        if not content_text:
            print(f"  [{i}/{len(json_files)}] SKIP {path.name}: no content field")
            fail += 1
            continue

        struct = Struct()
        struct.update({
            "doc_id": data.get("doc_id", doc_id),
            "title": data.get("title", path.stem),
            "category": data.get("category", "general"),
            "subcategory": data.get("subcategory", ""),
            "source_file": path.name,
            "file_path": str(path.relative_to(KB_DIR)),
            "playwright_verified": bool(data.get("playwright_verified", False)),
        })

        doc = discoveryengine.Document(
            name=f"{BRANCH}/documents/{doc_id}",
            struct_data=struct,
            content=discoveryengine.Document.Content(
                raw_bytes=content_text.encode("utf-8"),
                mime_type="text/plain",
            ),
        )

        try:
            request = discoveryengine.UpdateDocumentRequest(
                document=doc, allow_missing=True
            )
            doc_client.update_document(request=request)
            if i % 25 == 0 or i == len(json_files):
                print(f"  [{i}/{len(json_files)}] OK   {doc_id}")
            success += 1
        except Exception as e:
            print(f"  [{i}/{len(json_files)}] FAIL {doc_id}: {e}")
            fail += 1

    return success, fail


def main() -> int:
    print(f"[CONFIG] project={PROJECT_ID}  datastore={DATASTORE_ID}  location={LOCATION}")

    if not KB_DIR.exists():
        print(f"[FATAL] KB directory not found: {KB_DIR}")
        return 1

    create_datastore()
    success, fail = upload_kb_files()

    print()
    print(f"[RESULT] success={success}  fail={fail}")
    print(f"[NEXT]   Datastore resource name (for env / agent.py):")
    print(f"         {DATASTORE_NAME}")
    print(f"[NOTE]   Indexing typically takes 5–30 minutes before searches return results.")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
