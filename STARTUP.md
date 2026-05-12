# Local Development Setup

Run each command in a separate terminal tab. Start in this order:

## 1. ADK Engine (port 8080)

```bash
cd ~/Desktop/Projects/google-ai-engine-research/adk_deploy && /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 -m google.adk.cli web . --port 8080
```

## 2. Backend (port 8000)

```bash
cd ~/Desktop/Projects/cs\ chatbot/cs-chatbot/backend && uvicorn main:app --reload --port 8000
```

## 3. Frontend (port 5173)

```bash
cd ~/Desktop/Projects/cs\ chatbot/cs-chatbot/frontend && npm run dev
```

## Open in browser

```
http://localhost:5173
```
