# Changelog

All notable changes to CS Navigator are documented here.

## [5.0] - 2026-04-04
### Added
- DatabaseSessionService for multi-instance session persistence
- Grounding gate that catches agent hallucinations via KB chunk count
- 3-layer follow-up resolver (regex override, entity focus, LLM fallback)
- Course context engine (prereqs, schedules, faculty pre-computed on backend)
- Canvas LMS REST API integration with lazy loading
- Self-healing research pipeline (detect, cluster, research, suggest KB fixes)
- Guest personal query interception (redirects to signup)
- Data source tracking (manual_entry vs pdf_parse vs banner_scrape)
- KB failure auto-retry with 2s delay
- 43-category Promptfoo red team security audit
- 9 agent security rules (jailbreak, role-play, calibration framing)
- Cloud Scheduler cron jobs for memory consolidation

### Changed
- Session TTL extended from 30 minutes to 24 hours
- min-instances bumped to 2 for ADK and backend
- Registration restricted to morgan.edu (test.com gated by env var)

### Fixed
- Guest chat fabricating random GPAs from hardcoded array
- Context bleed between unrelated follow-up questions
- Agent contradicting itself about DegreeWorks access mid-conversation
- ADK session 404 errors during multi-instance load balancing
- Profile picture CSS leaking to non-sidebar elements
- TTS button not showing stop state during playback
- Ticket attachment uploads failing on large screenshots

### Removed
- Manual DegreeWorks entry form (unverified data risk)
- Bookmarklet sync (replaced by Banner auto-sync + PDF upload)
- Hardcoded admin credentials in seed scripts

## [4.3] - 2026-04-01
### Added
- Email verification and forgot password flow
- Auto-research pipeline with failed query clustering
- Structured KB v7 with 51 documents

## [4.0] - 2026-03-12
### Fixed
- Agent accuracy improved from 39% to 100% via fresh session strategy
- Semantic caching for similar question matching

## [3.0] - 2026-03-09
### Added
- 8-specialist multi-agent architecture
- Promptfoo security test suite (23 tests)

### Changed
- Replaced single agent with specialist routing

## [2.2] - 2026-03-08
### Added
- Multi-tier caching (L1 in-memory + L2 Redis Cloud)
- SSE streaming for real-time chat

## [2.0] - 2026-03-05
### Changed
- Migrated from RAG pipeline to Google ADK Agent Engine
- Replaced Pinecone + OpenAI with Vertex AI Search + Gemini

## [1.0] - 2026-02-15
### Added
- Initial release with RAG pipeline
- Pinecone vector DB + OpenAI GPT-3.5-turbo
- Basic chat interface
- AWS EC2 deployment
