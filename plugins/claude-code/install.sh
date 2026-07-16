#!/usr/bin/env bash
# Install or update SkillOpt-Sleep for Claude Code from a source checkout.
#
#   ./install.sh [repo-path]    install or update
#
# What this does:
#   1. Clone (or pull) the SkillOpt repo
#   2. uv tool install .  (engine snapshot — explicit reinstall to update)
#   3. claude plugin marketplace add  (register/update the marketplace)
#   4. claude plugin install          (install/update the plugin)
#
# After this, /skillopt-sleep and /skillopt-sleep-handoff work in any project.
# Re-run the script to update after git pull.
set -euo pipefail

REPO="${1:-$HOME/repos/SkillOpt}"

# 1) Clone or update the repo
if [ ! -d "$REPO/.git" ]; then
  echo "[install] cloning SkillOpt to $REPO"
  mkdir -p "$(dirname "$REPO")"
  git clone https://github.com/microsoft/SkillOpt.git "$REPO"
else
  echo "[install] updating $REPO"
  git -C "$REPO" pull --ff-only
fi

# 2) Install the engine as a snapshot from the local checkout
echo "[install] uv tool install (engine snapshot from local checkout)"
uv tool install "$REPO" --force

# 3) Register/update the marketplace from the local checkout
#    Rewrite marketplace.json's source to point at the local checkout,
#    so claude plugin install fetches from $REPO, not upstream main.
#    Use a temp copy to avoid dirtying the working tree.
PLUGIN_DIR="$REPO/plugins/claude-code"
TMP_PLUGIN_DIR=$(mktemp -d -t skillopt-plugin.XXXXXX)
trap 'rm -rf "$TMP_PLUGIN_DIR"' EXIT
cp -R "$PLUGIN_DIR/." "$TMP_PLUGIN_DIR/"
jq --arg repo "$REPO" \
  '.plugins[0].source = {"source": "git-subdir", "url": $repo, "path": "plugins/claude-code", "ref": "HEAD"}' \
  "$TMP_PLUGIN_DIR/.claude-plugin/marketplace.json" \
  > "$TMP_PLUGIN_DIR/.claude-plugin/marketplace.json.tmp" \
  && mv "$TMP_PLUGIN_DIR/.claude-plugin/marketplace.json.tmp" \
        "$TMP_PLUGIN_DIR/.claude-plugin/marketplace.json"

echo "[install] marketplace add (from local checkout)"
claude plugin marketplace add "$TMP_PLUGIN_DIR" --scope user

# 4) Install (or update) the plugin
echo "[install] plugin install"
claude plugin install skillopt-sleep@skillopt-sleep --scope user

cat <<EOF

[install] Done. Slash commands available in any project:
  /skillopt-sleep status
  /skillopt-sleep run
  /skillopt-sleep-handoff run

To update later: re-run this script. It pulls, reinstalls the engine snapshot,
and refreshes the plugin. You're in control of when the version changes.
EOF
