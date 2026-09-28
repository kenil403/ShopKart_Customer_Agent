// All calls go through the Vite proxy (/api -> http://127.0.0.1:8000).

const OFFLINE = {
  type: "offline",
  message: "We can't reach ShopKart support right now. Please try again in a moment.",
  dev: { detail: "Backend not reachable on 127.0.0.1:8000", hint: "In the backend folder run: uv run python run.py" },
};

export class ApiError extends Error {
  constructor(info) {
    super(info.message);
    this.info = info;
  }
}

export async function sendMessage(message, sessionId) {
  let res;
  try {
    res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, session_id: sessionId }),
    });
  } catch {
    throw new ApiError(OFFLINE);
  }
  const body = await res.json().catch(() => null);
  if (!body) throw new ApiError(OFFLINE); // Vite returns an empty 500 when the backend is down
  if (!res.ok || body.error) {
    throw new ApiError(body.error || {
      type: "error",
      message: "Sorry, something went wrong on our side. Please try again.",
      dev: { detail: `HTTP ${res.status}` },
    });
  }
  return body;
}

export async function getHealth() {
  const res = await fetch("/api/health");
  const body = await res.json().catch(() => null);
  if (!res.ok || !body) throw new Error("offline");
  return body;
}

// Tell the backend to forget a conversation's memory (best effort).
export async function deleteSession(sessionId) {
  try {
    await fetch(`/api/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
  } catch {
    /* offline: the local history entry is removed anyway */
  }
}
