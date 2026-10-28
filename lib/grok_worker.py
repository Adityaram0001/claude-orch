#!/usr/bin/env python3
"""Grok engine for orch workers. Called by bin/orch; see docs/design.md, "Grok workers".

A Grok worker is `grok -p --resume <session>` on a session this module opens first over
ACP (`grok agent stdio`). Two session settings have no headless flag: the context window
(`session/set_model` `_meta.contextWindow`) and the session's rules (`session/new`
`_meta.rules`, which carries the worker protocol). Both persist across `--resume`.
A session opened over ACP does not get the project's CLAUDE.md / AGENTS.md injected the
way a plain headless session does, so `render` embeds those files in the rules.

Subcommands (one line each in main()):
  render    write the worker protocol with placeholders and project instructions filled in
  boot      grant folder trust, open the session, set model/window/effort; print its id
  translate Claude-syntax permission rules -> Grok rules (one per line)
  deny      the never-grantable deny list from worker_settings.json, in Grok syntax
  harvest   fold a streaming-json capture into run.json (Claude-compatible keys)
  classify  print OUTCOME<TAB>detail for a finished run
  billing   read the weekly allowance meter into the grok limits file (no model call)
  limits    print the Grok limits line and routing advice; exit 2 when EXHAUSTED
  latch     record a rate-limit outcome (or clear it) in the grok limits file
"""
import datetime
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time

GROK_BIN = os.environ.get("ORCH_GROK_BIN", "grok")
WINDOWS = {"256k": 256000, "500k": 500000}
STATUSES = ("DONE", "BLOCKED", "FAILED", "PLAN_CHANGE")
SECTIONS = ("## Summary", "## Claims verified", "## Root cause", "## What I did", "## Evidence",
            "## Open items", "## Needs from human", "## Cost of a shortcut")
RESULT_CAP = 150
INSTRUCTION_NAMES = ("CLAUDE.md", "CLAUDE.local.md", ".claude/CLAUDE.md", "AGENTS.md")
FILE_CAP = 10000    # characters embedded per instruction file (Grok's own cap for AGENTS.md)
TOTAL_CAP = 40000   # beyond this, files are listed for the worker to read instead of embedded
GROK_RULE_TOOLS = ("Bash", "Read", "Edit", "Write", "Grep", "WebFetch", "MCPTool")
TOOL_NAMES = {"run_terminal_command": "Bash", "read_file": "Read", "write": "Write", "search_replace": "Edit",
              "grep": "Grep", "list_dir": "LS", "web_fetch": "WebFetch", "web_search": "WebSearch"}
INPUT_KEYS = ("command", "file_path", "target_file", "target_directory", "path", "url", "query")
CANCELLED_RE = re.compile(r"user cancelled the execution for tool", re.I)
DENIED_RE = re.compile(r"denied by permission policy", re.I)
RATE_RE = re.compile(r"rate.?limit|too many requests|usage limit|credit limit|\b429\b", re.I)
ROLE_SHELL = {
    "explore": "Explore role: read-only. You write only inside `{{WORKER_DIR}}/`, with the write/edit tools. "
               "No scripts, no commits.",
    "build": "Build role: loops, environment variables, redirects and anything multi-step go in a script you "
             "write with the write tool into `{{WORKER_DIR}}/scratch/<name>.sh` or `.py`, run as exactly "
             "`bash {{WORKER_DIR}}/scratch/<name>.sh` or `python3 {{WORKER_DIR}}/scratch/<name>.py` from the project root.",
}


def _read(path):
    with open(path, errors="replace") as fh:
        return fh.read()


def _load(path):
    try:
        data = json.loads(_read(path))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(path, data):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


def _grok_env():
    """Grok's Claude Code compatibility layer starts the user's Claude MCP servers and runs Claude hooks.
    Neither belongs in a worker or in orch's own ACP calls (opt back in with ORCH_GROK_CLAUDE_MCPS=1 /
    ORCH_GROK_CLAUDE_HOOKS=1)."""
    env = dict(os.environ)
    env["GROK_CLAUDE_MCPS_ENABLED"] = os.environ.get("ORCH_GROK_CLAUDE_MCPS", "0")
    env["GROK_CLAUDE_HOOKS_ENABLED"] = os.environ.get("ORCH_GROK_CLAUDE_HOOKS", "0")
    return env


# ---------------------------------------------------------------- protocol
def instruction_files(project):
    """Instruction files a Claude worker in `project` would see: every directory from the project
    up to, not including, $HOME. Outermost first. One entry per file (macOS is case-insensitive,
    so CLAUDE.md and Claude.md can be the same file)."""
    home = os.path.realpath(os.path.expanduser("~"))
    d = os.path.realpath(project)
    groups, seen = [], set()
    while d != home:
        here = []
        for name in INSTRUCTION_NAMES:
            path = os.path.join(d, name)
            if os.path.isfile(path):
                st = os.stat(path)
                if (st.st_dev, st.st_ino) not in seen:
                    seen.add((st.st_dev, st.st_ino))
                    here.append(path)
        groups.insert(0, here)
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return [path for group in groups for path in group]


def instructions_block(project):
    files = instruction_files(project)
    if not files:
        return "No CLAUDE.md or AGENTS.md was found for this project.\n"
    parts, listed, used = [], [], 0
    for path in files:
        text = _read(path).rstrip()
        if len(text) > FILE_CAP:
            text = text[:FILE_CAP] + f"\n[truncated at {FILE_CAP} characters; read `{path}` for the rest]"
        if used + len(text) > TOTAL_CAP:
            listed.append(path)
            continue
        used += len(text)
        parts.append(f"### `{path}`\n\n{text}\n")
    if listed:
        parts.append("Not reproduced here for size; read them yourself before you start:\n"
                     + "".join(f"- `{path}`\n" for path in listed))
    return "\n".join(parts)


def render(template, out, project, worker_id, worker_dir, manager, role):
    text = _read(template).replace("{{ROLE_SHELL}}", ROLE_SHELL[role])
    for key, value in (("WORKER_ID", worker_id), ("WORKER_DIR", worker_dir), ("PROJECT_ROOT", project),
                       ("MANAGER_NAME", manager), ("ROLE", role)):
        text = text.replace("{{" + key + "}}", value)
    # Last, so placeholder-like text inside the user's files is left alone.
    text = text.replace("{{INSTRUCTION_FILES}}", instructions_block(project))
    with open(out, "w") as fh:
        fh.write(text)


# ---------------------------------------------------------------- ACP
class Acp:
    """Just enough JSON-RPC over `grok agent stdio` to open a session and read the meter.
    Requests from the agent (permission prompts, file system calls) are refused: nothing runs here."""

    def __init__(self, cwd=None, log=None):
        self.proc = subprocess.Popen([GROK_BIN, "agent", "--no-leader", "stdio"], cwd=cwd, env=_grok_env(),
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=log or subprocess.DEVNULL, text=True, bufsize=1)
        self.lines = queue.Queue()
        self.next_id = 0
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for line in self.proc.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def _send(self, message):
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def call(self, method, params, timeout=60):
        self.next_id += 1
        my_id = self.next_id
        self._send({"jsonrpc": "2.0", "id": my_id, "method": method, "params": params})
        deadline = time.time() + timeout
        while True:
            try:
                line = self.lines.get(timeout=max(deadline - time.time(), 0.01))
            except queue.Empty:
                raise TimeoutError(f"{method}: no answer in {timeout}s") from None
            if line is None:
                raise RuntimeError(f"{method}: grok agent exited")
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if "method" in message and "id" in message:
                self._send({"jsonrpc": "2.0", "id": message["id"],
                            "error": {"code": -32601, "message": "not supported by orch"}})
            elif message.get("id") == my_id:
                if "error" in message:
                    raise RuntimeError(f"{method}: {json.dumps(message['error'])[:300]}")
                return message.get("result") or {}

    def start(self):
        self.call("initialize", {"protocolVersion": 1, "clientCapabilities": {
            "fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False}})
        self.call("authenticate", {"methodId": "cached_token"})   # the reply carries account details; never logged
        return self

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()


def boot(project, model, window, effort, rules_path, log_path):
    """Open the worker's session and return its id."""
    with open(log_path, "a") as log:
        if os.environ.get("ORCH_GROK_TRUST", "1") != "0":
            # Grok loads project skills, hooks and settings only in trusted folders. No model call.
            subprocess.run([GROK_BIN, "--cwd", project, "--trust", "inspect"], env=_grok_env(),
                           stdout=subprocess.DEVNULL, stderr=log, timeout=60, check=False)
        log.flush()
        acp = Acp(project, log).start()
        try:
            sid = acp.call("session/new", {"cwd": project, "mcpServers": [],
                                           "_meta": {"rules": _read(rules_path)}})["sessionId"]
            meta = {"contextWindow": WINDOWS[window]}
            if effort:
                meta["reasoningEffort"] = effort
            answer = acp.call("session/set_model", {"sessionId": sid, "modelId": model, "_meta": meta})
            refused = (answer.get("_meta") or {}).get("model") or {}
            if isinstance(refused, dict) and "Err" in refused:
                raise RuntimeError(f"session/set_model refused {model}: {json.dumps(refused)[:300]}")
            try:
                acp.call("session/close", {"sessionId": sid}, timeout=15)
            except Exception:
                pass
            return sid
        finally:
            acp.close()


# ---------------------------------------------------------------- permission rules
def translate(rule):
    """One Claude-syntax rule -> its Grok rules. `Bash(git add:*)` -> `Bash(git add)`, `Bash(git add *)`.
    Tools Grok has no rule prefix for (Glob, WebSearch, SendMessage, ...) yield nothing."""
    rule = rule.strip()
    match = re.match(r"^(\w+)(?:\((.*)\))?$", rule, re.S)
    if not match or match.group(1) not in GROK_RULE_TOOLS:
        return []
    tool, body = match.groups()
    if body is None:
        return [tool]
    if body.startswith("~/"):
        body = os.path.expanduser("~") + body[1:]
    if tool == "Bash" and body.endswith(":*"):
        prefix = body[:-2].rstrip()
        return [f"Bash({prefix})", f"Bash({prefix} *)"]
    return [f"{tool}({body})"]


def deny_rules(settings_path):
    """The workers' never-grantable deny list in Grok syntax. Grok runs `git -C <dir> <sub>` (Claude denies
    that shape outright), so every git rule also gets its `git -C` form. Workers may not start agents or
    orch, nor write Grok's own settings."""
    rules = []
    for rule in json.loads(_read(settings_path))["permissions"]["deny"]:
        rules += translate(rule)
        match = re.match(r"^Bash\(git (.+):\*\)$", rule)
        if match:
            rules.append(f"Bash(git -C * {match.group(1).rstrip()}*)")
    rules += ["Bash(grok)", "Bash(grok *)", "Bash(orch)", "Bash(orch *)"]
    rules += [f"{tool}({os.path.expanduser('~')}/.grok/**)" for tool in ("Edit", "Write")]
    return rules


# ---------------------------------------------------------------- harvest + classify
def _events(path):
    events = []
    try:
        lines = _read(path).splitlines()
    except OSError:
        return events
    for line in lines:
        line = line.strip()
        if line:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                events.append(event)
    return events


def _content_text(event):
    out = []
    for item in event.get("content") or []:
        if isinstance(item, dict):
            inner = item.get("content")
            out.append(str(inner.get("text") or "") if isinstance(inner, dict) else str(item.get("text") or ""))
    return " ".join(out)


def harvest(stream_path, stderr_path, run_path, model):
    """Fold a streaming-json capture into run.json. Keys match a Claude run.json where orch status and
    orch report read them (session_id, num_turns, total_cost_usd, terminal_reason, is_error, result,
    permission_denials[].tool_name / .tool_input.command)."""
    events = _events(stream_path)
    try:
        stderr = _read(stderr_path)[-4000:]
    except OSError:
        stderr = ""
    calls, denials, texts, errors = {}, [], [], []
    end, cancelled, max_turns = {}, None, False
    for event in events:
        kind = event.get("type")
        if kind == "end":
            end = event
        elif kind == "text":
            texts.append(str(event.get("data") or ""))
        elif kind == "error":
            errors.append(str(event.get("message") or event.get("data") or event))
        elif kind == "max_turns_reached":
            max_turns = True
        elif kind in ("tool_call", "tool_call_update"):
            cid = str(event.get("toolCallId") or "")
            raw = event.get("rawInput") if isinstance(event.get("rawInput"), dict) else {}
            known = calls.setdefault(cid, {"tool": "", "input": ""})
            known["tool"] = str(event.get("toolName") or event.get("title") or known["tool"])
            known["input"] = str(next((raw[k] for k in INPUT_KEYS if raw.get(k)), known["input"]))[:300]
            if event.get("status") == "failed":
                text = _content_text(event)
                denial = {"tool": known["tool"], "input": known["input"],
                          "tool_name": TOOL_NAMES.get(known["tool"], known["tool"]),
                          "tool_input": {"command": known["input"]}}
                if CANCELLED_RE.search(text):
                    cancelled = denial
                    denials.append(denial)
                elif DENIED_RE.search(text):
                    denials.append(denial)
    stop = str(end.get("stopReason") or "")
    if stop == "max_turn_requests" or re.search(r"max turns reached", stderr, re.I):
        max_turns = True
    message = " ".join(errors).strip()
    if not end and not message:
        message = stderr.strip()[-400:] or "no output from grok"
    if max_turns:
        terminal = "max_turns"
    elif cancelled:
        terminal = "permission_cancelled"
    elif stop == "end_turn":
        terminal = "completed"
    else:
        terminal = "error"
    run = {
        "engine": "grok",
        "model": model,
        "session_id": end.get("sessionId"),
        "num_turns": end.get("num_turns"),
        "total_cost_usd": end.get("total_cost_usd"),   # xAI's API-equivalent figure; the plan bills weekly
        "usage": end.get("usage"),
        "stop_reason": stop,
        "terminal_reason": terminal,
        "is_error": terminal == "error",
        "error_message": message[:1000],
        "result": (message or "".join(texts))[-500:],
        "cancelled_by": cancelled,
        "permission_denials": denials,
    }
    _save(run_path, run)
    return run


def contract_problems(text):
    """What is wrong with a result.md, as short phrases. Empty means it follows the protocol."""
    if text is None:
        return ["no result.md"]
    lines = text.splitlines() or [""]
    problems = []
    match = re.match(r"^STATUS:\s*([A-Z_]+)\s*$", lines[0])
    if not match or match.group(1) not in STATUSES:
        problems.append("line 1 is not STATUS: DONE|BLOCKED|FAILED|PLAN_CHANGE")
    headings = [line for line in lines if line.startswith("## ")]
    problems += [f"missing {s}" for s in SECTIONS if not any(h.startswith(s) for h in headings)]
    if len(lines) > RESULT_CAP:
        problems.append(f"{len(lines)} lines (cap {RESULT_CAP})")
    if match and match.group(1) == "BLOCKED":
        body = text.split("## Needs from human", 1)[-1].split("\n## ", 1)[0].strip().lower()
        if body in ("", "none", "n/a"):
            problems.append("BLOCKED with nothing under Needs from human")
    return problems


def classify(run_path, result_path):
    """Print `OUTCOME<TAB>detail` and return OUTCOME."""
    run = _load(run_path)
    if not run:
        print("SCRIPT_ERROR\trun.json unreadable (grok did not start?)")
        return "SCRIPT_ERROR"
    text = _read(result_path) if os.path.exists(result_path) else None
    problems = contract_problems(text)
    status = text.splitlines()[0].split(":", 1)[-1].strip() if text and not problems else None
    cost = run.get("total_cost_usd")
    info = (f"turns={run.get('num_turns')} cost_usd={'?' if cost is None else format(cost, '.4f')} "
            f"session={run.get('session_id')} denials={len(run.get('permission_denials') or [])} "
            f"terminal={run.get('terminal_reason')}")
    cancelled = run.get("cancelled_by")

    def out(word, detail):
        print(f"{word}\t{detail}")
        return word

    if run.get("is_error") and RATE_RE.search(str(run.get("error_message"))):
        return out("RATE_LIMITED", f"{str(run.get('error_message'))[:160]} | {info}")
    if status:
        late = {"max_turns": " (hit max_turns after writing result)",
                "permission_cancelled": " (cancelled by an unallowed command after writing result)"}
        return out(status, info + late.get(run.get("terminal_reason"), ""))
    if cancelled:
        return out("BLOCKED", f"not allowed: {cancelled.get('tool')}: {cancelled.get('input')} | {info}")
    if text is not None:
        return out("INCOMPLETE", f"{'; '.join(problems)} | {info}")
    if run.get("terminal_reason") == "max_turns":
        return out("MAX_TURNS", f"no result.md written | {info}")
    if run.get("is_error"):
        return out("API_ERROR", f"{str(run.get('error_message'))[:160]} | {info}")
    return out("EXITED_NO_RESULT", f"{str(run.get('result'))[:120]} | {info}")


# ---------------------------------------------------------------- weekly allowance
def _ts(value):
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def refresh_billing(limits_path, max_age=0):
    """Read the weekly meter (`_x.ai/billing`, what Grok's /usage shows). Keeps old data on failure."""
    data = _load(limits_path)
    if max_age and time.time() - ((data.get("billing") or {}).get("at") or 0) < max_age:
        return True
    try:
        acp = Acp().start()
        try:
            config = acp.call("_x.ai/billing", {}, timeout=30).get("config") or {}
        finally:
            acp.close()
    except Exception as exc:
        print(f"grok: weekly meter unavailable: {exc}", file=sys.stderr)
        return False
    period = config.get("currentPeriod") or {}
    data["billing"] = {"used_percentage": config.get("creditUsagePercent"), "period_type": period.get("type"),
                       "period_start": period.get("start") or config.get("billingPeriodStart"),
                       "period_end": period.get("end") or config.get("billingPeriodEnd"), "at": time.time()}
    used = data["billing"]["used_percentage"]
    if data.get("status") == "rate_limited" and isinstance(used, (int, float)) and used < 97:
        data["status"] = "ok"   # the meter shows allowance again; the next rate limit sets the latch anew
    _save(limits_path, data)
    return True


def budget(data, now=None):
    """Weekly pace -> SURPLUS | ON_PACE | TIGHT | EXHAUSTED | UNKNOWN, with the numbers behind it."""
    now = now or time.time()
    bill = data.get("billing") or {}
    used, start, end = bill.get("used_percentage"), _ts(bill.get("period_start")), _ts(bill.get("period_end"))
    out = {"word": "EXHAUSTED" if data.get("status") == "rate_limited" else "UNKNOWN",
           "used": used, "elapsed": None, "projected": None, "hours_left": None}
    if not isinstance(used, (int, float)) or not start or not end or end <= start:
        return out
    elapsed = min(max((now - start) / (end - start), 0.0), 1.0)
    hours_left = max(end - now, 0) / 3600
    projected = used / max(elapsed, 0.1)   # early in the week the pace is noise; floor at 10% elapsed
    out.update(elapsed=round(100 * elapsed), projected=round(projected), hours_left=hours_left)
    if out["word"] == "EXHAUSTED" or used >= 97:
        out["word"] = "EXHAUSTED"
    elif projected >= 100:
        out["word"] = "TIGHT"
    elif projected <= 70 or (hours_left <= 36 and used <= 75):
        out["word"] = "SURPLUS"
    else:
        out["word"] = "ON_PACE"
    return out


ADVICE = {
    "SURPLUS": "allowance would expire unused: every grok-eligible unit to grok, plus useful extra grok units (manager skill section 11)",
    "ON_PACE": "every grok-eligible unit to grok; grok-4.7, build-fast only on the critical path",
    "TIGHT": "grok only for large grok-eligible units; small ones to claude; no build-fast",
    "EXHAUSTED": "no grok spawns; route grok-eligible units to claude until the weekly reset",
    "UNKNOWN": "run `orch limits --refresh` (reads the meter; no model call)",
}


def show_limits(limits_path):
    data = _load(limits_path)
    if not data:
        print("grok: no data yet. Run: orch limits --refresh   (reads the weekly meter; no model call)")
        return 3
    b = budget(data)
    bill = data.get("billing") or {}
    parts = [f"grok: budget={b['word']}"]
    if b["used"] is not None:
        parts.append(f"weekly used={b['used']:g}%")
    if b["elapsed"] is not None:
        reset = datetime.datetime.fromtimestamp(_ts(bill["period_end"])).strftime("%a %H:%M")
        hours = b["hours_left"]
        parts += [f"week {b['elapsed']}% elapsed", f"pace->{b['projected']}% at reset",
                  f"resets_in={int(hours // 24)}d{int(hours % 24):02d}h ({reset})"]
    age = int(time.time() - (bill.get("at") or data.get("latched_at") or 0)) // 60
    parts.append(f"as_of={age}m ago" + (" (STALE: run 'orch limits --refresh')" if age > 60 else ""))
    if data.get("status") == "rate_limited":
        parts.append(f"rate-limited by {data.get('latched_by')}: {str(data.get('latch_message'))[:120]}")
    print("  ".join(parts))
    print(f"grok advice: {ADVICE[b['word']]}")
    return 2 if b["word"] == "EXHAUSTED" else 0


def latch(limits_path, outcome, source, message=""):
    """A grok RATE_LIMITED sets the latch; any outcome showing grok took the prompt clears it."""
    data = _load(limits_path)
    if outcome == "RATE_LIMITED":
        data.update(status="rate_limited", latched_by=source, latch_message=message[:400], latched_at=time.time())
    elif outcome in STATUSES + ("INCOMPLETE", "MAX_TURNS"):
        data["status"] = "ok"
    else:
        return
    _save(limits_path, data)


def main(argv):
    cmd, args = (argv[1], argv[2:]) if len(argv) > 1 else ("", [])
    if cmd == "render":      # render <template> <out> <project> <worker-id> <worker-dir> <manager> <role>
        render(*args[:7])
    elif cmd == "boot":      # boot <project> <model> <256k|500k> <effort> <rules-file> <log>
        try:
            print(boot(*args[:6]))
        except Exception as exc:
            with open(args[5], "a") as log:
                log.write(f"orch boot failed: {exc}\n")
            print(f"grok session bootstrap failed: {exc}", file=sys.stderr)
            return 1
    elif cmd == "translate":  # translate <rule>...
        for rule in args:
            for grok_rule in translate(rule):
                print(grok_rule)
    elif cmd == "deny":      # deny <worker_settings.json>
        print("\n".join(deny_rules(args[0])))
    elif cmd == "harvest":   # harvest <stream.jsonl> <stderr.log> <run.json> <model>
        harvest(*args[:4])
    elif cmd == "classify":  # classify <run.json> <result.md>
        classify(*args[:2])
    elif cmd == "billing":   # billing <limits.json> [max-age-seconds]
        return 0 if refresh_billing(args[0], int(args[1]) if len(args) > 1 else 0) else 1
    elif cmd == "limits":    # limits <limits.json>
        return show_limits(args[0])
    elif cmd == "latch":     # latch <limits.json> <outcome> <source> [message]
        latch(*args[:4])
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
