# Cloud Run JOB image for the KB web scrape.
#
# Playwright's own image, because the browser and its system libraries are the
# hard part and 249 of the 382 KB documents come from JS-rendered accordions
# that plain HTTP cannot see. The image is large (~1.5GB) — irrelevant for a
# Job, which has no cold-start path and runs on a schedule or a button press.
FROM mcr.microsoft.com/playwright/python:v1.49.0-jammy

WORKDIR /app

COPY kb_scraper/requirements.txt ./kb_scraper/requirements.txt
RUN pip install --no-cache-dir -r kb_scraper/requirements.txt

# Chromium only; the other two browsers would add ~700MB for nothing.
RUN playwright install chromium --with-deps

# The job reuses the backend's models, DB wiring and datastore client rather
# than duplicating them — one definition of a document, one migration path.
COPY backend/db.py backend/models.py backend/datastore_manager.py ./backend/
# The doc_id -> morgan.edu URL index. The seeded documents carry no source_url
# in struct_data, so without this every page maps to nothing and the crawl
# reports all 382 as new.
COPY backend/kb_structured/_all_documents.jsonl ./backend/kb_structured/_all_documents.jsonl
COPY kb_scraper/ ./kb_scraper/

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend:/app/kb_scraper

CMD ["python", "kb_scraper/run.py"]
