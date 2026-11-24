---
name: antigravity-orchestration
description: Delegate implementation work to Gemini in the Antigravity App through the ag-bridge MCP tools (ag_start, ag_status, ag_questions, ag_answer, ag_reply, ag_tail, ag_send). Use when the user says to have Antigravity, Gemini or AG implement something, or to hand a task to the Antigravity agent and handle its questions.
---

# Orchestrating Antigravity (Gemini) from Claude Code

You brief Gemini, watch it work, answer its questions, and check what it built. Gemini runs in the
user's Antigravity App, so the user can open the conversation and watch.

## Before the first call

-> The App must be running and set to **Turbo** mode. In Request Review mode a command-approval
popup blocks the agent and no tool can answer it. If `ag_status` returns `unknown` for a long time
with no steps, ask the user to check the App.

-> First time on a machine: the Antigravity side needs `python install_antigravity.py` from the plugin
folder, then an App restart. If `ag_start` works but no question ever arrives, that step is missing.

## The loop

1. **`ag_start(prompt, workspace, model)`**. Always pass `workspace`: the absolute path of the project
   folder you are working in. Returns `conversation_id` and `task_id`. Pick `model` from `ag_models`.
   Tell the user the conversation title: `orchestrated <task_id>`.
2. **Check `ag_status(conversation_id)`**, or arm the watcher below for several agents. A task takes from
   30 seconds to several minutes. Never poll in a tight loop. States:
   - `running`: keep waiting.
   - `asking`: Gemini posted a question. Go to step 3.
   - `done`: Gemini stopped. Go to step 4.
   - `asking_popup_unanswerable`: Gemini used its built-in popup. Tell the user to answer it in the App.
   - `unknown`: nothing happened yet. Wait, then tell the user.
3. **Answer questions.** `ag_questions` lists them. Answer with `ag_answer(question_id, text)` when the
   answer follows from the task or the project rules. **When it is the user's decision, ask the user
   first.** Never guess a product or design choice for them.
4. **Read the result.** `ag_reply` gives Gemini's report. `ag_tail` gives the steps, tool calls and errors.
5. **Check the work yourself.** Read the changed files and run the build or tests. A report that
   says "done" is a claim, not proof.
6. **Follow up** with `ag_send(conversation_id, text)` on the same conversation when it is idle.
   Start a new conversation for an unrelated task.

## The workspace: the folder you work in

`ag_start` binds the conversation to the workspace through an Antigravity **project**. It looks for a project
that already holds the folder. For a git checkout without one, it writes a project file in
`~/.gemini/config/projects/` and starts the conversation under it. The conversation then has a real workspace:
the App lists it under that project, hooks receive `workspacePaths`, and shell commands start in the folder.
The result shows `project: {id, status}` where status is `existing`, `created`, or `none: <reason>`.

-> **A folder that is not a git checkout gets no project.** The brief still names it on a `Workspace:` line,
and a hook starts shell commands there, but the App shows the chat outside any project.

-> **A created project appears in the user's App sidebar.** Say so when `status` is `created`.

The brief also names the folder on a `Workspace:` line, which is how the Wire plugin finds it without a project.

-> **Pass the project you are working in.** An agent that works on `my-project` passes the path of
`my-project`. Never rely on the default:
it is the folder the bridge server started in, which may not be your project.

-> **Wire gets the right repository.** The Wire plugin's SessionStart hook reads the `Workspace:` line and
sends the checkout facts of that folder (`remote`, `first_commit`, `branch`, `head_sha`, `dirty`) to
`session_open`. Gemini opens its Wire session on your project, not on a guess.

-> **A path that is not an existing folder is refused,** and nothing starts.

-> **Gemini edits only inside the workspace** unless the task names another absolute path. When the task needs
a file outside it, name that path in the prompt.

## Passing skills to Gemini

Gemini cannot see your loaded skills. Pass them with `ag_start(prompt, skills=[…])`. The bridge turns each
entry into an absolute path and lists the paths in the brief, with the instruction to read each file and
follow it. Gemini reads the files itself, including sibling files such as `references/…` inside the skill folder.

-> **By name:** `skills: ["engineering", "antigravity-bridge:antigravity-orchestration"]`. `ag_skills` lists every
name the bridge can resolve: project skills (`.claude/skills` of the current project), user skills
(`~/.claude/skills`) and installed plugin skills (`plugin:name`).

-> **By path:** an absolute path to a `SKILL.md`, or to the skill folder. Use this for a skill that is not installed.

-> **An unknown name is refused** with the list of available names. Nothing starts.

Rules:

-> **Pass only the skills the task needs.** Each one costs Gemini a file read and context.

-> **A skill written for Claude Code names Claude tools.** The brief tells Gemini to ignore tools it lacks and apply
the method. For a skill that depends heavily on Claude-only tools, write the method into the prompt instead.

-> **The skill file is read at task start.** A later edit does not reach a conversation already running.

## Choosing the model

`ag_start(prompt, model)` takes a key from `ag_models`. The list lives in
`~/.gemini/ag-bridge/models.json`. Add an exact id there when the user gets access to a new model.
An id that is not in the list is refused with the allowed list.

Measured on this machine (2026-11-23):

| Requested | Actually ran |
|---|---|
| `flash` | `gemini-3.8-flash-high`, or `gemini-3.8-flash-tiered` when capacity is short |
| `flash_lite` | `gemini-3.5-flash-lite` |
| `pro` | `gemini-3.8-flash-tiered` (silent fallback, no pro access) |
| `gemini-4-neon-medium` | `gemini-3.8-flash-tiered` before the Pro plan, now runs as asked |

After the Pro plan (re-measured the same day): `flash` and `pro` now run `gemini-4-hydrogen-tiered-high` and
`gemini-4-hydrogen-tiered-xhigh`. The exact ids for neon, argon and hydrogen at low, medium and high ran as asked.
`carbon` at medium and high fell back to hydrogen, `gemini-3.1-pro-*` and `gemini-3.7-flash-high` fell back too,
and `carbon-low` crashed once. The live list, with notes, is `ag_models`. Trust it over this table.

An agent that stops with no reply (state `error`) crashed. Retry with `ag_send` or start again on another model.

**A model without access falls back to flash and reports no error.** After `ag_start`, read
`model_used` in `ag_status`, or watch for the `MODEL_MISMATCH` line. On a mismatch, tell the user
before trusting the result, since a hard task may need the model that was asked for.

Pick the model by the work: flash for small edits and file writing, a stronger model for design,
refactors across many files, or anything where a wrong guess is costly.

## Running several agents without waiting in place

Do not poll `ag_status` in a loop. Start every agent, then arm one Monitor that pushes events to you.

1. Call `ag_start` once per task. Keep each `conversation_id`.
2. Arm the watcher with the Monitor tool, one `--conv` per conversation:

```
cd <plugin root>/server && python watch.py --conv <id1> --conv <id2> --conv <id3> --until-done
```

   Use `${CLAUDE_PLUGIN_ROOT}` for the plugin root. Set `timeout_ms` to 1800000 and re-arm on expiry.
3. Keep working on other things. Each event line wakes you:
   - `ASKING conv=… q=…`: answer with `ag_answer`, or ask the user if it is their decision.
   - `DONE conv=…`: read `ag_reply`, then check the work on disk.
   - `POPUP conv=…`: a built-in question only the user can answer in the App. Tell the user which chat.
   - `STALLED` or `ERROR`: look at `ag_tail`, then send a nudge with `ag_send` or tell the user.
   - `ALL_DONE`: every watched agent finished.
4. After an `ag_send` follow-up, arm the watcher again for that conversation.

Arm the watcher right after `ag_start`. A conversation that already finished when the watcher
starts counts as a baseline and does not report DONE.

`ag_list` shows the conversations you started with `ag_start`, with their state.

The bridge covers only conversations started with `ag_start`. A chat the user starts by hand in the
App has no task id, so its questions cannot be routed to you.

## Writing the brief

Gemini starts with no memory of this project. The brief carries everything.

-> **State the goal and the done condition.** "Add X so that Y passes", not "work on X".

-> **Name the files and folders.** Give absolute paths.

-> **Paste the rules that matter**: build command, test command, style rules, what not to touch.

-> **Say what to report**: files changed, commands run, results, what it did not do.

-> **Never promise** a step Gemini cannot do. It has no access to your tools or this conversation.

`ag_start` already adds the rules about asking questions and about not searching for missing
files. Do not repeat them. Still name every path Gemini needs, because it will ask rather than look.

If Gemini starts wandering anyway (many steps in `ag_tail` reading unrelated files), send
`ag_send` with "Stop. Report what is missing in one sentence and end your turn."

## Rules for you

-> One conversation per task. Keep the `conversation_id` and `task_id` in your notes.

-> Do not start a second conversation that edits the same files while the first runs.

-> If a task fails with `ERROR_MESSAGE` steps such as "No capacity available", wait a minute and send
`ag_send(conversation_id, "continue")`. If it repeats, tell the user.

-> The bridge reads `~/.gemini/antigravity` and the App's local daemon. Both are the user's own
machine. Never print the daemon token.
