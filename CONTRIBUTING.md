# Contributing to CS Navigator

CS Navigator is a research project at Morgan State University's Computer Science Department. We welcome contributions from students, faculty, and the open source community.

## Getting Started

1. Fork the repository
2. Clone your fork: `git clone https://github.com/YOUR_USERNAME/cs-navigator.git`
3. Create a branch: `git checkout -b feature/your-feature`
4. Make your changes
5. Push and open a Pull Request against `dev`

## Development Setup

```bash
# Copy environment template
cp .env.example .env
# Fill in your API keys and database URL

# Start ADK Agent (Terminal 1)
cd adk_agent && pip install google-adk && adk web . --port 8080

# Start Backend (Terminal 2)
cd backend && pip install -r requirements.txt && uvicorn main:app --host 127.0.0.1 --port 5001

# Start Frontend (Terminal 3)
cd frontend && npm install && npm run dev -- --port 3000
```

## Branch Strategy

- `main` - Production code. Never push directly.
- `dev` - Active development. PRs go here.

## What Can I Contribute?

### Knowledge Base Updates
The chatbot's accuracy depends on its knowledge base. If you find wrong or missing information:
1. Open an issue with the "knowledge-base" label
2. Or edit the JSON files in `backend/kb_structured/` and submit a PR

### Bug Fixes
Found a bug? Check the [Issues](https://github.com/theaayushstha1/cs-navigator/issues) tab first. If it's not reported:
1. Open an issue with the "bug" label
2. Include steps to reproduce and the bot's response

### Frontend Improvements
The frontend is React 19 + Vite. Components are in `frontend/src/components/`.

### Backend Features
The backend is FastAPI (Python 3.11). Main entry point is `backend/main.py`.

## Code Style

- Python: Follow existing patterns in `main.py`. No additional linting required.
- JavaScript/JSX: Follow existing React patterns. No strict linting enforced.
- Keep it simple. Don't over-engineer.

## PR Guidelines

- One feature/fix per PR
- Include a clear description of what changed and why
- Test locally with all 3 services running before submitting
- Never commit `.env`, credentials, or API keys
- Screenshots for UI changes

## Questions?

- Open an issue
- Contact the CS department at compsci@morgan.edu or (443) 885-3962
