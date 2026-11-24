"""Antigravity hook: log each payload to events.jsonl and answer the hook protocol.

Usage: python log_hook.py <EventName>
PreToolUse must print a decision. It allows every call, except ask_question inside a
conversation started by the orchestrator bridge: that call is denied with a pointer to
ask_orchestrator. Every other event prints {}.
"""
import json
import re
import sys
import time
from pathlib import Path

LOG = Path(__file__).parent / "events.jsonl"
BRIDGE_MARKER = "You are working for an orchestrator"


WORKSPACE_LINE = re.compile(r"^Workspace: (.+)$", re.MULTILINE)
KEEPS_ITS_FOLDER = ("set-location", "cd ", "cd\t", "push-location", "sl ")


def workspace_of(first_message: str) -> str:
    match = WORKSPACE_LINE.search(first_message)
    return match.group(1).strip() if match else ""


def decide(payload: dict) -> dict:
    """The PreToolUse answer. Every call is allowed, with two rules for bridge conversations.

    ask_question is denied. A shell command that names no folder of its own starts in the
    conversation's Workspace, because a conversation started through the agent API has no
    shell folder of its own and its commands would start in the Antigravity install folder.
    """
    call = payload.get("toolCall", {})
    tool = call.get("name", "")
    first_message = payload.get("lastUserInput", "")
    if BRIDGE_MARKER not in first_message:
        return {"decision": "allow"}
    if tool == "ask_question":
        return {
            "decision": "deny",
            "reason": "Do not use ask_question. Call the MCP tool ask_orchestrator with your task_id "
                      "and question, then end your turn.",
        }
    workspace = workspace_of(first_message)
    command = str(call.get("args", {}).get("CommandLine", ""))
    if tool == "run_command" and workspace and command.strip():
        if not command.lstrip().lower().startswith(KEEPS_ITS_FOLDER):
            folder = workspace.replace("'", "''")
            return {"decision": "allow", "overwrite": {"CommandLine": f"Set-Location -LiteralPath '{folder}'; {command}"}}
    return {"decision": "allow"}


def main() -> None:
    event = sys.argv[1] if len(sys.argv) > 1 else "unknown"
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {"unparsed": raw}
    record = {"ts": time.time(), "event": event, "payload": payload}
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    out = decide(payload) if event == "PreToolUse" else {}
    sys.stdout.write(json.dumps(out))


if __name__ == "__main__":
    main()
