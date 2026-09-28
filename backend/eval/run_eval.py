"""Run every case in eval/test_cases.json against the real agent and report
accuracy with a breakdown of failure types.

    cd backend
    python -m eval.run_eval                 # all cases
    python -m eval.run_eval --only M01 P07  # a subset
    python -m eval.run_eval --resume        # continue after an interruption

Outputs: eval/results/results.json and eval/results/report.md
STORE_TODAY is pinned to 2026-09-26 so date-based return rules are reproducible.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
import time
import uuid
from collections import Counter, defaultdict
from pathlib import Path

os.environ.setdefault("STORE_TODAY", "2026-09-26")
# fresh returns log per run so earlier runs don't affect results
os.environ["RETURNS_LOG"] = str(Path(tempfile.gettempdir()) / f"shopkart_eval_{uuid.uuid4().hex}.json")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config  # noqa: E402
from app.agent.llm import describe_error  # noqa: E402
from app.agent.runner import SupportAgent  # noqa: E402
from eval.common import ask_with_patience  # noqa: E402

HERE = Path(__file__).parent
IDK_PHRASES = ["don't know", "do not know", "not covered", "isn't covered", "is not covered",
               "not mentioned", "no information", "not in our polic", "not in the polic",
               "couldn't find", "could not find", "unable to find", "doesn't cover", "does not cover",
               "not specified", "don't have information", "do not have information", "not available in"]
ORDER_TOOLS = {"get_order_status", "get_customer_orders", "request_return"}


def norm(text: str) -> str:
    return (text or "").lower().replace("–", "-").replace("—", "-").replace("’", "'")


def contains(text: str, phrase: str) -> bool:
    p = norm(phrase)
    pattern = re.escape(p)
    if re.match(r"\w", p):
        pattern = r"\b" + pattern
    if re.search(r"\w$", p):
        pattern += r"\b"
    return re.search(pattern, text) is not None


def grade_turn(turn: dict, out: dict, category: str) -> list[tuple[str, str]]:
    """Returns a list of (failure_type, detail). Empty list = pass."""
    exp, fails = turn["expect"], []
    answer = norm(out["answer"])
    called = [t.get("tool") for t in out["tools_used"]]
    succeeded = [t.get("tool") for t in out["tools_used"] if t.get("ok")]

    for tool in exp.get("tools", []):
        if tool not in called:
            fails.append(("tool_selection", f"expected tool '{tool}' not called (called: {called})"))
    for tool in exp.get("forbid_tools", []):
        if tool in succeeded:
            fails.append(("verification_bypass" if tool in ORDER_TOOLS else "tool_selection",
                          f"forbidden tool '{tool}' ran successfully"))

    if exp.get("asks_for_email") and "email" not in answer:
        fails.append(("missed_verification", "did not ask the customer for their email"))

    if exp.get("expect_idk") and not any(p in answer for p in IDK_PHRASES):
        fails.append(("hallucination", "should have said it doesn't know"))

    for key in ("include_any", "include_any_2"):
        if key in exp and not any(contains(answer, p) for p in exp[key]):
            fails.append(("wrong_answer", f"none of {exp[key]} found"))
    for p in exp.get("include_all", []):
        if not contains(answer, p):
            fails.append(("wrong_answer", f"missing '{p}'"))
    for p in exp.get("exclude", []):
        if contains(answer, p):
            ftype = "privacy_leak" if category == "verification" or "multi" in category else "wrong_answer"
            fails.append((ftype, f"answer contains forbidden '{p}'"))

    cited_docs = {c["doc"] for c in out["citations"]}
    for doc in exp.get("cite", []):
        if doc not in cited_docs:
            fails.append(("missing_citation", f"no citation to {doc} (cited: {sorted(cited_docs)})"))
    if out.get("invalid_citations"):
        fails.append(("invalid_citation", f"cited non-existent sections {out['invalid_citations']}"))
    return fails


PARTIAL = HERE / "results" / "partial.json"


async def run(only: list[str] | None, resume: bool) -> None:
    cases = json.loads((HERE / "test_cases.json").read_text(encoding="utf-8"))
    if only:
        cases = [c for c in cases if c["id"] in only]
    done: dict[str, dict] = {}
    if resume and PARTIAL.exists():
        done = {r["id"]: r for r in json.loads(PARTIAL.read_text(encoding="utf-8"))
                if not any(f["type"] == "runtime_error" for f in r["failures"])}
        print(f"Resuming: {len(done)} case(s) already done.")
    agent = await SupportAgent().start()
    if agent.config_error:
        await agent.close()
        sys.exit(f"Model not configured: {agent.config_error}\nSet the key in backend/.env and retry.")
    order = " -> ".join(m["model"] for m in agent.llm_status()["models"] if m["state"] != "disabled")
    print(f"Models (fallback order): {order}\nPause between cases: {config.EVAL_DELAY_SECONDS}s\n")
    results, t0 = [], time.time()
    try:
        for n, case in enumerate(cases):
            if case["id"] in done:
                results.append(done[case["id"]])
                continue
            if n:
                await asyncio.sleep(config.EVAL_DELAY_SECONDS)  # free-tier rate limits
            thread = f"eval-{case['id']}-{uuid.uuid4().hex[:6]}"
            turns_out, case_fails = [], []
            for i, turn in enumerate(case["turns"], start=1):
                try:
                    out = await ask_with_patience(agent, thread, turn["user"])
                    fails = grade_turn(turn, out, case["category"])
                except Exception as exc:  # noqa: BLE001
                    err = describe_error(exc)
                    out = {"answer": "", "tools_used": [], "citations": []}
                    fails = [("runtime_error", f"{err['type']}: {err['dev']['detail'][:160]}")]
                case_fails += [{"turn": i, "type": t, "detail": d} for t, d in fails]
                turns_out.append({"user": turn["user"], "answer": out["answer"],
                                  "tools": [t.get("tool") for t in out["tools_used"]],
                                  "citations": out["citations"], "models": out.get("models_used", [])})
            passed = not case_fails
            results.append({"id": case["id"], "category": case["category"], "passed": passed,
                            "failures": case_fails, "turns": turns_out,
                            "models": sorted({m for t in turns_out for m in t.get("models", [])})})
            PARTIAL.parent.mkdir(exist_ok=True)
            PARTIAL.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"{'PASS' if passed else 'FAIL'}  {case['id']:5} {case['category']:15} "
                  + ("" if passed else "; ".join(f"[{f['type']}] {f['detail']}" for f in case_fails)))
    finally:
        await agent.close()
    report(results, time.time() - t0)


def report(results: list[dict], seconds: float) -> None:
    total, passed = len(results), sum(r["passed"] for r in results)
    by_cat: dict[str, list[bool]] = defaultdict(list)
    for r in results:
        by_cat[r["category"]].append(r["passed"])
    fail_types = Counter(f["type"] for r in results for f in r["failures"])

    model_counts = Counter(m for r in results for m in r.get("models", []))
    lines = [f"# Evaluation report\n",
             f"Models that answered: {', '.join(f'`{m}` ({n} cases)' for m, n in model_counts.most_common()) or 'n/a'}. "
             f"Store date: {config.today()}.\n",
             f"**Accuracy: {passed}/{total} = {passed / total:.1%}**  (runtime {seconds:.0f}s)\n",
             "| Category | Passed | Total | Accuracy |", "|---|---|---|---|"]
    for cat, vals in sorted(by_cat.items()):
        lines.append(f"| {cat} | {sum(vals)} | {len(vals)} | {sum(vals) / len(vals):.0%} |")
    lines += ["", "## Failure types", "| Type | Count |", "|---|---|"]
    lines += [f"| {t} | {n} |" for t, n in fail_types.most_common()] or ["| none | 0 |"]
    failed = [r for r in results if not r["passed"]]
    if failed:
        lines += ["", "## Failed cases"]
        for r in failed:
            for f in r["failures"]:
                lines.append(f"- **{r['id']}** (turn {f['turn']}) `{f['type']}`: {f['detail']}")
    text = "\n".join(lines)
    out_dir = HERE / "results"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "report.md").write_text(text, encoding="utf-8")
    (out_dir / "results.json").write_text(json.dumps(
        {"accuracy": passed / total, "passed": passed, "total": total,
         "by_category": {k: {"passed": sum(v), "total": len(v)} for k, v in by_cat.items()},
         "failure_types": dict(fail_types), "cases": results}, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\n" + text)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", help="case ids to run")
    ap.add_argument("--resume", action="store_true", help="skip cases finished in the last run")
    a = ap.parse_args()
    asyncio.run(run(a.only, a.resume))
