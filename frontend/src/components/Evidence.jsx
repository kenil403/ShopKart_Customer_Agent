import { useState } from "react";
import { DEV } from "../devmode.js";

// ---------- customer wording: plain language, no tool or system names
function customerStep(t) {
  if (t.tool === "search_policy") return t.auto ? "Checked the return policy for this item" : "Checked ShopKart's policies";
  if (t.blocked === "verification") {
    return t.tool === "get_customer_orders" ? "Asked you to confirm your email"
      : "Couldn't verify this order with your email";
  }
  if (t.tool === "get_customer_orders") return t.ok ? "Looked up your orders" : "Looked for orders on your email";
  if (t.tool === "get_order_status") return t.ok ? `Checked order ${t.order_id}` : "Looked up the order";
  if (t.tool === "request_return") {
    if (t.ok) return `Created a return request for ${t.order_id}`;
    if (t.outcome === "manual_review") return `Sent ${t.order_id} for review by our team`;
    return `Checked if ${t.order_id} can be returned`;
  }
  return null;
}

// ---------- developer wording
const DEV_LABEL = {
  search_policy: "search_policy",
  get_customer_orders: "get_customer_orders",
  get_order_status: "get_order_status",
  request_return: "request_return",
};
function devDetail(t) {
  if (t.blocked) return `blocked: ${t.blocked}`;
  if (t.tool === "search_policy") return `${t.auto ? "auto: " : ""}“${t.query}”${t.ok ? "" : " → NO_MATCH"}`;
  if (t.tool === "request_return") return `${t.order_id} → ${t.outcome || (t.ok ? "approved" : "not created")}`;
  if (t.tool === "get_customer_orders") return t.ok ? `${t.orders.length} orders` : "none";
  if (t.tool === "get_order_status") return t.ok ? t.order_id : "not found";
  return t.error || "";
}

export default function Evidence({ data }) {
  const [open, setOpen] = useState(false);
  if (!data) return null;
  const tools = data.tools_used || [];
  const cited = data.citations || [];

  if (!DEV) {
    const steps = [...new Set(tools.map(customerStep).filter(Boolean))];
    if (cited.length === 0 && steps.length === 0) return null; // e.g. a greeting
    return (
      <div className="label">
        <span className="label-tape">Sources</span>
        {cited.length > 0 && (
          <div className="label-row">
            <span className="label-key">Based on</span>
            <div className="label-vals">
              {cited.map((c) => (
                <span key={c.doc + c.section} className="chip doc">
                  {c.title} <b>§ {c.section}</b>
                </span>
              ))}
            </div>
          </div>
        )}
        {steps.length > 0 && (
          <div className="label-row">
            <span className="label-key">What we did</span>
            <ul className="done">{steps.map((s) => <li key={s}>{s}</li>)}</ul>
          </div>
        )}
      </div>
    );
  }

  // ------------------------------------------------ developer view
  const retrieved = data.documents_retrieved || [];
  const returnRules = tools.flatMap((t) => t.citations || []);
  return (
    <div className="label">
      <span className="label-tape">Trace</span>
      <div className="label-row">
        <span className="label-key">Cited</span>
        <div className="label-vals">
          {cited.length ? cited.map((c) => (
            <span key={c.doc + c.section} className="chip doc">{c.doc} <b>§ {c.section}</b></span>
          )) : <span className="chip plain">no citation</span>}
          {(data.invalid_citations || []).map((c, i) => (
            <span key={`bad${i}`} className="chip stop">invalid: {c.doc} § {c.section}</span>
          ))}
        </div>
      </div>
      {tools.length > 0 && (
        <div className="label-row">
          <span className="label-key">Tools</span>
          <ol className="steps">
            {tools.map((t, i) => (
              <li key={i} className={t.blocked ? "stop" : t.via === "mcp" ? "mcp" : "rag"}>
                <span className="via">{t.via === "mcp" ? "MCP" : "RAG"}</span>
                <span className="step-name">{DEV_LABEL[t.tool] || t.tool}</span>
                <span className="step-detail">{devDetail(t)}</span>
              </li>
            ))}
          </ol>
        </div>
      )}
      <div className="label-row">
        <span className="label-key">Model</span>
        <div className="label-vals">
          {(data.models_used || []).map((m) => <span key={m} className="chip plain">{m}</span>)}
        </div>
      </div>
      {(retrieved.length > 0 || returnRules.length > 0) && (
        <>
          <button className="label-toggle" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
            {open ? "Hide retrieval details" : "Show retrieval details"}
          </button>
          {open && (
            <div className="label-details">
              {retrieved.length > 0 && (
                <>
                  <p>Sections retrieved (BM25 score)</p>
                  <ul>{retrieved.map((d, i) => <li key={i}>{d.doc} § {d.section} <span>{d.score}</span></li>)}</ul>
                </>
              )}
              {returnRules.length > 0 && (
                <>
                  <p>Rules applied by the eligibility engine</p>
                  <ul>{returnRules.map((c, i) => <li key={i}>{c}</li>)}</ul>
                </>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
