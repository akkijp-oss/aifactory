#!/bin/bash
# Run as root on a dedicated Linux instance with systemd. No hypervisor required.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Run as root' >&2; exit 1; }
config_source=${1:?usage: install-linux-worker.sh CONFIG_JSON BINARY_DIRECTORY [--headless]}
binaries=${2:?binary directory required}
mode=${3:-}
[[ -z "$mode" || "$mode" == --headless ]] || { echo 'Unknown option' >&2; exit 1; }
command -v systemctl >/dev/null
systemd_version=$(systemctl --version | awk 'NR==1 {print $2}')
[[ "$systemd_version" =~ ^[0-9]+$ && "$systemd_version" -ge 254 ]] || { echo 'systemd 254 or newer is required' >&2; exit 1; }
# Keep platform installation paths fixed; the workflow validates the work root.
python3 - "$config_source" <<'PY'
import json,sys
c=json.load(open(sys.argv[1]))
expected={'work_root':'/var/lib/aifactory-worker/work','state_dir':'/var/lib/aifactory-worker/private/state',
          'task_user':'aifactory-task','token_file':'/etc/aifactory-worker/worker.token','ca_file':'/etc/aifactory-worker/server.crt'}
for k,v in expected.items():
 if c.get(k)!=v:raise SystemExit(f'{k} must be {v}')
PY
[[ ! -e /var/lib/aifactory-worker/private/state/guest-lease ]] || { echo 'Worker is leased; release it before updating' >&2; exit 1; }
[[ -x "$binaries/aifactory-worker" && -x "$binaries/aifactory-computer" ]] || { echo 'Both worker binaries are required' >&2; exit 1; }
if [[ "$mode" == --headless ]]; then
 for tool in Xvfb xauth xdpyinfo xdotool xclip openbox gmrun dbus-run-session; do command -v "$tool" >/dev/null; done
 python3 -c 'from PIL import ImageGrab'
fi
if ! id aifactory-task >/dev/null 2>&1; then useradd --create-home --shell /bin/bash aifactory-task; fi
[[ $(id -u aifactory-task) != 0 ]] || exit 1
if id -nG aifactory-task | tr ' ' '\n' | grep -Eq '^(sudo|wheel|docker|lxd|disk)$'; then
 echo 'Task user must not belong to privileged groups' >&2; exit 1
fi
install -d -m 755 /var/lib/aifactory-worker /var/lib/aifactory-worker/work /usr/local/lib/aifactory-computer
install -d -m 700 /etc/aifactory-worker /var/lib/aifactory-worker/private /var/lib/aifactory-worker/private/state
install -m 600 "$config_source" /etc/aifactory-worker/config.json
[[ -s /etc/aifactory-worker/worker.token && -s /etc/aifactory-worker/server.crt ]] || { echo 'Install worker.token and server.crt first' >&2; exit 1; }
chown root:root /etc/aifactory-worker/{config.json,worker.token,server.crt}
chmod 600 /etc/aifactory-worker/{config.json,worker.token,server.crt}
if systemctl cat aifactory-worker.service >/dev/null 2>&1; then systemctl stop aifactory-worker.service; fi
if [[ "$mode" == --headless ]] && systemctl cat aifactory-desktop.service >/dev/null 2>&1; then systemctl stop aifactory-desktop.service; fi
install -m 755 "$binaries/aifactory-worker" /usr/local/bin/aifactory-worker
install -m 755 "$binaries/aifactory-computer" /usr/local/lib/aifactory-computer/aifactory-computer
source_dir=$(cd "$(dirname "$0")/../computer" && pwd)
install -m 644 "$source_dir/linux.py" /usr/local/lib/aifactory-computer/linux.py
install -m 755 "$source_dir/headless-linux.sh" /usr/local/lib/aifactory-computer/headless-linux.sh
install -d -o root -g aifactory-task -m 750 /var/lib/aifactory-worker/desktop
if [[ ! -f /var/lib/aifactory-worker/desktop/agent.token ]]; then
 /usr/local/lib/aifactory-computer/aifactory-computer -mode init-token
fi
chown root:aifactory-task /var/lib/aifactory-worker/desktop/agent.token
chmod 640 /var/lib/aifactory-worker/desktop/agent.token
cat > /etc/systemd/system/aifactory-worker.service <<'UNIT'
[Unit]
Description=AIFactory standalone Linux pull worker
Wants=network-online.target
After=network-online.target
[Service]
ExecStart=/usr/local/bin/aifactory-worker -config /etc/aifactory-worker/config.json
Restart=on-failure
RestartSec=5
UMask=0077
[Install]
WantedBy=multi-user.target
UNIT
if [[ "$mode" == --headless ]]; then
 cat > /etc/systemd/system/aifactory-desktop.service <<'UNIT'
[Unit]
Description=AIFactory dedicated virtual X11 desktop
After=network.target
[Service]
User=aifactory-task
Group=aifactory-task
Environment=HOME=/home/aifactory-task
Environment=LANG=C.UTF-8
ExecStart=/usr/bin/dbus-run-session -- /usr/local/lib/aifactory-computer/headless-linux.sh
Restart=on-failure
RestartSec=5
KillMode=control-group
TimeoutStopSec=10
NoNewPrivileges=yes
UMask=0077
[Install]
WantedBy=multi-user.target
UNIT
fi
systemctl daemon-reload
systemctl enable --now aifactory-worker.service
if [[ "$mode" == --headless ]]; then systemctl enable --now aifactory-desktop.service; fi
