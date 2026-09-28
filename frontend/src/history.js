// Conversation history, stored in this browser (localStorage).
// The backend keeps each conversation's memory under the same id, so a chat
// reopened from history continues with its full context.
const KEY = "shopkart.conversations.v1";
const MAX = 50;

export function loadConversations() {
  try {
    const list = JSON.parse(localStorage.getItem(KEY) || "[]");
    return Array.isArray(list) ? list : [];
  } catch {
    return [];
  }
}

export function saveConversations(list) {
  try {
    const clean = list
      .slice(0, MAX)
      .map((c) => ({ ...c, messages: c.messages.filter((m) => m.role !== "error") })); // errors are temporary
    localStorage.setItem(KEY, JSON.stringify(clean));
  } catch {
    /* storage full or disabled: history just won't persist */
  }
}

// Title for the sidebar: the first question, without email addresses
// ("Where are my orders? My email is a@b.com" -> "Where are my orders?").
export function titleFrom(text) {
  const t = text
    .replace(/\b(my\s+)?(registered\s+)?e-?mail(\s+address)?(\s+is|:)?\s*\S+@\S+/gi, "")
    .replace(/\S+@\S+\.\S+/g, "")
    .replace(/\s+/g, " ")
    .replace(/[\s,;.]+$/, "")
    .replace(/^[\s,;.:]+/, "")
    .trim()
    .replace(/^./, (c) => c.toUpperCase()) || "Question about my order";
  return t.length > 48 ? `${t.slice(0, 46).trimEnd()}…` : t;
}

// Group for the sidebar: Today, Yesterday, Previous 7 days, Older.
export function groupByDate(list) {
  const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const today = startOfDay(new Date());
  const day = 86400000;
  const groups = [["Today", []], ["Yesterday", []], ["Previous 7 days", []], ["Older", []]];
  for (const c of [...list].sort((a, b) => b.updatedAt - a.updatedAt)) {
    const d = startOfDay(new Date(c.updatedAt));
    const idx = d >= today ? 0 : d >= today - day ? 1 : d >= today - 7 * day ? 2 : 3;
    groups[idx][1].push(c);
  }
  return groups.filter(([, items]) => items.length);
}

export function timeLabel(ts) {
  const d = new Date(ts);
  const now = new Date();
  if (d.toDateString() === now.toDateString()) {
    return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }
  return d.toLocaleDateString([], { day: "numeric", month: "short" });
}
