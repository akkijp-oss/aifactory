#!/bin/bash
# Dedicated X11 session. Its pixels and sockets are never exposed over TCP.
set -euo pipefail
umask 077
export DISPLAY=:99
export XDG_SESSION_TYPE=x11
unset WAYLAND_DISPLAY
export XAUTHORITY="$HOME/.local/state/aifactory-desktop/Xauthority"
mkdir -p "$(dirname "$XAUTHORITY")"
python3 - <<'PY'
import os,secrets,subprocess,pathlib
p=pathlib.Path(os.environ['XAUTHORITY']);p.touch(mode=0o600,exist_ok=True)
subprocess.run(['xauth','-f',str(p)],input=f'add :99 . {secrets.token_hex(16)}\n',text=True,check=True,stdout=subprocess.DEVNULL)
PY
Xvfb "$DISPLAY" -screen 0 1280x960x24 -nolisten tcp -auth "$XAUTHORITY" &
display_pid=$!
trap 'kill "$display_pid" 2>/dev/null || true' EXIT
for i in $(seq 1 50); do
  if xdpyinfo >/dev/null 2>&1; then break; fi
  kill -0 "$display_pid" || exit 1
  sleep .1
done
xdpyinfo >/dev/null
# Keep the virtual desktop usable without a display manager or application menu.
config_dir="$HOME/.local/state/aifactory-desktop"
cat > "$config_dir/rc.xml" <<'XML'
<?xml version="1.0" encoding="UTF-8"?>
<openbox_config xmlns="http://openbox.org/3.4/rc">
  <focus><focusNew>yes</focusNew></focus>
  <keyboard>
    <keybind key="A-F2"><action name="Execute"><command>gmrun</command></action></keybind>
    <keybind key="A-Tab"><action name="NextWindow"/></keybind>
    <keybind key="A-F4"><action name="Close"/></keybind>
  </keyboard>
</openbox_config>
XML
openbox --config-file "$config_dir/rc.xml" &
/usr/local/lib/aifactory-computer/aifactory-computer -mode serve
