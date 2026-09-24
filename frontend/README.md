# Retail Intelligence — frontend

Vite + React. Mirrors the FQC `chat/frontend` structure:

| file | role |
|---|---|
| `src/App.jsx` | mounts `<Dashboard>` + `<ChatWidget>`; a chat answer bumps a signal that refetches the dashboard |
| `src/Dashboard.jsx` / `.css` | KPI cards, revenue/hour bar charts, top categories, cashier leaderboard, top products, low stock, recent bills (with filter), revenue-trend window presets. Polls `/dashboard` every 30 s. Light theme, WGDeepInsight brand. |
| `src/ChatWidget.jsx` / `.css` | Centered chat panel. Streams `/chat/stream` (SSE): renders the result table from `meta`, appends answer `token`s live, swaps in the safety-checked `done` text. Every answer has a "show how this was answered" panel — the SQL + each pipeline stage with timings. Sends the last 4 turns as history. |
| `src/api.js` | `API_BASE` — `http://127.0.0.1:8000` in dev, same-origin when built. Override with `VITE_API_BASE`. |
| `src/Logo.jsx` | WGDeepInsight mark (SVG). Drop a real `public/logo.png` and swap for an `<img>`. |

## Commands

```bash
npm install
npm run dev      # http://localhost:5173  (API on :8000)
npm run build    # -> dist/  (FastAPI serves this at / )
```

## Not ported from the reference

The FQC ChatWidget's hands-free **voice mode** (mic → VAD → Whisper → send) is
left out — it needs a `/speech-to-text` endpoint backed by faster-whisper. Add
that endpoint and lift `ChatWidget`'s voice code from the reference if needed.
