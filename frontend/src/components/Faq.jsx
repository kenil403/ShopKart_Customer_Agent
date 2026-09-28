import { useRef } from "react";

// Full FAQ on the main screen of a new conversation.
export function FaqGrid({ groups, onAsk, disabled }) {
  return (
    <div className="faq-grid">
      {groups.map((g) => (
        <section key={g.topic} className={`faq-card${g.topic === "Demo script" ? " demo" : ""}`}>
          <h2>{g.topic}</h2>
          <ul>
            {g.items.map((q) => (
              <li key={q}>
                <button onClick={() => onAsk(q)} disabled={disabled}>{q}</button>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

// During a conversation: every FAQ stays one click away above the message box.
export function FaqStrip({ groups, onAsk, disabled }) {
  const rowRef = useRef(null);
  // Let a normal mouse wheel scroll the row sideways.
  const onWheel = (e) => {
    const el = rowRef.current;
    if (el && Math.abs(e.deltaY) > Math.abs(e.deltaX)) el.scrollLeft += e.deltaY;
  };
  return (
    <div className="faq-strip">
      <span className="faq-strip-label">Frequently asked</span>
      <div className="faq-strip-row" ref={rowRef} onWheel={onWheel}>
        {groups.flatMap((g) => g.items).map((q) => (
          <button key={q} onClick={() => onAsk(q)} disabled={disabled}>{q}</button>
        ))}
      </div>
    </div>
  );
}
