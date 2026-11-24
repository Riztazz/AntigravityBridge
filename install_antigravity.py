"""One-time setup of the Antigravity side. Safe to run again.

Copies ask_server.py and log_hook.py to ~/.gemini/ag-bridge, adds the ask_orchestrator MCP
server to ~/.gemini/config/mcp_config.json, and adds the logging hooks to
~/.gemini/config/hooks.json. Existing entries of other names are kept.
Restart the Antigravity App afterwards.
"""
import json
import shutil
import sys
from pathlib import Path

SRC = Path(__file__).parent / "server"
STATE = Path.home() / ".gemini" / "ag-bridge"
CONFIG = Path.home() / ".gemini" / "config"
PY = sys.executable.replace("\\", "/")


def load(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8").strip()
        return json.loads(text) if text else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save(path: Path, data: dict) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(data, indent=2) + "\n")


def main() -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / "questions").mkdir(exist_ok=True)
    CONFIG.mkdir(parents=True, exist_ok=True)
    for name in ("ask_server.py", "log_hook.py"):
        shutil.copy2(SRC / name, STATE / name)

    mcp_path = CONFIG / "mcp_config.json"
    mcp = load(mcp_path)
    mcp.setdefault("mcpServers", {})["ag-bridge-ask"] = {
        "command": PY, "args": [str(STATE / "ask_server.py").replace("\\", "/")]}
    save(mcp_path, mcp)

    hook_cmd = lambda event: f"{PY} {str(STATE / 'log_hook.py').replace(chr(92), '/')} {event}"
    flat = lambda event: [{"type": "command", "command": hook_cmd(event), "timeout": 10}]
    nested = lambda event: [{"matcher": "*", "hooks": flat(event)}]
    hooks_path = CONFIG / "hooks.json"
    hooks = load(hooks_path)
    hooks["ag-bridge-log"] = {
        "SessionStart": flat("SessionStart"),
        "PreToolUse": nested("PreToolUse"),
        "PostToolUse": nested("PostToolUse"),
        "PreInvocation": flat("PreInvocation"),
        "PostInvocation": flat("PostInvocation"),
        "Stop": flat("Stop"),
    }
    save(hooks_path, hooks)
    print("Installed. Restart the Antigravity App, and set its mode to Turbo.")


if __name__ == "__main__":
    main()
