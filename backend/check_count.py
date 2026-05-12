# backend/check_count.py

import os
from dotenv import load_dotenv
from pinecone import Pinecone

# Load env
BASE_DIR = os.path.dirname(__file__)
load_dotenv(os.path.join(BASE_DIR, ".env"))

# Init Pinecone client
pc = Pinecone(
    api_key=os.getenv("PINECONE_API_KEY"),
    environment=os.getenv("PINECONE_ENV"),
)

# Get an index handle
index_name = os.getenv("PINECONE_INDEX_NAME")
index = pc.Index(index_name)

# Describe stats
stats = index.describe_index_stats()
total = stats["total_vector_count"]

print(f"Total vectors in '{index_name}': {total}")
