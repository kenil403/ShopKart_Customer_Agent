// Developer view: open the app with ?dev=1 (e.g. http://127.0.0.1:5173/?dev=1).
// Customers never see model names, MCP tool names, scores or technical errors.
export const DEV = new URLSearchParams(window.location.search).has("dev");
