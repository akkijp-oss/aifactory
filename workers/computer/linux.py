#!/usr/bin/env python3
"""Bounded X11 desktop actions. Run only in the dedicated desktop session."""
import base64
import io
import json
import os
import subprocess
import sys
import time


def run(*args, **kwargs):
    return subprocess.run(args, check=True, timeout=10, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, **kwargs)


def native(request):
    if not os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY') or os.environ.get('XDG_SESSION_TYPE') == 'wayland':
        raise ValueError('X11 required; native Wayland is unsupported')
    action = request['action']
    if action in ('screenshot', 'click', 'move'):
        from PIL import ImageGrab
        desktop = ImageGrab.grab(xdisplay=os.environ['DISPLAY'])
        physical_width, physical_height = desktop.size
        width = min(1024, physical_width)
        height = max(1, round(physical_height * width / physical_width))
        if action == 'screenshot':
            output = io.BytesIO()
            desktop.resize((width, height)).save(output, 'PNG')
            return dict(ok=True, image=base64.b64encode(output.getvalue()).decode(),
                        mimeType='image/png', width=width, height=height)
        x, y = request['x'], request['y']
        if type(x) is not int or type(y) is not int or not 0 <= x < width or not 0 <= y < height:
            raise ValueError('coordinates outside screenshot')
        # --sync waits for a movement event even when the pointer is already there.
        run('xdotool', 'mousemove', str(round(x * physical_width / width)),
            str(round(y * physical_height / height)))
        if action == 'click':
            button = request.get('button', 'left')
            count = request.get('count', 1)
            if button not in ('left', 'right') or count not in (1, 2):
                raise ValueError('invalid click')
            run('xdotool', 'click', '--repeat', str(count), '--delay', '60', '1' if button == 'left' else '3')
    elif action == 'type':
        text = request.get('text', '')
        if not isinstance(text, str) or len(text.encode()) > 8192:
            raise ValueError('text exceeds 8192 UTF-8 bytes')
        if text:
            # xclip keeps selection ownership in a child; never inherit captured pipes.
            subprocess.run(['xclip', '-selection', 'clipboard', '-in'], input=text.encode(),
                           check=True, timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            run('xdotool', 'key', '--clearmodifiers', 'ctrl+v')
            time.sleep(.2)
    elif action == 'key':
        mapping = dict(CTRL='ctrl', ALT='alt', SHIFT='shift', WIN='super', ENTER='Return',
                       TAB='Tab', ESC='Escape', SPACE='space', BACKSPACE='BackSpace',
                       DELETE='Delete', LEFT='Left', RIGHT='Right', UP='Up', DOWN='Down',
                       HOME='Home', END='End', PAGEUP='Prior', PAGEDOWN='Next')
        mapping.update({k: k.lower() for k in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'})
        mapping.update({f'F{i}': f'F{i}' for i in range(1, 13)})
        keys = request.get('keys', [])
        if not isinstance(keys, list) or not 1 <= len(keys) <= 4 or any(not isinstance(k, str) or k.upper() not in mapping for k in keys):
            raise ValueError('unsupported key')
        run('xdotool', 'key', '--clearmodifiers', '+'.join(mapping[k.upper()] for k in keys))
    elif action == 'scroll':
        amount = request.get('amount')
        if type(amount) is not int or amount == 0 or not -20 <= amount <= 20:
            raise ValueError('scroll amount must be -20..20 excluding zero')
        run('xdotool', 'click', '--repeat', str(abs(amount)), '--delay', '30', '4' if amount > 0 else '5')
    else:
        raise ValueError('unsupported action')
    return dict(ok=True)


if __name__ == '__main__':
    try:
        request = json.loads(sys.stdin.buffer.read(32769))
        print(json.dumps(native(request)))
    except Exception as error:
        print(json.dumps(dict(ok=False, error=str(error))))
        sys.exit(1)
