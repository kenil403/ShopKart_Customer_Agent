import { DEV } from "../devmode.js";
import { DEMO_ACCOUNTS } from "../faq.js";
import { groupByDate, timeLabel } from "../history.js";

export default function HistorySidebar({ conversations, activeId, pendingId, onNew, onOpen, onDelete, onClear, onPickEmail }) {
  const groups = groupByDate(conversations);

  return (
    <nav className="history" aria-label="Conversation history">
      <button className="btn-new" onClick={onNew}>
        <span aria-hidden>+</span> New conversation
      </button>

      <div className="history-scroll">
        {groups.length === 0 ? (
          <p className="history-empty">Your conversations will appear here.</p>
        ) : (
          groups.map(([label, items]) => (
            <section key={label} className="history-group">
              <h2>{label}</h2>
              <ul>
                {items.map((c) => {
                  const n = c.messages.filter((m) => m.role === "user").length;
                  return (
                  <li key={c.id} className={c.id === activeId ? "active" : ""}>
                    <button
                      className="history-item"
                      onClick={() => onOpen(c.id)}
                      title={c.title}
                      aria-current={c.id === activeId ? "true" : undefined}
                    >
                      <span className="history-title">{c.title}</span>
                      <span className="history-meta">
                        {c.id === pendingId ? "Replying…" : `${timeLabel(c.updatedAt)}, ${n} ${n === 1 ? "question" : "questions"}`}
                      </span>
                    </button>
                    <button
                      className="history-delete"
                      onClick={() => onDelete(c.id)}
                      aria-label={`Delete conversation: ${c.title}`}
                      title="Delete conversation"
                    >
                      ×
                    </button>
                  </li>
                  );
                })}
              </ul>
            </section>
          ))
        )}
      </div>

      <footer className="history-foot">
        {DEV && (
          <details className="demo-accounts">
            <summary>Demo accounts</summary>
            <ul>
              {DEMO_ACCOUNTS.map(([email, orders]) => (
                <li key={email}>
                  <button onClick={() => onPickEmail(email)}>{email}</button>
                  <small>{orders}</small>
                </li>
              ))}
            </ul>
          </details>
        )}
        {conversations.length > 0 && (
          <button className="btn-clear" onClick={onClear}>Clear history</button>
        )}
      </footer>
    </nav>
  );
}
