# Legacy RAG Scripts (v1.0 - v2.0)

These scripts are from the original RAG pipeline era when CS Navigator used Pinecone vector DB + OpenAI GPT-3.5-turbo. They are kept for reference but are no longer used in production.

**Current system uses:** Google ADK + Vertex AI Search + Gemini 2.5 Flash

| Script | Purpose |
|--------|---------|
| `ingestion.py` | Pinecone KB document ingestion |
| `admin.py` | Pinecone admin panel |
| `chatbot.py` | Legacy test harness |
| `check_count.py` | Pinecone vector count utility |
| `count_chunks.py` | Pinecone chunk count utility |
| `convert_and_upload.py` | Legacy KB format converter |
| `empty_pincecone.py` | Pinecone index cleanup |
| `cache_warmup.py` | Redis cache pre-warming |
| `init_db.py` | DB initialization (now in main.py startup) |
| `migrate_db.py` | DB migration utility (fully migrated) |
