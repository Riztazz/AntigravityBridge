"""Minimal stdio MCP server exposing ask_orchestrator.

Antigravity spawns this process. A question is written to questions/<id>.json and the
tool returns at once, so no MCP timeout applies. Standard library only.
"""
import json
import os
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).parent
QUESTIONS = ROOT / "questions"
QUESTIONS.mkdir(exist_ok=True)

TOOL = {
    "name": "ask_orchestrator",
    "description": (
        "Ask the orchestrator a question when you need a decision or missing information. "
        "Use this instead of ask_question. The call returns at once: end your turn after "
        "calling it. The answer arrives later as a new message."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "The task id given in your brief."},
            "question": {"type": "string", "description": "The question, self-contained."},
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional answer choices.",
            },
        },
        "required": ["question"],
    },
}


def send(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def handle_call(args: dict) -> str:
    qid = time.strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]
    record = {
        "id": qid,
        "ts": time.time(),
        "question": args.get("question", ""),
        "options": args.get("options", []),
        "task_id": args.get("task_id", ""),
        "env": {k: v for k, v in os.environ.items() if k.startswith("ANTIGRAVITY")},
        "cwd": os.getcwd(),
        "answered": False,
    }
    (QUESTIONS / f"{qid}.json").write_text(json.dumps(record, indent=1), encoding="utf-8")
    return (
        f"Question {qid} sent to the orchestrator. Reply with one short sentence saying "
        "you are waiting for the answer, then end your turn. The answer arrives as a new message."
    )


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = req.get("method")
        rid = req.get("id")
        if method == "initialize":
            send({
                "jsonrpc": "2.0", "id": rid,
                "result": {
                    "protocolVersion": req.get("params", {}).get("protocolVersion", "2024-11-05"),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "ag-bridge-ask", "version": "0.1"},
                },
            })
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": rid, "result": {"tools": [TOOL]}})
        elif method == "tools/call":
            params = req.get("params", {})
            if params.get("name") == "ask_orchestrator":
                text = handle_call(params.get("arguments", {}))
                send({"jsonrpc": "2.0", "id": rid,
                      "result": {"content": [{"type": "text", "text": text}]}})
            else:
                send({"jsonrpc": "2.0", "id": rid,
                      "error": {"code": -32601, "message": "unknown tool"}})
        elif method == "ping":
            send({"jsonrpc": "2.0", "id": rid, "result": {}})
        elif rid is not None:
            send({"jsonrpc": "2.0", "id": rid,
                  "error": {"code": -32601, "message": f"unsupported: {method}"}})


if __name__ == "__main__":
    main()
