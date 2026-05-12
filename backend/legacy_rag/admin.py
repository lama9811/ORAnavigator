import os
import time
import shutil

from fastapi import FastAPI, Request, Query, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv
from pinecone import Pinecone, ServerlessSpec

from ingestion import ingest_data  # your ingestion.py with ingest_data()

# ─── Load env ────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(__file__)
load_dotenv(os.path.join(BASE_DIR, ".env"))

PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
PINECONE_ENV     = os.getenv("PINECONE_ENV")
PINECONE_INDEX   = os.getenv("PINECONE_INDEX_NAME")
missing = [k for k in ["PINECONE_API_KEY", "PINECONE_ENV", "PINECONE_INDEX_NAME"] if not os.getenv(k)]
if missing:
    raise RuntimeError(f"Missing env vars in admin.py: {', '.join(missing)}")

def get_pinecone_index():
    pc = Pinecone(api_key=PINECONE_API_KEY, environment=PINECONE_ENV)
    if PINECONE_INDEX not in pc.list_indexes().names():
        pc.create_index(
            name=PINECONE_INDEX,
            dimension=1536,
            metric="cosine",
            spec=ServerlessSpec(cloud="aws", region=PINECONE_ENV),
        )
    return pc.Index(PINECONE_INDEX)

DATA_DIR     = os.path.join(BASE_DIR, "data_sources")
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR   = os.path.join(BASE_DIR, "static")
os.makedirs(DATA_DIR, exist_ok=True)

app = FastAPI()
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

templates = Jinja2Templates(directory=TEMPLATE_DIR)


@app.get("/ping")
async def ping():
    return {"status": "pong"}


@app.get("/admin/update", response_class=HTMLResponse)
async def update_form(
    request:  Request,
    filename: str = Query(None),
    query:    str = Query(None),
):
    # list all JSON files
    all_files = sorted(f for f in os.listdir(DATA_DIR) if f.lower().endswith(".json"))

    # optionally filter by search query
    matched = []
    if query:
        q = query.lower()
        for fn in all_files:
            content = open(os.path.join(DATA_DIR, fn), "r", encoding="utf-8").read().lower()
            if q in content:
                matched.append(fn)

    # load the selected file’s content
    content = ""
    if filename in all_files:
        content = open(os.path.join(DATA_DIR, filename), "r", encoding="utf-8").read()

    return templates.TemplateResponse("update.html", {
        "request":   request,
        "all_files": all_files,
        "matched":   matched,
        "selected":  filename,
        "content":   content,
        "query":     query or ""
    })


@app.post("/admin/update")
async def receive_update(
    request:  Request,
    file:     UploadFile = File(None),
    raw_json: str        = Form(None),
    filename: str        = Form(None),
    query:    str        = Form(None),
):
    # Decide where to write
    if file and file.filename:
        dest_name = file.filename
        dest_path = os.path.join(DATA_DIR, dest_name)
        with open(dest_path, "wb") as buf:
            shutil.copyfileobj(file.file, buf)

    elif raw_json is not None:
        dest_name = filename or f"update_{int(time.time())}.json"
        dest_path = os.path.join(DATA_DIR, dest_name)
        with open(dest_path, "w", encoding="utf-8") as buf:
            buf.write(raw_json)

    else:
        raise HTTPException(400, "No file uploaded or JSON provided")

    # Re-ingest **all** JSON files (your ingest_data() reads DATA_DIR)
    ingest_data()

    # Redirect back to the form
    redirect_url = f"/admin/update?filename={dest_name}"
    if query:
        redirect_url += f"&query={query}"
    return RedirectResponse(redirect_url, status_code=303)
