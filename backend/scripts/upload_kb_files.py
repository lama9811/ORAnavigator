#!/usr/bin/env python3
"""
Upload Knowledge Base Files to GCS + Vertex AI Datastore
=========================================================
This script uploads all JSON files from data_sources/ to the cloud KB.

Usage:
    python upload_kb_files.py                    # Upload all JSON files
    python upload_kb_files.py forms.json         # Upload specific file
    python upload_kb_files.py --list             # List current datastore docs
    python upload_kb_files.py --sync             # Re-sync all docs
"""

import os
import sys
import json
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datastore_manager import (
    upload_document,
    list_datastore_documents,
    sync_datastore,
    GCS_BUCKET_NAME,
    DATASTORE_ID,
)

# Data sources directory
DATA_SOURCES_DIR = Path(__file__).parent / "data_sources"

# Files to upload (JSON files that should be in the KB)
KB_FILES = [
    "Department.json",
    "advising.json",
    "classes.json",
    "degree.json",
    "forms.json",
    "academic_resources.json",
    "career and educational resource.json",
    "scmns_info.json",
    "financial_aid.json",
    "registration.json",
    "morgan_state calendar.json",
    "Earl S. Richardson Library.json",
    "upcoming tracks.json",
]


def list_current_docs():
    """List all documents currently in the datastore."""
    print("\n[LIST] Current documents in Vertex AI Datastore:")
    print(f"   Bucket: {GCS_BUCKET_NAME}")
    print(f"   Datastore: {DATASTORE_ID.split('/')[-1]}\n")

    try:
        docs = list_datastore_documents()
        if not docs:
            print("   (No documents found)")
            return

        print(f"   {'Filename':<45} {'Size':>10}")
        print("   " + "-" * 57)
        for doc in docs:
            size_kb = doc['size'] / 1024 if doc['size'] else 0
            print(f"   {doc['filename']:<45} {size_kb:>8.1f} KB")
        print(f"\n   Total: {len(docs)} documents")
    except Exception as e:
        print(f"   [ERROR] Error listing documents: {e}")


def upload_file(filename: str) -> bool:
    """Upload a single file to the cloud KB."""
    filepath = DATA_SOURCES_DIR / filename

    if not filepath.exists():
        print(f"   [ERROR] File not found: {filepath}")
        return False

    try:
        # Read file content
        with open(filepath, "rb") as f:
            content = f.read()

        # Determine content type
        if filename.endswith(".json"):
            content_type = "application/json"
        elif filename.endswith(".txt"):
            content_type = "text/plain"
        else:
            content_type = "text/plain"

        # Upload to GCS + Datastore
        print(f"   [UPLOAD] {filename}...", end=" ", flush=True)
        result = upload_document(filename, content, content_type)

        if result["success"]:
            print("[OK]")
            return True
        else:
            print(f"[FAIL] {result['message']}")
            return False

    except Exception as e:
        print(f"[ERROR] {e}")
        return False


def upload_all_files():
    """Upload all KB files to the cloud."""
    print("\n[UPLOAD] Uploading Knowledge Base files to Google Cloud...")
    print(f"   Source: {DATA_SOURCES_DIR}")
    print(f"   Target: gs://{GCS_BUCKET_NAME}/\n")

    success_count = 0
    fail_count = 0

    for filename in KB_FILES:
        if upload_file(filename):
            success_count += 1
        else:
            fail_count += 1

    print(f"\n   [OK] Uploaded: {success_count}")
    if fail_count:
        print(f"   [FAIL] Failed: {fail_count}")

    return fail_count == 0


def sync_all():
    """Re-sync all documents in the datastore."""
    print("\n[SYNC] Syncing datastore with GCS bucket...")
    try:
        result = sync_datastore()
        if result["success"]:
            print(f"   [OK] {result['message']}")
            return True
        else:
            print(f"   [FAIL] {result['message']}")
            return False
    except Exception as e:
        print(f"   [ERROR] {e}")
        return False


def main():
    print("=" * 60)
    print("  CS Navigator - Knowledge Base Uploader")
    print("=" * 60)

    # Parse arguments
    if len(sys.argv) > 1:
        arg = sys.argv[1]

        if arg == "--list":
            list_current_docs()
        elif arg == "--sync":
            sync_all()
        elif arg.endswith(".json"):
            # Upload specific file
            print(f"\n[UPLOAD] Uploading single file: {arg}")
            upload_file(arg)
        else:
            print(f"\nUnknown argument: {arg}")
            print(__doc__)
            sys.exit(1)
    else:
        # Upload all files
        upload_all_files()
        print("\n" + "-" * 60)
        sync_all()
        print("\n" + "-" * 60)
        list_current_docs()

    print("\n[DONE] Complete!\n")


if __name__ == "__main__":
    main()
