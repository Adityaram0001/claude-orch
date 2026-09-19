#!/usr/bin/env bash
# Install the orchestration toolkit for the current user:
#   `orch` on PATH, skills in ~/.claude/skills, statusline that records usage limits + context.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$HOME/.local/bin" "$HOME/.claude/skills" "$HOME/.claude/orch/sessions"
ln -sfn "$HERE/bin/orch" "$HOME/.local/bin/orch"
ln -sfn "$HERE/bin/orch-statusline" "$HOME/.local/bin/orch-statusline"
for d in "$HERE"/skills/*/; do
  n="$(basename "$d")"
  ln -sfn "$d" "$HOME/.claude/skills/$n"
done
# Statusline: only set it if the user has none (never overwrite a custom one).
python3 - "$HOME/.claude/settings.json" <<'PY'
import json,sys,os
p=sys.argv[1]; s=json.load(open(p)) if os.path.exists(p) else {}
if 'statusLine' in s and 'orch-statusline' not in json.dumps(s['statusLine']):
    print("statusLine already set to something else; leaving it. To record limits, make it run orch-statusline.")
else:
    s['statusLine']={"type":"command","command":os.path.expanduser("~/.local/bin/orch-statusline")}
    json.dump(s,open(p,'w'),indent=2); print("statusLine -> orch-statusline (records 5h/7d limits and context% for orch limits/brief)")
PY
# PreCompact hook: records compactions per session so `orch context` has a signal inside IDE extensions
# (which never run the statusline). Appended only if not already present.
python3 - "$HOME/.claude/settings.json" <<'PY'
import json,sys,os
p=sys.argv[1]; s=json.load(open(p)) if os.path.exists(p) else {}
cmd=("python3 -c \"import json,sys,os,time; d=json.load(sys.stdin); "
     "p=os.path.expanduser('~/.claude/orch/sessions/'+d.get('session_id','unknown')+'.compact.log'); "
     "os.makedirs(os.path.dirname(p),exist_ok=True); "
     "open(p,'a').write(json.dumps({'t':time.time(),'session_id':d.get('session_id'),'cwd':d.get('cwd'),'trigger':d.get('trigger')})+'\\n')\"")
hooks=s.setdefault('hooks',{}); pre=hooks.setdefault('PreCompact',[])
if not any('orch/sessions' in json.dumps(h) for h in pre):
    pre.append({"hooks":[{"type":"command","command":cmd}]})
    json.dump(s,open(p,'w'),indent=2); print("PreCompact hook -> records compactions for orch context")
else: print("PreCompact hook already installed")
PY
export PATH="$HOME/.local/bin:$PATH"
command -v claude >/dev/null 2>&1 || echo "warning: claude not on PATH; install Claude Code (https://code.claude.com/docs/en/getting-started/installation) or run: claude install latest"
echo "installed skills: $(ls -1 "$HERE"/skills | tr '\n' ' ')"
echo "orch -> $HOME/.local/bin/orch"
echo "Open a NEW Claude Code session for the statusline to start reporting; then 'orch limits' works."
