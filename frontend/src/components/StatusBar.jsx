import { DEV } from "../devmode.js";

const STATE_WORD = { ready: "ready", cooling: "cooling down", disabled: "off" };

// Top-right status. Customers: simple availability. Developers: live model + every model's state.
export default function StatusBar({ health }) {
  const { state, data } = health;

  if (!DEV) {
    if (state === "ready") return <div className="status ok"><span className="dot" aria-hidden />Online</div>;
    if (state === "checking") return <div className="status"><span className="dot" aria-hidden />Connecting</div>;
    return <div className="status bad"><span className="dot" aria-hidden />Unavailable</div>;
  }

  if (state === "checking") return <div className="status"><span className="dot" aria-hidden />Connecting to backend</div>;
  if (state === "offline") return <div className="status bad"><span className="dot" aria-hidden />Backend not running</div>;

  const { active, models } = data.llm;
  const summary = models
    .map((m) => `${m.provider_name} ${m.model_name}: ${STATE_WORD[m.state]}`
      + (m.retry_in_seconds ? ` (${m.retry_in_seconds}s)` : "")
      + (m.reason ? `, ${m.reason}` : ""))
    .join("\n");
  const standby = models.filter((m) => m.state === "ready").length - (active ? 1 : 0);

  return (
    <div className="status-group">
      <span className="dev-tag">Developer view</span>
      <div className={`status ${active ? "ok" : "warn"}`} title={summary}>
        <span className="dot" aria-hidden />
        {active ? (
          <>
            <strong>{active.provider_name}</strong>
            <span className="status-model">{active.model_name}</span>
            {standby > 0 && <span className="status-backup">+{standby} standby</span>}
          </>
        ) : (
          <span>All models cooling down or off</span>
        )}
      </div>
    </div>
  );
}
