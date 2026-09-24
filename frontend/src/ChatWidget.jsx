import { useEffect, useRef, useState } from "react";
import { API_BASE } from "./api";
import "./ChatWidget.css";

// ─── result-row rendering ────────────────────────────────────────────
// The backend can attach `meta.result` (the raw SQL rows) alongside the
// prose answer; when there's more than one row we render it as a table.
const MONEY_KEY = /(revenue|amount|billed|bill_value|avg_bill|subtotal|spend|spent|price|cost|line_total|turnover|per_customer|total_amount|value)/i;
const COUNT_KEY = /(count|bills|units|qty|quantity|footfall|seconds|stock|threshold|visits|customers|pct|percent|_id\b|\bid\b|days|hours|rank)/i;

function formatCell(key, value) {
  if (value === null || value === undefined) return "—";
  if (/(^ts$|_at$|_time$|_date$|first_visit|last_visit)/i.test(key) &&
      typeof value === "string" && !Number.isNaN(Date.parse(value))) {
    return new Date(value).toLocaleString();
  }
  if (typeof value === "number") {
    const r = Number.isInteger(value) ? value : Math.round(value * 100) / 100;
    if (MONEY_KEY.test(key) && !COUNT_KEY.test(key)) return "₹" + r.toLocaleString("en-IN");
    return r.toLocaleString("en-IN");
  }
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function GenericTable({ rows }) {
  if (!rows || rows.length <= 1) return null;
  const columns = Array.from(
    rows.reduce((set, r) => {
      Object.keys(r).forEach((k) => set.add(k));
      return set;
    }, new Set())
  );
  return (
    <div className="alert-table-wrap">
      <table className="alert-table">
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c}>{c === "_id" ? "key" : c.replace(/_/g, " ")}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {columns.map((c) => (
                <td key={c}>{formatCell(c, r[c])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function isFacetShape(rows) {
  return (
    rows.length === 1 &&
    Object.keys(rows[0]).length > 0 &&
    Object.values(rows[0]).every((v) => Array.isArray(v))
  );
}

function FacetReport({ row }) {
  const multiRowSections = Object.entries(row).filter(([, subRows]) => subRows.length > 1);
  if (multiRowSections.length === 0) return null;
  return (
    <div className="facet-report">
      {multiRowSections.map(([name, subRows]) => (
        <div className="facet-section" key={name}>
          <div className="facet-title">{name.replace(/_/g, " ")}</div>
          <GenericTable rows={subRows} />
        </div>
      ))}
    </div>
  );
}
// ─── end result-row rendering ────────────────────────────────────────

function ChatMessage({ role, content, meta }) {
  const rows = meta?.result;
  return (
    <div className={`msg msg-${role}`}>
      <div className="msg-bubble">
        <div className="msg-text">{content}</div>
        {rows && rows.length > 0 && (
          isFacetShape(rows) ? <FacetReport row={rows[0]} /> : <GenericTable rows={rows} />
        )}
      </div>
    </div>
  );
}

const SUGGESTIONS = [
  "What items are below threshold right now?",
  "Total revenue this week vs last week",
  "Which counter person has billed the most this week?",
  "Price of pro paneer 200g",
  "Show me what was in bill #15000",
  "Top 10 fastest-moving items this week",
];

export default function ChatWidget({ onAnswered }) {
  const [open, setOpen] = useState(false);
  const openRef = useRef(open);
  openRef.current = open;
  const [messages, setMessages] = useState([
    { role: "assistant", content: "Hi! Ask me about stock, billing, sales, customers, footfall or staff." },
  ]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [listening, setListening] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const scrollRef = useRef(null);

  // Sticky-scroll: only auto-follow streamed tokens while already near the bottom.
  const stickToBottomRef = useRef(true);
  function handleScroll(e) {
    const { scrollTop, scrollHeight, clientHeight } = e.currentTarget;
    stickToBottomRef.current = scrollHeight - scrollTop - clientHeight < 60;
  }
  useEffect(() => {
    if (open && stickToBottomRef.current) {
      scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
    }
  }, [messages, loading, open]);

  // Blur + freeze the dashboard behind the dialog (backdrop-filter alone is
  // unreliable across GPUs, so blur the dashboard element directly).
  useEffect(() => {
    document.body.classList.toggle("chat-open", open);
    return () => document.body.classList.remove("chat-open");
  }, [open]);

  function buildHistory() {
    const pairs = [];
    for (let i = 0; i < messages.length - 1; i++) {
      if (messages[i].role === "user" && messages[i + 1].role === "assistant") {
        pairs.push({
          question: messages[i].content,
          answer: messages[i + 1].content,
          sql: messages[i + 1].meta?.sql ?? null,
        });
      }
    }
    return pairs.slice(-4);
  }

  function patchLastMessage(patch) {
    setMessages((m) => {
      const copy = [...m];
      const last = copy[copy.length - 1];
      copy[copy.length - 1] = typeof patch === "function" ? patch(last) : { ...last, ...patch };
      return copy;
    });
  }

  async function sendQuestion(question) {
    const history = buildHistory();
    stickToBottomRef.current = true;
    setMessages((m) => [...m, { role: "user", content: question }]);
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, history }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Request failed (${res.status})`);
      }
      setMessages((m) => [...m, { role: "assistant", content: "", meta: null }]);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let sepIndex;
        while ((sepIndex = buf.indexOf("\n\n")) !== -1) {
          const rawEvent = buf.slice(0, sepIndex);
          buf = buf.slice(sepIndex + 2);
          const eventMatch = rawEvent.match(/^event: (.+)$/m);
          const dataMatch = rawEvent.match(/^data: (.*)$/m);
          if (!eventMatch || !dataMatch) continue;
          const eventType = eventMatch[1];
          const payload = JSON.parse(dataMatch[1]);
          if (eventType === "meta") {
            patchLastMessage({
              meta: {
                sql: payload.sql,
                source: payload.source,
                explanation: payload.explanation,
                result: payload.result,
                intent: payload.intent,
                stages: payload.stages,
              },
            });
          } else if (eventType === "token") {
            patchLastMessage((last) => ({ ...last, content: last.content + payload.text }));
          } else if (eventType === "done") {
            patchLastMessage((last) => ({
              ...last,
              content: payload.answer,
              meta: { ...last.meta, stages: payload.stages ?? last.meta?.stages },
            }));
          } else if (eventType === "error") {
            throw new Error(payload.error);
          }
        }
      }
      onAnswered?.();
    } catch (e) {
      setError(e.message);
      setMessages((m) => [...m, { role: "assistant", content: `Something went wrong: ${e.message}` }]);
    } finally {
      setLoading(false);
    }
  }

  function handleSubmit(e) {
    e.preventDefault();
    const q = input.trim();
    if (!q || loading) return;
    setInput("");
    sendQuestion(q);
  }

  async function transcribeAndSend(blob) {
    setTranscribing(true);
    setError(null);
    try {
      const formData = new FormData();
      formData.append("audio", blob, "voice-query.webm");
      const res = await fetch(`${API_BASE}/speech-to-text`, { method: "POST", body: formData });
      if (res.status === 422) {
        setTranscribing(false);
        return; // nothing intelligible — drop silently
      }
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Transcription failed (${res.status})`);
      }
      const { text } = await res.json();
      setTranscribing(false);
      if (text.trim()) await sendQuestion(text.trim());
    } catch (e) {
      setTranscribing(false);
      setError(e.message);
    }
  }

  // ── push-to-talk: hold Space to record, release to transcribe+send ──
  const ptStreamRef = useRef(null);
  const ptRecorderRef = useRef(null);
  const ptChunksRef = useRef([]);
  const spaceHeldRef = useRef(false);
  const transcribeAndSendRef = useRef(transcribeAndSend);
  transcribeAndSendRef.current = transcribeAndSend;

  useEffect(() => {
    async function startPushToTalk() {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        if (!spaceHeldRef.current) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }
        ptStreamRef.current = stream;
        const recorder = new MediaRecorder(stream);
        ptChunksRef.current = [];
        recorder.ondataavailable = (e) => {
          if (e.data.size > 0) ptChunksRef.current.push(e.data);
        };
        recorder.onstop = async () => {
          stream.getTracks().forEach((t) => t.stop());
          ptStreamRef.current = null;
          setListening(false);
          const blob = new Blob(ptChunksRef.current, { type: "audio/webm" });
          await transcribeAndSendRef.current(blob);
        };
        ptRecorderRef.current = recorder;
        recorder.start();
        setListening(true);
        if (!spaceHeldRef.current && recorder.state === "recording") recorder.stop();
      } catch {
        setError("Couldn't access the microphone — check browser permissions.");
        spaceHeldRef.current = false;
      }
    }

    function onKeyDown(e) {
      if (e.code !== "Space" || spaceHeldRef.current) return;
      const el = document.activeElement;
      const tag = el?.tagName;
      if ((tag === "INPUT" || tag === "TEXTAREA") && el.value?.trim()) return;
      if (!openRef.current) return;
      e.preventDefault();
      spaceHeldRef.current = true;
      startPushToTalk();
    }
    function onKeyUp(e) {
      if (e.code !== "Space" || !spaceHeldRef.current) return;
      e.preventDefault();
      spaceHeldRef.current = false;
      if (ptRecorderRef.current?.state === "recording") ptRecorderRef.current.stop();
    }
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      ptStreamRef.current?.getTracks().forEach((t) => t.stop());
    };
  }, []);

  return (
    <>
      {open && <div className="chat-widget-overlay" onClick={() => setOpen(false)} />}
      {open && (
        <div className="chat-widget-panel">
          <div className="chat-widget-header">
            <span>
              DeepInsight Assistant
              {(listening || transcribing) && (
                <span className="voice-mode-pill">
                  {listening ? "🎙️ listening" : "⏳ transcribing"}
                </span>
              )}
            </span>
            <button className="chat-widget-close" onClick={() => setOpen(false)} aria-label="Close chat">×</button>
          </div>

          <div className="chat-scroll" ref={scrollRef} onScroll={handleScroll}>
            {messages.map((m, i) =>
              m.role === "assistant" && m.content === "" && i === messages.length - 1 ? null : (
                <ChatMessage key={i} role={m.role} content={m.content} meta={m.meta} />
              )
            )}
            {loading && messages[messages.length - 1]?.content === "" && (
              <div className="msg msg-assistant">
                <div className="msg-bubble msg-typing">
                  <span className="dot" /><span className="dot" /><span className="dot" />
                </div>
              </div>
            )}
          </div>

          <div className="suggestions">
            {SUGGESTIONS.map((s) => (
              <button key={s} className="suggestion-chip" disabled={loading} onClick={() => sendQuestion(s)}>
                {s}
              </button>
            ))}
          </div>

          <form className="chat-input-row" onSubmit={handleSubmit}>
            <span className={`mic-btn ${listening ? "listening" : ""}`} title="Hold Space to talk" aria-hidden="true">🎤</span>
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={
                listening ? "Listening... (release Space to send)"
                : transcribing ? "Transcribing..."
                : "Ask a question... (hold Space to talk)"
              }
              disabled={loading}
            />
            <button type="submit" disabled={loading || !input.trim()}>Send</button>
          </form>
          {transcribing && <div className="transcribing-banner">Transcribing your question...</div>}
          {error && <div className="error-banner">{error}</div>}
        </div>
      )}

      {!open && (
        <div className="chat-widget-fab-label" onClick={() => setOpen(true)}>Ask Assistant</div>
      )}
      <button
        className="chat-widget-fab"
        onClick={() => setOpen((o) => !o)}
        aria-label={open ? "Close assistant" : "Open assistant"}
      >
        {open ? "×" : (
          <svg width="26" height="26" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path d="M12 3C6.48 3 2 6.94 2 11.8c0 2.62 1.3 4.97 3.36 6.57-.11.98-.5 2.32-1.36 3.63 1.62-.2 3.24-.9 4.5-1.77A11.6 11.6 0 0 0 12 20.6c5.52 0 10-3.94 10-8.8S17.52 3 12 3Z" fill="currentColor" />
          </svg>
        )}
      </button>
    </>
  );
}
