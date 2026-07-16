#!/usr/bin/env bash
# Install the SkillOpt-Sleep Devin integration into a project.
# Copies the SessionEnd hook and rules snippet into .devin/, and prints
# the MCP server registration command. Idempotent.
set -euo pipefail

PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$PLUGIN_DIR/../.." && pwd)"
PROJECT="${1:-$(pwd)}"

echo "[install] repo: $REPO_ROOT"
echo "[install] project: $PROJECT"

DEVIN_DIR="$PROJECT/.devin"
mkdir -p "$DEVIN_DIR/hooks" "$DEVIN_DIR/rules"

# 1) SessionEnd hook (on by default — provides activity signal for nightly harvest)
cp "$PLUGIN_DIR/hooks/hooks.v1.json" "$DEVIN_DIR/hooks.v1.json"
cp "$PLUGIN_DIR/hooks/on-session-end.sh" "$DEVIN_DIR/hooks/on-session-end.sh"
chmod +x "$DEVIN_DIR/hooks/on-session-end.sh"
echo "[install] session-end hook  -> $DEVIN_DIR/hooks.v1.json"
echo "[install] hook script       -> $DEVIN_DIR/hooks/on-session-end.sh"

# 2) Rules snippet so Devin proactively offers the tools
cp "$PLUGIN_DIR/devin-rules.snippet.md" "$DEVIN_DIR/rules/skillopt-sleep.md"
echo "[install] rules snippet     -> $DEVIN_DIR/rules/skillopt-sleep.md"

# 3) Print the MCP server registration command
cat <<EOF

[install] Register the MCP server (run once per machine):

  devin mcp add skillopt-sleep \\
    --env "SKILLOPT_DEVIN_CLAUDE_HOME=\$HOME/.skillopt-sleep-devin" \\
    -- python3 $PLUGIN_DIR/mcp_server.py

Done. Try asking Devin:
  Run the sleep cycle for this project.
EOF
