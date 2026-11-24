"""Watch Antigravity conversations started with ag_start and print one line per event.

Run it with Claude Code's Monitor tool. Each stdout line is one event:
  ASKING conv=<id> q=<question id>: <question> options=[...]   answer with ag_answer
  POPUP  conv=<id>: <question> options=[...]                   only the user can answer it in the App
  DONE   conv=<id>: <first 300 chars of the report>            read the full report with ag_reply
  ERROR  conv=<id>: <text>                                     capacity errors (503, 429) are skipped
  MODEL_MISMATCH conv=<id>: an exact model id was requested but another model is running
  STALLED conv=<id>: no transcript change for N seconds
  ALL_DONE                                                     printed once with --until-done, then exit

Usage: python watch.py --conv <id> [--conv <id> ...] [--until-done]
"""
import argparse
import sys
import time

import bridge_server as b

POLL_SECONDS = 2
STALL_SECONDS = 180
RETRIED_ERRORS = ("No capacity", "RESOURCE_EXHAUSTED")


def emit(line: str) -> None:
    sys.stdout.write(line.replace("\n", " ") + "\n")
    sys.stdout.flush()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--conv", action="append", required=True)
    parser.add_argument("--until-done", action="store_true")
    args = parser.parse_args()

    baseline = {}
    seen = set()
    done = set()
    stall_marked = {}
    emit(f"WATCHING {len(args.conv)} conversations")
    while True:
        for conv in args.conv:
            st = b.ag_status(conv)
            baseline.setdefault(conv, st["stops"])
            if st["model_mismatch"] and ("model", conv) not in seen:
                seen.add(("model", conv))
                emit(f"MODEL_MISMATCH conv={conv}: requested {st['model_requested']} but running on {st['model_used']}")
            for q in st["open_questions"]:
                key = ("ask", q["id"])
                if key not in seen:
                    seen.add(key)
                    emit(f"ASKING conv={conv} q={q['id']}: {q['question']} options={q['options']}")
            popup = st.get("popup")
            if popup:
                text = "; ".join(f"{p['question']} options={p['options']}" for p in popup["questions"])
                key = ("popup", conv, text)
                if key not in seen:
                    seen.add(key)
                    emit(f"POPUP conv={conv}: {text}")
            for err in st["recent_errors"]:
                key = ("err", conv, err["step"])
                if key not in seen:
                    seen.add(key)
                    if not any(marker in err["text"] for marker in RETRIED_ERRORS):
                        emit(f"ERROR conv={conv}: {err['text'][:200]}")
            if st["state"] == "done" and st["stops"] > baseline[conv]:
                key = ("done", conv, st["stops"])
                if key not in seen:
                    seen.add(key)
                    done.add(conv)
                    emit(f"DONE conv={conv} model={st['model_used']}: {b.ag_reply(conv).get('text', '')[:300]}")
            if st["state"] == "error" and st["stops"] > baseline[conv]:
                key = ("terminated", conv, st["stops"])
                if key not in seen:
                    seen.add(key)
                    emit(f"ERROR conv={conv}: agent terminated without a reply (model {st['model_requested']}). "
                         "Retry with ag_send, or start again on another model")
                    done.add(conv)
            if st["state"] == "running":
                age = b.transcript_age(conv)
                bucket = int(age // STALL_SECONDS)
                if bucket and stall_marked.get(conv) != bucket:
                    stall_marked[conv] = bucket
                    emit(f"STALLED conv={conv}: no transcript change for {int(age)} seconds")
        if args.until_done and all(c in done for c in args.conv):
            emit("ALL_DONE")
            return
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
