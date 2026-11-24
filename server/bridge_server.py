"""Claude Code MCP server that drives the Antigravity App through its local language server.

Tools: ag_start, ag_status, ag_tail, ag_reply, ag_questions, ag_answer, ag_send.
State lives in ~/.gemini/ag-bridge: events.jsonl (written by log_hook.py),
questions/ (written by ask_server.py), tasks.json (task id -> conversation id).
Standard library only. Run with --selftest for a quick check.
"""
import ctypes
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import uuid
from pathlib import Path

ROOT = Path.home() / ".gemini" / "ag-bridge"
ROOT.mkdir(parents=True, exist_ok=True)
EVENTS = ROOT / "events.jsonl"
QUESTIONS = ROOT / "questions"
QUESTIONS.mkdir(exist_ok=True)
TASKS = ROOT / "tasks.json"
GEMINI = Path.home() / ".gemini" / "antigravity"
LS_EXE = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "antigravity" / "resources" / "bin" / "language_server.exe"
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

BRIEF = (
    "You are working for an orchestrator, not a person at the keyboard.\n"
    "Rules:\n"
    "1. Never use the ask_question tool.\n"
    "2. If you need a decision or missing information, call the MCP tool ask_orchestrator "
    "(server ag-bridge-ask) with task_id=\"{task}\", your question, and options if any. "
    "Then reply with one short sentence saying you are waiting, and end your turn.\n"
    "3. The answer arrives later as a new message. Continue the work from there.\n"
    "4. If a file, folder, tool or fact you need is missing or unclear, do not search for it and do not read "
    "files outside the Workspace and the paths named in the task. Call ask_orchestrator and end your turn.\n"
    "5. Your working folder is the Workspace line below. Relative paths in the task start there. Edit files "
    "only inside it unless the task names another path.\n"
    "6. When the work is done, end with a short report: what changed, which files, what you did not do.\n\n"
    "Task id: {task}\nWorkspace: {workspace}\n\nTask:\n{prompt}{skills}"
)

SKILLS_BRIEF = (
    "\n\nSkills: before you start, read each file below completely with your file reader and follow it as "
    "instructions for this task. A relative path inside a skill refers to the skill's own folder. These skills "
    "were written for another agent: ignore instructions that name tools you do not have and apply the method.\n"
)


def pid_alive(pid: int) -> bool:
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    code = ctypes.c_ulong()
    ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(handle)
    return code.value == 259


def daemon_env() -> dict:
    files = sorted((GEMINI / "daemon").glob("hub_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        if pid_alive(d["pid"]):
            env = dict(os.environ)
            env["ANTIGRAVITY_LS_ADDRESS"] = f"127.0.0.1:{d['httpPort']}"
            env["ANTIGRAVITY_CSRF_TOKEN"] = d["csrfToken"]
            return env
    raise RuntimeError("Antigravity App is not running: no live daemon file in ~/.gemini/antigravity/daemon")


def agentapi(*args: str, env_extra: dict = None) -> dict:
    env = daemon_env()
    env.update(env_extra or {})
    out = subprocess.run([str(LS_EXE), "agentapi", *args], env=env,
                         capture_output=True, text=True, timeout=60)
    text = out.stdout.strip() or out.stderr.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def transcript(conv: str) -> list:
    path = GEMINI / "brain" / conv / ".system_generated" / "logs" / "transcript_full.jsonl"
    steps = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                steps.append(json.loads(line))
    except (OSError, json.JSONDecodeError):
        pass
    return steps


def events(conv: str) -> list:
    out = []
    try:
        for line in EVENTS.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                if r["payload"].get("conversationId") == conv:
                    out.append(r)
    except (OSError, json.JSONDecodeError):
        pass
    return out


def open_questions(conv: str = "") -> list:
    tasks = load_json(TASKS, {})
    result = []
    for f in sorted(QUESTIONS.glob("*.json")):
        q = load_json(f, {})
        if not q or q.get("answered"):
            continue
        qconv = tasks.get(q.get("task_id", ""), {}).get("conversation_id", "")
        q["conversation_id"] = qconv
        if not conv or qconv == conv:
            result.append(q)
    return result


def summarize(step: dict) -> dict:
    calls = [{"name": c.get("name"), "args": json.dumps(c.get("args", {}))[:300]} for c in step.get("tool_calls", [])]
    text = (step.get("content") or step.get("error") or "")[:600]
    return {"step": step["step_index"], "type": step["type"], "status": step.get("status"),
            "text": text, "tool_calls": calls}


# --- tools ---------------------------------------------------------------

DEFAULT_MODELS = {
    "default": "flash",
    "allowed": {
        "flash": "alias for the flash tier, currently gemini-3.8-flash-high (verified)",
        "flash_lite": "alias for the lite tier (not tested)",
        "pro": "alias for the pro tier (not tested)",
        "gemini-3.8-flash-high": "exact id (verified)",
        "gemini-4-neon-medium": "exact id found in the App binary (not tested, no access yet)",
        "gemini-4-carbon-high": "exact id found in the App binary (not tested, no access yet)",
        "gemini-4-argon": "exact id found in the App binary (not tested, no access yet)",
    },
}
MODELS_FILE = ROOT / "models.json"


def load_models() -> dict:
    """The model allowlist. Edit ~/.gemini/ag-bridge/models.json to add or remove entries."""
    if not MODELS_FILE.exists():
        MODELS_FILE.write_text(json.dumps(DEFAULT_MODELS, indent=2) + "\n", encoding="utf-8")
    return load_json(MODELS_FILE, DEFAULT_MODELS)


def model_used(steps: list) -> str:
    for s in reversed(steps):
        if s.get("type") == "PLANNER_RESPONSE" and s.get("model"):
            return s["model"]
    return ""


def ag_models() -> dict:
    return load_models()


def skill_roots() -> list:
    """Folders that hold skills as <root>/<name>/SKILL.md, most specific first."""
    home = Path.home() / ".claude"
    roots = [Path.cwd() / ".claude" / "skills", home / "skills"]
    for found in sorted((home / "plugins" / "cache").glob("*/*/*/skills"), key=lambda p: p.stat().st_mtime, reverse=True):
        roots.append(found)
    return [r for r in roots if r.is_dir()]


def find_skills() -> dict:
    """Map of skill name to its SKILL.md path. A plugin skill is also listed as plugin:name."""
    found = {}
    for root in skill_roots():
        plugin = root.parent.parent.name if "cache" in root.parts else ""
        for md in root.glob("*/SKILL.md"):
            name = md.parent.name
            found.setdefault(name, str(md))
            if plugin:
                found.setdefault(f"{plugin}:{name}", str(md))
    return found


def resolve_skill(spec: str) -> str:
    """A skill given as a path to SKILL.md, a path to its folder, a name, or plugin:name."""
    path = Path(spec)
    if path.is_dir() and (path / "SKILL.md").exists():
        return str(path / "SKILL.md")
    if path.is_file():
        return str(path)
    return find_skills().get(spec, "")


def ag_skills() -> dict:
    """Skills that ag_start can hand to Gemini by name."""
    return {"skills": find_skills()}


PROJECTS_DIR = Path.home() / ".gemini" / "config" / "projects"


def folder_key(path_or_uri: str) -> str:
    """A folder path or file URI in one comparable form: forward slashes, lower case, no end slash."""
    text = path_or_uri
    if text.startswith("file:///"):
        text = urllib.parse.unquote(text[len("file:///"):])
    return text.replace("\\", "/").rstrip("/").lower()


def folder_uri(path: str) -> str:
    """The file URI the App writes for a folder: lower case drive letter, the colon encoded."""
    drive_fixed = path[0].lower() + path[1:] if len(path) > 1 and path[1] == ":" else path
    return "file:///" + urllib.parse.quote(drive_fixed, safe="/")


def project_for_folder(workspace: str) -> str:
    """The id of an Antigravity project that holds this folder, or an empty string."""
    wanted = folder_key(workspace)
    for file in PROJECTS_DIR.glob("*.json"):
        project = load_json(file, {})
        for resource in project.get("projectResources", {}).get("resources", []):
            for entry in resource.values():
                if isinstance(entry, dict) and folder_key(entry.get("folderUri", "")) == wanted:
                    return project.get("id", "")
    return ""


def git_default_branch(workspace: str) -> str:
    """The remote's default branch, else a local `main` or `master`, else the current branch."""
    out = subprocess.run(["git", "-C", workspace, "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
                         capture_output=True, text=True)
    if out.returncode == 0 and out.stdout.strip():
        return out.stdout.strip().removeprefix("origin/")
    for name in ("main", "master"):
        found = subprocess.run(["git", "-C", workspace, "show-ref", "--verify", "--quiet", f"refs/heads/{name}"])
        if found.returncode == 0:
            return name
    out = subprocess.run(["git", "-C", workspace, "branch", "--show-current"], capture_output=True, text=True)
    return out.stdout.strip() or "main"


def ensure_project(workspace: str) -> tuple:
    """The project id for a workspace folder, and how it was found: existing, created, or none (reason).

    A conversation started under a project gets the project's folders as its workspace. A project
    is created only for a git checkout, the one folder kind whose file layout is known.
    """
    existing = project_for_folder(workspace)
    if existing:
        return existing, "existing"
    if not (Path(workspace) / ".git").exists():
        return "", "none: the folder is not a git checkout, so no project file is written"
    project_id = str(uuid.uuid4())
    project = {
        "id": project_id,
        "name": Path(workspace).name,
        "projectResources": {"resources": [{"gitFolder": {
            "folderUri": folder_uri(workspace), "defaultBranch": git_default_branch(workspace)}}]},
        "permissionGrants": {"v2Migrated": True},
        "settings": {},
        "isWorkspaceOnly": False,
    }
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    with (PROJECTS_DIR / f"{project_id}.json").open("w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(project, indent=2) + "\n")
    return project_id, "created"


def normalize_workspace(workspace: str) -> str:
    """An existing folder as an absolute path with forward slashes, or an empty string."""
    folder = Path(workspace or os.getcwd()).expanduser()
    return str(folder.resolve()).replace("\\", "/") if folder.is_dir() else ""


def ag_start(prompt: str, model: str = "", title: str = "", skills: list = None, workspace: str = "") -> dict:
    workspace_path = normalize_workspace(workspace)
    if not workspace_path:
        return {"error": f"workspace '{workspace or os.getcwd()}' is not an existing folder",
                "fix": "pass workspace as the absolute path of the project folder you are working in"}
    models = load_models()
    model = model or models["default"]
    if model not in models["allowed"]:
        return {"error": f"model '{model}' is not allowed", "allowed": models["allowed"],
                "fix": "pick one of the allowed models, or add it to ~/.gemini/ag-bridge/models.json"}
    resolved = {}
    for spec in skills or []:
        path = resolve_skill(spec)
        if not path:
            return {"error": f"skill '{spec}' not found", "available": sorted(find_skills())[:60],
                    "fix": "give a skill name from ag_skills, or an absolute path to a SKILL.md or its folder"}
        resolved[spec] = path
    skills_text = SKILLS_BRIEF + "".join(f"- {spec}: {path}\n" for spec, path in resolved.items()) if resolved else ""
    task = uuid.uuid4().hex[:8]
    before = {p.name for p in (GEMINI / "brain").iterdir()} if (GEMINI / "brain").exists() else set()
    project_id, project_status = ensure_project(workspace_path)
    resp = agentapi("new-conversation", f"--model={model}", f"--title={title or 'orchestrated ' + task}",
                    BRIEF.format(task=task, prompt=prompt, skills=skills_text, workspace=workspace_path),
                    env_extra={"ANTIGRAVITY_PROJECT_ID": project_id} if project_id else None)
    conv = resp.get("response", {}).get("newConversation", {}).get("conversationId", "")
    if not UUID_RE.fullmatch(conv):
        conv = ""
    if not conv:
        time.sleep(3)
        now = [p for p in (GEMINI / "brain").iterdir() if p.name not in before]
        conv = now[0].name if len(now) == 1 else ""
    tasks = load_json(TASKS, {})
    tasks[task] = {"conversation_id": conv, "started": time.time(), "requested_model": model, "skills": resolved,
                  "workspace": workspace_path, "project_id": project_id}
    TASKS.write_text(json.dumps(tasks, indent=1), encoding="utf-8")
    return {"task_id": task, "conversation_id": conv, "requested_model": model, "skills": resolved,
            "workspace": workspace_path, "project": {"id": project_id, "status": project_status},
            "note": "check model_used in ag_status: an unavailable model may fall back to the flash tier",
            "agentapi_response": resp}


def popup_question(steps: list) -> dict:
    """The built-in ask_question call at the end of the transcript, or {} when there is none."""
    if not steps:
        return {}
    for call in steps[-1].get("tool_calls", []):
        if call.get("name") == "ask_question":
            questions = call.get("args", {}).get("questions", [])
            return {"questions": [{"question": q.get("question", ""), "options": q.get("options", [])}
                                  for q in questions]}
    return {}


def transcript_age(conv: str) -> float:
    path = GEMINI / "brain" / conv / ".system_generated" / "logs" / "transcript_full.jsonl"
    try:
        return time.time() - path.stat().st_mtime
    except OSError:
        return float("inf")


def ag_status(conversation_id: str) -> dict:
    """State of any conversation, started by ag_start or by hand in the App."""
    ev = events(conversation_id)
    steps = transcript(conversation_id)
    asks = open_questions(conversation_id)
    last = ev[-1]["event"] if ev else None
    popup = popup_question(steps) if last != "Stop" else {}
    stops = len([e for e in ev if e["event"] == "Stop"])
    if asks:
        state = "asking"
    elif popup:
        state = "asking_popup_unanswerable"
    elif last == "Stop":
        replied = bool(steps) and steps[-1]["type"] == "PLANNER_RESPONSE"
        state = "done" if replied else "error"
    elif last is None and not steps:
        state = "unknown"
    elif last is None:
        state = "running" if transcript_age(conversation_id) < 60 else "idle_no_events"
    else:
        state = "running"
    errors = [summarize(s) for s in steps if s["type"] == "ERROR_MESSAGE"][-2:]
    task = next((v for v in load_json(TASKS, {}).values() if v.get("conversation_id") == conversation_id), {})
    requested = task.get("requested_model", "")
    used = model_used(steps)
    result = {"state": state, "last_event": last, "stops": stops, "steps": len(steps),
              "model_requested": requested, "model_used": used,
              "model_mismatch": requested.startswith("gemini-") and bool(used) and used != requested,
              "open_questions": [{"id": q["id"], "question": q["question"], "options": q["options"]} for q in asks],
              "recent_errors": errors}
    if popup:
        result["popup"] = popup
    return result


def ag_list(limit: int = 12) -> dict:
    """The conversations started with ag_start, newest first, with their state."""
    tasks = load_json(TASKS, {})
    newest = sorted(tasks.items(), key=lambda kv: kv[1].get("started", 0), reverse=True)[:limit]
    rows = []
    for task, info in newest:
        conv = info.get("conversation_id", "")
        st = ag_status(conv) if conv else {"state": "unknown", "steps": 0}
        rows.append({"task_id": task, "conversation_id": conv, "state": st["state"], "steps": st["steps"],
                     "age_seconds": int(time.time() - info.get("started", time.time()))})
    return {"conversations": rows}


def ag_tail(conversation_id: str, since_step: int = 0) -> dict:
    steps = [summarize(s) for s in transcript(conversation_id) if s["step_index"] >= since_step]
    return {"steps": steps, "next_since_step": (steps[-1]["step"] + 1) if steps else since_step}


def ag_reply(conversation_id: str) -> dict:
    for s in reversed(transcript(conversation_id)):
        if s["type"] == "PLANNER_RESPONSE" and s.get("content"):
            return {"step": s["step_index"], "text": s["content"]}
    return {"text": ""}


def ag_questions() -> dict:
    return {"questions": [{"id": q["id"], "conversation_id": q["conversation_id"], "task_id": q.get("task_id", ""),
                           "question": q["question"], "options": q["options"]} for q in open_questions()]}


def ag_send(conversation_id: str, text: str) -> dict:
    return agentapi("send-message", conversation_id, text)


def ag_answer(question_id: str, text: str) -> dict:
    for q in open_questions():
        if q["id"] == question_id:
            if not q["conversation_id"]:
                return {"error": "question has no conversation: task_id missing or unknown"}
            resp = ag_send(q["conversation_id"], f"Answer from the orchestrator to your question {question_id}: {text}")
            path = QUESTIONS / f"{question_id}.json"
            data = load_json(path, {})
            data["answered"] = True
            data["answer"] = text
            path.write_text(json.dumps(data, indent=1), encoding="utf-8")
            return {"sent": True, "agentapi_response": resp}
    return {"error": f"no open question {question_id}"}


TOOLS = {
    "ag_start": (ag_start, "Start a new Antigravity (Gemini) conversation in the App with a task. Returns conversation_id. "
                 "Gemini asks questions through ag_questions, never a popup.",
                 {"prompt": "string", "model": "string: a key from ag_models (default flash)", "title": "string",
                  "skills": "array of strings: skill names from ag_skills, or absolute paths to a SKILL.md or its folder",
                  "workspace": "string: absolute path of the project folder you are working in. Gemini works there."},
                 ["prompt"]),
    "ag_skills": (ag_skills, "Skills (project, user and plugin) that ag_start can pass to Gemini by name.", {}, []),
    "ag_status": (ag_status, "State of a conversation: running, done, asking (open question), asking_popup_unanswerable, unknown.",
                  {"conversation_id": "string"}, ["conversation_id"]),
    "ag_models": (ag_models, "The allowed models for ag_start, with notes, and the default.", {}, []),
    "ag_list": (ag_list, "The conversations started with ag_start, newest first, with their state.",
                {"limit": "integer"}, []),
    "ag_tail": (ag_tail, "Transcript steps from since_step onward. Use next_since_step for the next call.",
                {"conversation_id": "string", "since_step": "integer"}, ["conversation_id"]),
    "ag_reply": (ag_reply, "The last text message the agent wrote: its report or its plain-text question.",
                 {"conversation_id": "string"}, ["conversation_id"]),
    "ag_questions": (ag_questions, "Open questions Gemini posted through ask_orchestrator, across all conversations.", {}, []),
    "ag_answer": (ag_answer, "Answer an open question by id. Sends the answer into the Gemini conversation.",
                  {"question_id": "string", "text": "string"}, ["question_id", "text"]),
    "ag_send": (ag_send, "Send any message into a conversation that is idle, for follow-up work.",
                {"conversation_id": "string", "text": "string"}, ["conversation_id", "text"]),
}


def schema(props: dict, required: list) -> dict:
    out = {}
    for name, desc in props.items():
        if desc.startswith("array"):
            out[name] = {"type": "array", "items": {"type": "string"}, "description": desc}
        else:
            out[name] = {"type": "integer" if desc.startswith("integer") else "string", "description": desc}
    return {"type": "object", "properties": out, "required": required}


def send(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def serve() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        method, rid = req.get("method"), req.get("id")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": req.get("params", {}).get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}}, "serverInfo": {"name": "ag-bridge", "version": "0.1"}}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": rid, "result": {"tools": [
                {"name": n, "description": d, "inputSchema": schema(p, r)} for n, (_, d, p, r) in TOOLS.items()]}})
        elif method == "tools/call":
            params = req.get("params", {})
            entry = TOOLS.get(params.get("name"))
            if not entry:
                send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "unknown tool"}})
                continue
            try:
                text = json.dumps(entry[0](**params.get("arguments", {})), indent=1)
                send({"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": text}]}})
            except Exception as exc:  # report the failure to the caller instead of dying
                send({"jsonrpc": "2.0", "id": rid, "result": {"isError": True,
                      "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}]}})
        elif method == "ping":
            send({"jsonrpc": "2.0", "id": rid, "result": {}})
        elif rid is not None:
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"unsupported: {method}"}})


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        print(json.dumps(ag_questions(), indent=1))
        print(json.dumps(ag_status("e6494761-3480-42d0-9f8e-b1bfc1af22e3"), indent=1)[:600])
        print(json.dumps(ag_reply("e6494761-3480-42d0-9f8e-b1bfc1af22e3"), indent=1)[:300])
    else:
        serve()
