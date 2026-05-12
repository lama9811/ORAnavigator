# reset_index.py
import os
from dotenv import load_dotenv
from pinecone import Pinecone, ServerlessSpec

load_dotenv()
API_KEY    = os.getenv("PINECONE_API_KEY")
INDEX_NAME = os.getenv("PINECONE_INDEX_NAME")
REGION     = os.getenv("PINECONE_ENV") or "us-east-1"   # your region
DIM        = 1536                                       # your embedding dim
METRIC     = "cosine"

pc = Pinecone(api_key=API_KEY)

# Delete if it exists
if INDEX_NAME in pc.list_indexes().names():
    print(f"Deleting index {INDEX_NAME}…")
    pc.delete_index(INDEX_NAME)

# Recreate
print(f"Recreating index {INDEX_NAME}…")
pc.create_index(
    name=INDEX_NAME,
    dimension=DIM,
    metric=METRIC,
    spec=ServerlessSpec(cloud="aws", region=REGION),
)
print("Done.")
