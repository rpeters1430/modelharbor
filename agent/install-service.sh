#!/usr/bin/env bash
# Linux counterpart of install-task.ps1. Run as the user who owns the ComfyUI models (not root):
#   bash agent/install-service.sh
# Installs two systemd *user* units: a 30-minute inventory sync timer, and the always-on worker that
# carries out dashboard requests (Scan now, hash checks, downloads, deletes, conversions).
set -euo pipefail

AGENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/ModelHarbor"
CONFIG="$CONFIG_DIR/agent.json"
ENV_FILE="$CONFIG_DIR/agent.env"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
VENV="$AGENT_DIR/.venv"

if [ "$(id -u)" -eq 0 ]; then
    echo "Run this as your own user, not root: the agent must see your models with your permissions." >&2
    exit 1
fi
[ -f "$CONFIG" ] || { echo "Create $CONFIG from agent/config.example.json first." >&2; exit 1; }
if ! grep -q '^MODELHARBOR_AGENT_TOKEN=.\+' "$ENV_FILE" 2>/dev/null; then
    echo "Put MODELHARBOR_AGENT_TOKEN=<the NAS token> in $ENV_FILE first (optionally HF_TOKEN and CIVITAI_API_TOKEN too)." >&2
    exit 1
fi
chmod 600 "$ENV_FILE"  # the token must not be readable by other users

if [ ! -x "$VENV/bin/python" ]; then
    python3 -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --quiet --upgrade -r "$AGENT_DIR/requirements.txt"

mkdir -p "$UNIT_DIR"
COMMON="WorkingDirectory=$AGENT_DIR
EnvironmentFile=$ENV_FILE
Environment=PYTHONUNBUFFERED=1
Nice=10
IOSchedulingClass=idle"

cat > "$UNIT_DIR/modelharbor-sync.service" <<EOF
[Unit]
Description=ModelHarbor inventory sync (read-only ComfyUI and LLM server scan)
After=network-online.target

[Service]
Type=oneshot
$COMMON
ExecStart="$VENV/bin/python" "$AGENT_DIR/modelharbor_agent.py" --config "$CONFIG" --once
EOF

cat > "$UNIT_DIR/modelharbor-sync.timer" <<EOF
[Unit]
Description=Run the ModelHarbor inventory sync every 30 minutes

[Timer]
OnStartupSec=2min
OnUnitActiveSec=30min

[Install]
WantedBy=timers.target
EOF

cat > "$UNIT_DIR/modelharbor-worker.service" <<EOF
[Unit]
Description=ModelHarbor worker (dashboard scans, hash checks, downloads, deletes, conversions)
After=network-online.target

[Service]
$COMMON
ExecStart="$VENV/bin/python" "$AGENT_DIR/modelharbor_agent.py" --config "$CONFIG" --hash-worker
Restart=on-failure
RestartSec=60

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now modelharbor-sync.timer modelharbor-worker.service
systemctl --user start modelharbor-sync.service --no-block
echo "Installed and started modelharbor-sync.timer and modelharbor-worker.service."
echo "Logs: journalctl --user -u modelharbor-worker -u modelharbor-sync -f"
if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ]; then
    echo "They run while you are logged in. To keep them running after logout or before login: sudo loginctl enable-linger $USER"
fi
