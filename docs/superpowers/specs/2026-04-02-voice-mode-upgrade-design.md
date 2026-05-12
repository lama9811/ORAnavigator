# Voice Mode Upgrade: Real-Time Conversational Voice

**Date:** April 2, 2026
**Status:** Design approved, ready for implementation planning
**Branch:** version-5.0-canvas

## Problem

Current voice mode feels like a walkie-talkie, not a conversation. Three main pain points:

1. **Slow end-of-speech detection (3-5s):** Browser SpeechRecognition waits for silence timeout before processing. User finishes talking and stares at nothing for seconds.
2. **No streaming response (5-10s wait):** Voice mode uses sync `/chat` endpoint. Waits for full LLM response, then sends entire text to TTS, then plays. User waits 5-10s of silence between speaking and hearing the answer.
3. **No feedback or interruption:** No interim transcription (user can't see their words appearing). No barge-in (can't interrupt a long TTS response). No visual feedback beyond a pulsing circle.

## Solution

Replace slow components with streaming alternatives while keeping the proven ADK agent pipeline intact. Zero changes to agent accuracy or KB grounding.

### Architecture: Before vs After

**Before (current):**
```
User speaks
  -> Browser SpeechRecognition (waits 3-5s for silence)
  -> Full transcript arrives
  -> POST /chat (sync, waits for complete response: 2-8s)
  -> Full response text -> OpenAI TTS API (waits for full audio: 300ms-1s)
  -> Play entire audio clip
  -> Resume listening

Total perceived latency: 5-15 seconds of dead silence
```

**After (upgrade):**
```
User speaks
  -> @ricky0123/vad-web detects end of speech (<50ms)
  -> Audio streamed to Deepgram Nova-3 STT (interim results shown in real-time)
  -> Final transcript -> POST /chat/stream (SSE, chunks arrive in 200-500ms)
  -> Sentence splitter buffers text chunks
  -> Each complete sentence -> OpenAI TTS API (parallel, pipelined)
  -> First sentence audio plays while LLM still generating
  -> User can interrupt (barge-in) at any time by speaking

Total perceived latency: 1-2 seconds to first spoken word
```

## Components

### 1. VAD: @ricky0123/vad-web (Silero VAD)

**What it does:** Runs a tiny ML model (Silero VAD, ~2MB ONNX) in the browser via WebAssembly. Detects speech start/end with <1ms latency per audio frame. Replaces the browser's built-in silence detection which takes 3-5 seconds.

**Integration:**
- npm package: `@ricky0123/vad-web`
- Initializes with `MicVAD.new()`, provides `onSpeechStart` and `onSpeechEnd` callbacks
- `onSpeechEnd` returns the audio segment as a Float32Array
- Configurable: `minSpeechFrames` (minimum speech length), `positiveSpeechThreshold` (sensitivity)
- Runs entirely in browser, no backend needed

**Settings to tune:**
- `positiveSpeechThreshold: 0.8` (default 0.5, higher = less sensitive to background noise)
- `minSpeechFrames: 5` (minimum ~150ms of speech before triggering)
- `redemptionFrames: 8` (how many silent frames before end-of-speech, ~240ms)

### 2. STT: Deepgram Nova-3 Streaming

**What it does:** Streams raw audio to Deepgram via WebSocket, gets back interim and final transcripts in real-time. User sees their words appearing as they speak.

**Integration:**
- Backend WebSocket proxy at `/ws/stt` (Deepgram API key stays server-side)
- Frontend connects to proxy, sends audio chunks from VAD
- Proxy forwards to `wss://api.deepgram.com/v1/listen`
- Returns interim results (gray text, updating) and final results (committed text)
- Deepgram features: `smart_format`, `punctuate`, `endpointing` (configurable silence threshold)

**Backend proxy endpoint:**
```python
@app.websocket("/ws/stt")
async def stt_proxy(websocket: WebSocket):
    # Authenticate user via token in query params
    # Open WebSocket to Deepgram
    # Relay audio from client -> Deepgram
    # Relay transcripts from Deepgram -> client
```

**Cost:** ~$0.0059/minute (Nova-3), negligible for academic use.

**Fallback:** If Deepgram is unavailable or user has no internet for WebSocket, fall back to browser SpeechRecognition API (current approach). VAD still works either way.

### 3. Streaming Chat: Use Existing /chat/stream

**No changes needed to the backend.** The `/chat/stream` SSE endpoint already exists and streams text chunks. Voice mode currently uses the sync `/chat` endpoint. Just switch to `/chat/stream`.

The voice mode handler will:
1. Open SSE connection to `/chat/stream`
2. Receive text chunks as they arrive
3. Buffer chunks and split on sentence boundaries (`.`, `!`, `?`, `\n`)
4. Feed each complete sentence to the TTS pipeline

### 4. TTS: Sentence-by-Sentence Streaming with OpenAI TTS

**What it does:** Instead of waiting for the full response and sending it all to TTS at once, split into sentences and start speaking the first sentence while the LLM is still generating the rest.

**Pipeline:**
```
LLM chunk: "The CS department is"
  -> buffer (not a complete sentence yet)
LLM chunk: " located in McMechen Hall, Room 507."
  -> buffer has complete sentence -> send to TTS
  -> TTS audio arrives -> start playing
LLM chunk: " The phone number is"
  -> buffer (accumulating next sentence)
LLM chunk: " (443) 885-3962."
  -> complete sentence -> send to TTS (queued behind first)
  -> plays automatically after first finishes
```

**Audio queue:** Maintain an ordered queue of audio blobs. Each sentence gets its own TTS request. Requests are fired in parallel (up to 2 concurrent). Playback is sequential (sentence order preserved).

**Sentence splitter logic:**
- Split on: `. `, `! `, `? `, `\n`, `:\n`
- Don't split on: `Dr.`, `St.`, `Mr.`, `Ms.`, numbers like `3.953`
- Minimum sentence length: 20 chars (avoid splitting on short fragments)
- Flush remaining buffer when SSE stream ends (last sentence may not end with punctuation)

**Keep OpenAI TTS** (current, working, good quality). No need to add ElevenLabs dependency. The latency improvement comes from sentence-level streaming, not from switching TTS providers.

### 5. Barge-In (User Interruption)

**What it does:** If the user starts speaking while TTS is playing, immediately stop TTS and start listening.

**Implementation:**
- VAD's `onSpeechStart` callback fires when user starts talking
- If TTS is currently playing:
  1. Pause/stop current audio playback
  2. Clear the audio queue (don't play remaining sentences)
  3. Cancel any in-flight TTS requests
  4. Transition to "listening" state
- The new speech gets processed normally (VAD -> STT -> chat/stream -> TTS)

### 6. UI: Voice Mode Overlay

**State machine:**
```
IDLE -> (user clicks voice button) -> LISTENING
LISTENING -> (VAD detects end of speech) -> PROCESSING
PROCESSING -> (first TTS audio ready) -> SPEAKING
SPEAKING -> (all audio played) -> LISTENING (auto-loop)
SPEAKING -> (user speaks, barge-in) -> LISTENING
LISTENING/PROCESSING/SPEAKING -> (user clicks stop) -> IDLE
```

**Visual states:**
- **IDLE:** Blue soundwave button (current, just upgraded CSS)
- **LISTENING:** Red pulsing orb with live waveform visualization (Web Audio API analyser). Interim transcript text appearing below the orb in real-time.
- **PROCESSING:** Blue spinning orb. Shows "Thinking..." text.
- **SPEAKING:** Green orb with audio waveform. Shows the response text scrolling as it speaks. Subtitle: "Tap to interrupt"
- **Transition animations:** Smooth morph between states using CSS transitions on the orb.

**Waveform visualization:**
- Use Web Audio API `AnalyserNode` on the microphone stream
- Draw frequency bars or a smooth wave in a canvas element behind the orb
- Gives immediate visual feedback that the mic is active and hearing the user
- During TTS playback, visualize the output audio instead

## File Changes

### New Files
- `frontend/src/components/VoiceMode.jsx` - Voice mode overlay component (extracted from Chatbox)
- `frontend/src/components/VoiceMode.css` - Voice mode styles
- `frontend/src/lib/vad.js` - VAD wrapper (init, start, stop, callbacks)
- `frontend/src/lib/deepgramSTT.js` - Deepgram WebSocket STT client
- `frontend/src/lib/sentenceTTS.js` - Sentence splitter + TTS queue manager
- `backend/stt_proxy.py` - WebSocket proxy for Deepgram STT

### Modified Files
- `frontend/src/components/Chatbox.jsx` - Extract voice logic into VoiceMode component, wire up new pipeline
- `frontend/src/components/Chatbox.css` - Remove old voice styles (moved to VoiceMode.css)
- `frontend/package.json` - Add `@ricky0123/vad-web` dependency
- `backend/main.py` - Mount WebSocket route for STT proxy
- `backend/requirements.txt` - Add `websockets` if not present
- `.env.example` - Add `DEEPGRAM_API_KEY`

### Not Changed
- `adk_agent/` - Zero changes to the AI agent
- `backend/vertex_agent.py` - Zero changes to agent client
- `backend/research_agent.py` - Zero changes
- `backend/cache.py` - Cache still works (text queries are the same)
- `/chat/stream` endpoint - Already exists, no changes needed

## Environment Variables

| Variable | Required | Default | Notes |
|----------|----------|---------|-------|
| `DEEPGRAM_API_KEY` | Yes (for upgraded STT) | None | Free tier: 45,000 mins/year |
| `OPENAI_API_KEY` | Yes (existing) | Already set | Used for TTS (unchanged) |

## Fallback Strategy

Every new component has a fallback to the current working system:

| Component | Primary | Fallback | Trigger |
|-----------|---------|----------|---------|
| VAD | @ricky0123/vad-web | Browser SpeechRecognition silence detection | ONNX fails to load |
| STT | Deepgram streaming | Browser SpeechRecognition API | WebSocket fails / no API key |
| Chat | /chat/stream (SSE) | /chat (sync) | SSE connection fails |
| TTS | OpenAI TTS (sentence streaming) | Browser SpeechSynthesis | TTS API fails |

If ALL upgrades fail, the system degrades to exactly what it is today. No regression possible.

## Performance Targets

| Metric | Current | Target | How |
|--------|---------|--------|-----|
| End-of-speech detection | 3-5s | <200ms | VAD replaces browser silence detection |
| User sees transcript | Never (no interim) | Real-time | Deepgram interim results |
| Time to first spoken word | 5-15s | 1-2s | Streaming chat + sentence TTS |
| Interruption | Not possible | Instant | VAD barge-in detection |
| Visual feedback while listening | Pulsing circle | Live waveform | Web Audio API analyser |

## Implementation Order

1. **Phase 1: VAD + Interim Results** - Add @ricky0123/vad-web, enable interim results on existing browser STT. Biggest UX win for least code.
2. **Phase 2: Streaming TTS** - Switch voice mode from /chat to /chat/stream, implement sentence splitter + audio queue. Kills the long silence.
3. **Phase 3: Deepgram STT** - Add backend WebSocket proxy, replace browser STT with Deepgram streaming. Faster, more accurate transcription.
4. **Phase 4: Barge-In + Waveform** - Add interruption support, live waveform visualization. Polish.
5. **Phase 5: VoiceMode Component** - Extract all voice logic from Chatbox.jsx into dedicated VoiceMode.jsx. Clean separation.

Each phase is independently shippable and improves the experience incrementally.
