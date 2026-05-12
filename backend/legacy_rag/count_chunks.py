# count_chunks.py
import os, json
from dotenv import load_dotenv
from langchain.text_splitter import TokenTextSplitter

BASE_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(BASE_DIR, "data_sources")

def normalize_keys(obj):
    if isinstance(obj, dict):
        return {k.lower().replace("head", "chair"): normalize_keys(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [normalize_keys(v) for v in obj]
    return obj

splitter = TokenTextSplitter(
    chunk_size=800,
    chunk_overlap=150,
    model_name="gpt-3.5-turbo",
)

for fn in sorted(f for f in os.listdir(DATA_DIR) if f.lower().endswith(".json")):
    with open(os.path.join(DATA_DIR, fn), encoding="utf-8") as f:
        pretty = json.dumps(normalize_keys(json.load(f)), indent=2, ensure_ascii=False)
    n = len(splitter.split_text(pretty))
    print(f"{fn:30} → {n:3d} chunks")
