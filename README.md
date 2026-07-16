# Jarvis AI Agent

A personal J.A.R.V.I.S.: a cinematic command-center dashboard with a full voice
loop — talk to it, it thinks with Claude, and answers back in a cloned voice —
plus read-only access to Gmail and Google Calendar.

Built following [Build Your Own Jarvis with Claude](https://cindyzhu.com.au/guides/build-your-own-jarvis-with-claude.html),
then extended.

## Components

| Path | What it is |
|------|------------|
| `dashboard.html` | Self-contained HUD: 680-point energy sphere (canvas), connector status, activity log, funnel bars, ticker, BRIEF ME + mic + hands-free wake ("Hey Jarvis" / clap) |
| `jarvis_data.js` | All dashboard content (`window.JARVIS_DATA`) — edit this, not the HTML |
| `scripts/jarvis_server.mjs` | Voice-loop server (Node, zero deps, port 8330): serves the dashboard, `POST /ask` → speech-to-text → persistent `claude -p` (JARVIS persona) → Fish Audio TTS |
| `scripts/fish_tts.sh` | Text → mp3 via Fish Audio API |
| `scripts/generate_brief.sh` | Renders `jarvis_brief.txt` → `jarvis_brief.mp3` (the BRIEF ME audio) |
| `Start Jarvis.command` | macOS double-click launcher |
| `index.html` + `js/` + `css/` + `assets/` | Separate Three.js scroll landing page (`generate_assets.py` builds the .glb assets) |

## Setup

1. `claude` CLI installed and logged in (the voice brain runs on your Claude subscription).
2. Fish Audio account; in `~/.zshrc`:
   ```sh
   export FISH_API_KEY="..."
   export FISH_VOICE_ID="..."   # optional voice from fish.audio library
   ```
3. Gmail/Calendar (optional): authorize the claude.ai Gmail and Google Calendar
   connectors (`/mcp` in an interactive `claude` session). The server allowlists
   **read-only** tools — Jarvis can search/read mail and look up events, never
   send/create/delete.
4. Run `Start Jarvis.command` (or `node scripts/jarvis_server.mjs`) → http://localhost:8330

Env knobs: `JARVIS_MODEL` (default `haiku`), `JARVIS_PORT` (8330),
`JARVIS_GMAIL=0` to disable connectors, `FISH_MODEL` (default `s2.1-pro-free`).

## Notes

- Mic transcription uses the browser's built-in speech recognition (free).
  Fish's `/v1/asr` fallback path exists but needs paid Fish API credit.
- The server keeps one warm `claude` process in stream-json mode; replies
  resolve on the assistant event but the next question is gated on the turn's
  `result` event (pairing breaks otherwise).
- No secrets live in this repo — keys come from the environment / `~/.zshrc`.
