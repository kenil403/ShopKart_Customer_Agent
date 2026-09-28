import { DEV } from "../devmode.js";
import Evidence from "./Evidence.jsx";

const CITE = /\[\s*(\d{2}_[\w]+\.md)\s*§\s*([^\]]+?)\s*\]/g;

// "03_returns_policy_update_2026.md" -> "returns policy update 2026" (fallback when no title is known)
const fallbackTitle = (doc) => doc.replace(/^\d{2}_/, "").replace(/\.md$/, "").replace(/_/g, " ");
const sectionNumber = (section) => section.split(".")[0].trim();

// Inline formatting: **bold** and policy citations as small chips.
function inline(text, keyBase, titles) {
  const parts = [];
  let last = 0;
  const tokens = [...text.matchAll(new RegExp(`${CITE.source}|\\*\\*([^*]+)\\*\\*`, "g"))];
  tokens.forEach((m, i) => {
    if (m.index > last) parts.push(text.slice(last, m.index));
    if (m[1]) {
      const title = titles[m[1]] || fallbackTitle(m[1]);
      parts.push(
        <span key={`${keyBase}-c${i}`} className="cite" title={DEV ? `${m[1]} § ${m[2]}` : `${title}, section ${m[2]}`}>
          {title}, §{sectionNumber(m[2])}
        </span>
      );
    } else {
      parts.push(<strong key={`${keyBase}-b${i}`}>{m[3]}</strong>);
    }
    last = m.index + m[0].length;
  });
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

// Minimal block formatting: paragraphs and bullet / numbered lists.
function Formatted({ text, titles }) {
  const blocks = [];
  let list = null;
  text.split("\n").forEach((raw) => {
    const line = raw.trimEnd();
    const bullet = line.match(/^\s*(?:[-*•]|\d+[.)])\s+(.*)$/);
    if (bullet) {
      if (!list) { list = { ordered: /^\s*\d/.test(line), items: [] }; blocks.push(list); }
      list.items.push(bullet[1]);
    } else {
      list = null;
      if (line.trim()) blocks.push(line);
    }
  });
  return blocks.map((b, i) =>
    typeof b === "string" ? (
      <p key={i}>{inline(b, i, titles)}</p>
    ) : b.ordered ? (
      <ol key={i}>{b.items.map((it, j) => <li key={j}>{inline(it, `${i}-${j}`, titles)}</li>)}</ol>
    ) : (
      <ul key={i}>{b.items.map((it, j) => <li key={j}>{inline(it, `${i}-${j}`, titles)}</li>)}</ul>
    )
  );
}

export default function Message({ msg }) {
  if (msg.role === "user") {
    return <div className="msg user"><div className="bubble">{msg.text}</div></div>;
  }
  if (msg.role === "error") {
    const { message, dev, type } = msg.info || {};
    const soft = type === "busy" || type === "offline"; // temporary, not a failure
    return (
      <div className="msg agent">
        <div className={`bubble error${soft ? " soft" : ""}`} role="alert">
          <p className="error-title">{message || "Sorry, something went wrong. Please try again."}</p>
          {DEV && dev && (
            <div className="error-dev">
              {dev.detail && <p>{dev.detail}</p>}
              {dev.attempts?.length > 0 && (
                <ul>{dev.attempts.map((a, i) => <li key={i}>{a.model}: {a.reason}</li>)}</ul>
              )}
              {dev.hint && <p>{dev.hint}</p>}
            </div>
          )}
        </div>
      </div>
    );
  }
  const titles = Object.fromEntries((msg.data?.citations || []).map((c) => [c.doc, c.title]));
  return (
    <div className="msg agent">
      <div className="bubble"><Formatted text={msg.text || "Sorry, I couldn't put an answer together. Please try again."} titles={titles} /></div>
      <Evidence data={msg.data} />
    </div>
  );
}
