#!/usr/bin/env bash
# Run serial-xfer serve on Jetson as an always-on systemd service (it also starts at boot).
#
#   ./deploy/install-jetson-service.sh            # install / update (asks for the sudo password)
#   ./deploy/install-jetson-service.sh uninstall  # remove
#
# Settings can be changed with environment variables (defaults are the fastest settings):
#   PORT=/dev/ttyTHS1 BAUD=3000000 CHUNK=59000 RTSCTS=1 ROOT=$HOME/share
set -euo pipefail

NAME=serial-xfer
UNIT=/etc/systemd/system/$NAME.service

if [[ "${1:-}" == "uninstall" ]]; then
    sudo systemctl disable --now "$NAME.service" 2>/dev/null || true
    sudo rm -f "$UNIT"
    sudo systemctl daemon-reload
    echo "Removed $NAME.service."
    exit 0
fi

PROJECT=$(cd "$(dirname "$0")/.." && pwd)
PORT=${PORT:-/dev/ttyTHS1}
BAUD=${BAUD:-3000000}
CHUNK=${CHUNK:-59000}
RTSCTS=${RTSCTS:-1}
ROOT=${ROOT:-$HOME/share}
RUN_USER=$(id -un)

cd "$PROJECT"
uv sync
mkdir -p "$ROOT"

OPTS="-p $PORT -b $BAUD --chunk $CHUNK"
[[ "$RTSCTS" == "1" ]] && OPTS="$OPTS --rtscts"

sudo tee "$UNIT" >/dev/null <<EOF
[Unit]
Description=Serial file transfer server (serial-xfer serve on $PORT)

[Service]
Type=simple
User=$RUN_USER
SupplementaryGroups=dialout
WorkingDirectory=$ROOT
ExecStart="$PROJECT/.venv/bin/serial-xfer" serve $OPTS --root "$ROOT"
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable "$NAME.service"
sudo systemctl restart "$NAME.service"
sleep 2
systemctl --no-pager --lines=5 status "$NAME.service" || true
echo
echo "Installed. Received files go to: $ROOT"
echo "  Log:    journalctl -u $NAME -f"
echo "  Stop:   sudo systemctl stop $NAME      (stop it when using the port from Jetson for put etc.)"
echo "  Start:  sudo systemctl start $NAME"
