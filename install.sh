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
export PATH="$HOME/.local/bin:$PATH"
command -v claude >/dev/null 2>&1 || echo "warning: claude not on PATH; install Claude Code (https://code.claude.com/docs/en/getting-started/installation) or run: claude install latest"
echo "installed skills: $(ls -1 "$HERE"/skills | tr '\n' ' ')"
echo "orch -> $HOME/.local/bin/orch"
echo "Open a NEW Claude Code session for the statusline to start reporting; then 'orch limits' works."
