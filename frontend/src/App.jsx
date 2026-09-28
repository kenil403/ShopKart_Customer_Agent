import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, deleteSession, getHealth, sendMessage } from "./api.js";
import { DEV } from "./devmode.js";
import { DEMO_FAQ, FAQ } from "./faq.js";
import { loadConversations, saveConversations, titleFrom } from "./history.js";
import { FaqGrid, FaqStrip } from "./components/Faq.jsx";
import HistorySidebar from "./components/HistorySidebar.jsx";
import Message from "./components/Message.jsx";
import StatusBar from "./components/StatusBar.jsx";

const newId = () => (crypto.randomUUID ? crypto.randomUUID() : `c-${Date.now()}-${Math.random().toString(36).slice(2)}`);
const faqGroups = DEV ? [DEMO_FAQ, ...FAQ] : FAQ;

export default function App() {
  const [conversations, setConversations] = useState(loadConversations);
  const [activeId, setActiveId] = useState(null);      // null = new, empty conversation
  const [pendingId, setPendingId] = useState(null);    // conversation waiting for a reply
  const [input, setInput] = useState("");
  const [health, setHealth] = useState({ state: "checking" });
  const [drawerOpen, setDrawerOpen] = useState(false); // history drawer on small screens
  const endRef = useRef(null);
  const threadRef = useRef(null);
  const inputRef = useRef(null);

  const active = conversations.find((c) => c.id === activeId) || null;
  const messages = active?.messages || [];
  const busy = pendingId !== null;

  useEffect(() => saveConversations(conversations), [conversations]);

  // Poll the backend: every 4 s until it's ready, then every 30 s (no AI calls involved).
  const refreshHealth = useCallback(async () => {
    try {
      const h = await getHealth();
      setHealth({ state: h.llm.ready ? "ready" : "no_model", data: h });
      return h.llm.ready;
    } catch {
      setHealth({ state: "offline" });
      return false;
    }
  }, []);

  useEffect(() => {
    let timer;
    const loop = async () => {
      const ok = await refreshHealth();
      timer = setTimeout(loop, ok ? 30000 : 4000);
    };
    loop();
    return () => clearTimeout(timer);
  }, [refreshHealth]);

  // Follow new messages in a chat; show the home screen from the top.
  useEffect(() => {
    if (messages.length === 0) threadRef.current?.scrollTo({ top: 0 });
    else endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length, pendingId, activeId]);

  // Update one conversation by id (the reply may arrive after the user switched chats).
  const updateConversation = (id, fn) =>
    setConversations((list) => list.map((c) => (c.id === id ? fn(c) : c)));

  const append = (id, message) =>
    updateConversation(id, (c) => ({ ...c, updatedAt: Date.now(), messages: [...c.messages, message] }));

  async function submit(text) {
    const message = (text ?? input).trim();
    if (!message || busy) return;
    setInput("");

    let id = activeId;
    if (!id) {
      id = newId();
      const now = Date.now();
      setConversations((list) => [{ id, title: titleFrom(message), createdAt: now, updatedAt: now, messages: [] }, ...list]);
      setActiveId(id);
    }
    append(id, { role: "user", text: message });
    setPendingId(id);
    try {
      const res = await sendMessage(message, id); // the conversation id is the backend's session id
      append(id, { role: "agent", text: res.answer, data: res });
    } catch (err) {
      const info = err instanceof ApiError ? err.info : { message: err.message };
      append(id, { role: "error", info });
      if (DEV) refreshHealth();
    } finally {
      setPendingId(null);
      inputRef.current?.focus();
    }
  }

  function startNew() {
    setActiveId(null);
    setDrawerOpen(false);
    inputRef.current?.focus();
  }

  function openConversation(id) {
    setActiveId(id);
    setDrawerOpen(false);
  }

  function removeConversation(id) {
    setConversations((list) => list.filter((c) => c.id !== id));
    if (id === activeId) setActiveId(null);
    deleteSession(id);
  }

  function clearHistory() {
    if (!window.confirm("Delete all conversations from this device?")) return;
    conversations.forEach((c) => deleteSession(c.id));
    setConversations([]);
    setActiveId(null);
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-left">
          <button
            className="btn-history"
            onClick={() => setDrawerOpen((o) => !o)}
            aria-expanded={drawerOpen}
            aria-controls="history-panel"
          >
            History
          </button>
          <div className="brand">
            <span className="brand-mark" aria-hidden>SK</span>
            <span className="brand-name">ShopKart <span>Support</span></span>
          </div>
        </div>
        <StatusBar health={health} />
      </header>

      <div className="body">
        <div id="history-panel" className={`history-wrap${drawerOpen ? " open" : ""}`}>
          <HistorySidebar
            conversations={conversations}
            activeId={activeId}
            pendingId={pendingId}
            onNew={startNew}
            onOpen={openConversation}
            onDelete={removeConversation}
            onClear={clearHistory}
            onPickEmail={(email) => setInput((v) => (v ? `${v} ${email}` : `My email is ${email}`))}
          />
        </div>
        {drawerOpen && <button className="scrim" aria-label="Close history" onClick={() => setDrawerOpen(false)} />}

        <main className="chat">
          <div className="thread" ref={threadRef} aria-live="polite">
            <Notice health={health} />

            {messages.length === 0 ? (
              <div className="home">
                <div className="hero">
                  <h1>Hi, how can we help?</h1>
                  <p>
                    Pick a question below or type your own. To check an order, we'll ask for the email
                    address you registered with.
                  </p>
                </div>
                <FaqGrid groups={faqGroups} onAsk={submit} disabled={busy} />
              </div>
            ) : (
              <>
                <div className="thread-head">
                  <h1>{active.title}</h1>
                  <p>
                    Started {new Date(active.createdAt).toLocaleString([], {
                      day: "numeric", month: "short", hour: "numeric", minute: "2-digit",
                    })}
                  </p>
                </div>
                {messages.map((m, i) => <Message key={i} msg={m} />)}
                {pendingId === activeId && (
                  <div className="msg agent">
                    <div className="bubble typing" aria-label="Writing a reply"><span /><span /><span /></div>
                  </div>
                )}
              </>
            )}
            <div ref={endRef} />
          </div>

          <div className="dock">
            {messages.length > 0 && <FaqStrip groups={faqGroups} onAsk={submit} disabled={busy} />}
            <form className="composer" onSubmit={(e) => { e.preventDefault(); submit(); }}>
              <label htmlFor="msg" className="sr-only">Message</label>
              <textarea
                id="msg"
                ref={inputRef}
                rows={1}
                value={input}
                placeholder="Type your question"
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); }
                }}
              />
              <button type="submit" disabled={busy || !input.trim()}>Send</button>
            </form>
          </div>
        </main>
      </div>
    </div>
  );
}

// Banner above the chat only when the assistant can't answer at all.
function Notice({ health }) {
  if (health.state !== "offline" && health.state !== "no_model") return null;
  if (!DEV) {
    return (
      <div className="notice">
        <strong>Support is temporarily unavailable.</strong>
        <p>We're reconnecting automatically. Please try again in a moment.</p>
      </div>
    );
  }
  if (health.state === "offline") {
    return (
      <div className="notice">
        <strong>Backend not running.</strong>
        <p>In the <code>backend</code> folder run <code>uv run python run.py</code>. This page reconnects by itself.</p>
      </div>
    );
  }
  return (
    <div className="notice">
      <strong>No AI model is available.</strong>
      <p>
        Set API keys in <code>backend/.env</code> and restart, or run <code>uv run python -m scripts.check_llm</code>.
        {health.data.llm.error && <span className="notice-detail"> Details: {health.data.llm.error}</span>}
      </p>
    </div>
  );
}
