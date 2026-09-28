"""Answer every line of data/questions.txt and save the results to answers.json.

    cd backend
    uv run python -m eval.run_questions              # data/questions.txt -> answers.json
    uv run python -m eval.run_questions --resume     # continue after an interruption
    uv run python -m eval.run_questions --questions data/questions_extra.txt --out answers_extra.json

* Each question runs in a fresh conversation (they are independent).
* If every model is rate-limited, it waits for the cooldown and retries the same
  question (up to EVAL_MAX_WAIT_SECONDS), so free tiers don't break the run.
* answers.json is saved after every question, so nothing is lost if you stop it.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

os.environ.setdefault("STORE_TODAY", "2026-09-26")
os.environ["RETURNS_LOG"] = str(Path(tempfile.gettempdir()) / f"shopkart_q_{uuid.uuid4().hex}.json")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app.agent.llm import describe_error  # noqa: E402
from app.agent.runner import SupportAgent  # noqa: E402
from eval.common import ask_with_patience  # noqa: E402

DEFAULT_OUT = ROOT / "answers.json"
IDK = ("don't know", "do not know", "isn't covered", "not covered")


async def main(resume: bool, questions_file: str | None, out_file: str | None) -> None:
    qfile = Path(questions_file or os.getenv("QUESTIONS_FILE", config.DATA_DIR / "questions.txt"))
    if not qfile.is_absolute():
        qfile = ROOT / qfile
    OUT = (ROOT / out_file) if out_file else DEFAULT_OUT
    questions = [q.strip() for q in qfile.read_text(encoding="utf-8").splitlines() if q.strip()]

    previous = {}
    if resume and OUT.exists():
        previous = {a["question"]: a for a in json.loads(OUT.read_text(encoding="utf-8")) if not a.get("error")}
        print(f"Resuming: {len(previous)} answer(s) kept from the last run.")

    agent = await SupportAgent().start()
    if agent.config_error:
        await agent.close()
        sys.exit(f"No model configured: {agent.config_error}\nSet the keys in backend/.env and retry.")

    answers = []
    try:
        for i, q in enumerate(questions, start=1):
            if q in previous:
                answers.append({**previous[q], "id": i})
                continue
            if answers:
                await asyncio.sleep(config.EVAL_DELAY_SECONDS)
            print(f"[{i}/{len(questions)}] {q}", flush=True)
            entry = {"id": i, "question": q}
            try:
                out = await ask_with_patience(agent, f"q-{i}-{uuid.uuid4().hex[:6]}", q)
                entry.update(answer=out["answer"], citations=out["citations"],
                             tools_used=[t.get("tool") for t in out["tools_used"]],
                             model=(out.get("models_used") or [None])[-1])
                print(f"    -> {out['answer'][:150].replace(chr(10), ' ')}\n", flush=True)
            except Exception as exc:  # noqa: BLE001
                err = describe_error(exc)
                entry.update(answer=None, error=err["dev"]["detail"][:300])
                print(f"    !! failed: {err['type']} (re-run with --resume later)\n", flush=True)
            answers.append(entry)
            OUT.write_text(json.dumps(answers, indent=2, ensure_ascii=False), encoding="utf-8")
    finally:
        await agent.close()

    OUT.write_text(json.dumps(answers, indent=2, ensure_ascii=False), encoding="utf-8")
    ok = [a for a in answers if not a.get("error")]
    cited = sum(bool(a.get("citations")) for a in ok)
    idk = sum(any(p in (a.get("answer") or "").lower() for p in IDK) for a in ok)
    orders = sum(any(t != "search_policy" for t in a.get("tools_used", [])) for a in ok)
    print(f"Saved {OUT}\n  answered: {len(ok)}/{len(answers)}   with citations: {cited}   "
          f"said 'don't know': {idk}   used order tools: {orders}")
    if len(ok) < len(answers):
        print("  Some questions failed (all models busy). Run again with --resume.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--resume", action="store_true", help="keep answers from the last run, redo failed ones")
    ap.add_argument("--questions", help="question file (default: data/questions.txt)")
    ap.add_argument("--out", help="output file (default: answers.json)")
    a = ap.parse_args()
    asyncio.run(main(a.resume, a.questions, a.out))
