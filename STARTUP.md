# Local Development Setup

Run each service in a separate terminal tab. Start in this order:

## 1. ADK Agent (port 8081)

```bash
cd ~/Desktop/ora-navigator/adk_agent && \
  source ~/Desktop/ora-navigator/.venv/bin/activate && \
  adk web . --port 8081
```

## 2. Backend (port 5002)

```bash
cd ~/Desktop/ora-navigator/backend && \
  source ~/Desktop/ora-navigator/.venv/bin/activate && \
  uvicorn main:app --host 127.0.0.1 --port 5002
```

## 3. Frontend (port 3001)

```bash
cd ~/Desktop/ora-navigator/frontend && npm run dev -- --port 3001
```

## Open in browser

```
http://localhost:3001
```

Make sure `.env` is populated (copy `.env.example` and fill in DATABASE_URL, JWT_SECRET, GOOGLE_CLOUD_PROJECT).
