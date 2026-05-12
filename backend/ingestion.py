# ingestion.py

import os
import re
import json
import time
import hashlib
import asyncio
from typing import List, Dict, Optional, Any

from dotenv import load_dotenv
from langchain.text_splitter import TokenTextSplitter
from langchain_openai import OpenAIEmbeddings
from langchain_pinecone import PineconeVectorStore
from pinecone import Pinecone, ServerlessSpec

# ── ROBUST ENV LOADING ─────────────────────────────────────────────────────────
# 1. Get the absolute path of the directory containing this script (backend/)
backend_dir = os.path.dirname(os.path.abspath(__file__))

# 2. Get the parent directory (cs-chatbot root/)
root_dir = os.path.dirname(backend_dir)

# 3. Build path to .env file in the root
env_path = os.path.join(root_dir, ".env")

print(f"🔍 Checking for .env at: {env_path}")

if os.path.exists(env_path):
    print("✅ Found .env file. Loading variables...")
    load_dotenv(env_path)
else:
    print("⚠️  WARNING: .env file NOT found at root. Checking current directory...")
    # Fallback: check if it's inside backend/ for some reason
    load_dotenv() 

# ── Verify Variables ───────────────────────────────────────────────────────────
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
PINECONE_ENV     = os.getenv("PINECONE_ENV")            # e.g., "us-east-1"
PINECONE_INDEX   = os.getenv("PINECONE_INDEX_NAME")     # e.g., "vectorized-datasource"
OPENAI_API_KEY   = os.getenv("OPENAI_API_KEY")
NAMESPACE        = os.getenv("PINECONE_NAMESPACE", "docs")

# Debug print to show which specific key is missing (values hidden)
print(f"   - PINECONE_API_KEY: {'OK' if PINECONE_API_KEY else 'MISSING'}")
print(f"   - PINECONE_ENV:     {'OK' if PINECONE_ENV else 'MISSING'}")
print(f"   - PINECONE_INDEX:   {'OK' if PINECONE_INDEX else 'MISSING'}")
print(f"   - OPENAI_API_KEY:   {'OK' if OPENAI_API_KEY else 'MISSING'}")

if not all([PINECONE_API_KEY, PINECONE_ENV, PINECONE_INDEX, OPENAI_API_KEY]):
    raise RuntimeError(
        "\n❌ CRITICAL ERROR: Missing environment variables.\n"
        f"Please create a .env file at: {env_path}\n"
        "It must contain: PINECONE_API_KEY, PINECONE_ENV, PINECONE_INDEX_NAME, OPENAI_API_KEY"
    )

# ── Pinecone client & index ────────────────────────────────────────────────────
pc = Pinecone(api_key=PINECONE_API_KEY)

def _to_dict(obj):
    return obj.to_dict() if hasattr(obj, "to_dict") else obj

def ensure_index(name: str, region: str, dim: int = 1536, metric: str = "cosine") -> None:
    """Create the index if needed and wait until it's ready."""
    if name not in pc.list_indexes().names():
        pc.create_index(
            name=name,
            dimension=dim,
            metric=metric,
            spec=ServerlessSpec(cloud="aws", region=region),
        )
    # wait until ready
    for _ in range(60):
        desc = _to_dict(pc.describe_index(name))
        if desc.get("status", {}).get("ready"):
            return
        time.sleep(2)
    raise TimeoutError(f"Pinecone index '{name}' not ready after waiting.")

ensure_index(PINECONE_INDEX, PINECONE_ENV)
index = pc.Index(PINECONE_INDEX)

# ── Helpers ────────────────────────────────────────────────────────────────────
def normalize_keys(obj: Any) -> Any:
    """Recursively lowercase keys and rename 'head' → 'chair'."""
    if isinstance(obj, dict):
        return {k.lower().replace("head", "chair"): normalize_keys(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [normalize_keys(v) for v in obj]
    return obj

def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9\-]+", "-", s.lower())

def ns_exists(idx, ns: str) -> bool:
    """Return True if namespace currently exists (created after first upsert)."""
    stats = _to_dict(idx.describe_index_stats())
    return ns in (stats.get("namespaces") or {})

def load_json_documents(paths: List[str]) -> List[Dict]:
    """Return [{'text': pretty_json, 'source': filename}, ...]."""
    docs: List[Dict] = []
    for path in paths:
        if not os.path.exists(path):
            print(f"⚠️  Skipping missing file: {path}")
            continue
        fn = os.path.basename(path)
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            pretty = json.dumps(normalize_keys(raw), indent=2, ensure_ascii=False)
            docs.append({"text": pretty, "source": fn})
        except Exception as e:
            print(f"⚠️  Failed to parse {fn}: {e}")
    return docs

# ── Main ingestion ─────────────────────────────────────────────────────────────
async def ingest_data(file_paths: Optional[List[str]] = None) -> None:
    """
    1) Pick JSON files from ./data_sources (or provided list)
    2) Load & normalize
    3) Split into chunks
    4) Embed & upsert (stable IDs; per-file delete on subsequent runs)
    """
    # 1) Discover files
    if not file_paths:
        # data_sources is assumed to be inside backend/
        data_dir = os.path.join(backend_dir, "data_sources")
        if not os.path.exists(data_dir):
             print(f"⚠️  Data directory not found at: {data_dir}")
             return
        
        file_paths = [
            os.path.join(data_dir, f)
            for f in sorted(os.listdir(data_dir))
            if f.lower().endswith(".json")
        ]

    # 2) Load
    docs = load_json_documents(file_paths)
    if not docs:
        print("⚠️  No JSON documents found to ingest.")
        return

    # 3) Chunking
    splitter = TokenTextSplitter(
        chunk_size=800,
        chunk_overlap=150,
        model_name="gpt-3.5-turbo",
    )

    # 4) Embeddings + vector store handle (1536-dim model)
    embeddings = OpenAIEmbeddings(
        openai_api_key=OPENAI_API_KEY,
        model="text-embedding-3-small",
    )
    vstore = PineconeVectorStore.from_existing_index(
        embedding=embeddings,
        index_name=PINECONE_INDEX,
        namespace=NAMESPACE,
    )

    total_chunks = 0
    first_run = not ns_exists(index, NAMESPACE)

    for doc in docs:
        src = doc["source"]
        src_key = slug(src)

        # On re-ingest, remove prior vectors for that file (skip on first run)
        if first_run:
            print(f"⏭️  Namespace '{NAMESPACE}' not present yet; skipping delete for {src}.")
        else:
            index.delete(filter={"source": src}, namespace=NAMESPACE)

        # Split and build stable IDs (file slug + chunk index + short content hash)
        chunks = splitter.split_text(doc["text"])
        texts, metadatas, ids = [], [], []
        for i, chunk in enumerate(chunks):
            short = hashlib.sha1(chunk.encode("utf-8")).hexdigest()[:10]
            ids.append(f"{src_key}-{i:05d}-{short}")
            texts.append(chunk)
            metadatas.append({"source": src, "chunk": i})

        if texts:
            vstore.add_texts(texts=texts, metadatas=metadatas, ids=ids)
            total_chunks += len(texts)
            print(f"📦 {src}: upserted {len(texts)} chunks")

    # Pinecone stats can lag briefly; wait and then read per-namespace count
    time.sleep(3)
    stats = _to_dict(index.describe_index_stats())
    ns_total = stats.get("namespaces", {}).get(NAMESPACE, {}).get("vector_count", 0)
    print(f"✅ Upserted {total_chunks} chunks. Total in namespace '{NAMESPACE}': {ns_total}")

# ── Entrypoint ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    start = time.time()
    asyncio.run(ingest_data())
    print(f"✔️  Done in {time.time() - start:.1f}s")