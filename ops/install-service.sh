#!/usr/bin/env bash
# Install (or remove) Stats EV Solver as a background service for the current user.
#
#   ops/install-service.sh install
#   ops/install-service.sh uninstall
#   ops/install-service.sh status
#
# Two things get installed: the API, kept running, and a scheduled job that records
# slates through the day and grades yesterday's picks each morning. Everything runs as
# your own user -- no root, no system-wide daemons.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ACTION="${1:-install}"
PY="$REPO_ROOT/.venv/bin/python"
OS="$(uname -s)"

API_LABEL="com.statsevsolver.api"
JOBS_LABEL="com.statsevsolver.jobs"

die() { echo "error: $*" >&2; exit 1; }

[ -x "$PY" ] || die "no virtualenv at $PY -- run 'make setup' first"

# --------------------------------------------------------------------- macOS
install_macos() {
  local agents="$HOME/Library/LaunchAgents"
  mkdir -p "$agents" "$REPO_ROOT/data/logs"

  cat > "$agents/$API_LABEL.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$API_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string><string>-m</string><string>app.cli</string><string>serve</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict><key>PYTHONPATH</key><string>$REPO_ROOT/backend</string></dict>
  <key>WorkingDirectory</key><string>$REPO_ROOT</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$REPO_ROOT/data/logs/api.log</string>
  <key>StandardErrorPath</key><string>$REPO_ROOT/data/logs/api.log</string>
</dict>
</plist>
PLIST

  # Snapshot through the afternoon and evening to capture line movement, then grade
  # the previous day in the morning once results have settled.
  cat > "$agents/$JOBS_LABEL.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$JOBS_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$REPO_ROOT/ops/run-jobs.sh</string><string>both</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO_ROOT</string>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Hour</key><integer>9</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>13</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>16</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>18</integer><key>Minute</key><integer>30</integer></dict>
  </array>
  <key>StandardOutPath</key><string>$REPO_ROOT/data/logs/jobs.log</string>
  <key>StandardErrorPath</key><string>$REPO_ROOT/data/logs/jobs.log</string>
</dict>
</plist>
PLIST

  launchctl unload "$agents/$API_LABEL.plist" 2>/dev/null
  launchctl unload "$agents/$JOBS_LABEL.plist" 2>/dev/null
  launchctl load "$agents/$API_LABEL.plist" || die "could not load $API_LABEL"
  launchctl load "$agents/$JOBS_LABEL.plist" || die "could not load $JOBS_LABEL"
  echo "Installed. The API starts on login and is kept running."
  echo "Open http://127.0.0.1:8000  |  logs: $REPO_ROOT/data/logs/"
}

uninstall_macos() {
  local agents="$HOME/Library/LaunchAgents"
  launchctl unload "$agents/$API_LABEL.plist" 2>/dev/null
  launchctl unload "$agents/$JOBS_LABEL.plist" 2>/dev/null
  rm -f "$agents/$API_LABEL.plist" "$agents/$JOBS_LABEL.plist"
  echo "Removed."
}

status_macos() {
  launchctl list | grep -E "statsevsolver" || echo "Not running."
}

# --------------------------------------------------------------------- Linux
install_linux() {
  local unit_dir="$HOME/.config/systemd/user"
  mkdir -p "$unit_dir" "$REPO_ROOT/data/logs"

  cat > "$unit_dir/stats-ev-solver.service" <<UNIT
[Unit]
Description=Stats EV Solver API
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$REPO_ROOT
Environment=PYTHONPATH=$REPO_ROOT/backend
ExecStart=$PY -m app.cli serve
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
UNIT

  cat > "$unit_dir/stats-ev-solver-jobs.service" <<UNIT
[Unit]
Description=Stats EV Solver scheduled snapshot and grading

[Service]
Type=oneshot
WorkingDirectory=$REPO_ROOT
ExecStart=$REPO_ROOT/ops/run-jobs.sh both
UNIT

  cat > "$unit_dir/stats-ev-solver-jobs.timer" <<UNIT
[Unit]
Description=Run Stats EV Solver jobs through the day

[Timer]
OnCalendar=*-*-* 09,13,16:00:00
OnCalendar=*-*-* 18:30:00
Persistent=true

[Install]
WantedBy=timers.target
UNIT

  systemctl --user daemon-reload || die "systemctl --user unavailable"
  systemctl --user enable --now stats-ev-solver.service
  systemctl --user enable --now stats-ev-solver-jobs.timer
  echo "Installed. Open http://127.0.0.1:8000  |  logs: $REPO_ROOT/data/logs/"
  echo "If the API should keep running while you are logged out:"
  echo "  sudo loginctl enable-linger $USER"
}

uninstall_linux() {
  systemctl --user disable --now stats-ev-solver.service 2>/dev/null
  systemctl --user disable --now stats-ev-solver-jobs.timer 2>/dev/null
  rm -f "$HOME/.config/systemd/user/stats-ev-solver"*.service \
        "$HOME/.config/systemd/user/stats-ev-solver"*.timer
  systemctl --user daemon-reload 2>/dev/null
  echo "Removed."
}

status_linux() {
  systemctl --user status stats-ev-solver.service --no-pager 2>/dev/null | head -5
  systemctl --user list-timers stats-ev-solver-jobs.timer --no-pager 2>/dev/null | head -3
}

case "$OS:$ACTION" in
  Darwin:install)   install_macos ;;
  Darwin:uninstall) uninstall_macos ;;
  Darwin:status)    status_macos ;;
  Linux:install)    install_linux ;;
  Linux:uninstall)  uninstall_linux ;;
  Linux:status)     status_linux ;;
  *:install|*:uninstall|*:status)
    die "unsupported OS '$OS' -- run the API with 'make api' and schedule ops/run-jobs.sh yourself" ;;
  *) die "usage: install-service.sh install|uninstall|status" ;;
esac
