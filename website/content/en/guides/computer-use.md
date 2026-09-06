# Mac, Windows, and Linux computer use

Dedicated worker environments support screenshots, clicks, pointer movement, Unicode typing, key combinations, and scrolling. Actions use the existing authenticated pull queue and exclusive worker leases. Requests cannot select a different host or VM.

## Use from a ticket

Install the desktop helper on the worker VM, then set these project options:

```yaml
backend: windows-pull # macos-pull for Mac
worker: windows-worker-01
computer_use: true
```

`ticket_run` / `kb run` supplies each agent with the VM-local `computer` MCP tool. This option requires a Mac, Windows, or standalone Linux pull backend and an MCP-capable Claude CLI. The runner uses `--strict-mcp-config` with the generated configuration for this run.

Describe the application, exact input, expected display, and completion conditions in the ticket. For example: open Notepad, type a specified Japanese sentence, inspect the screenshot, and report whether the displayed text matches.

Screenshots update `screenshot-latest.png` in the run's work directory. The latest image is collected with the other artifacts after SHA-256 verification. Copy images to separate names directly under the work directory if more than one is needed; images placed in a subdirectory are not collected. All artifacts together must fit within 4 MiB. Anyone who can read the artifacts can also see information visible on the captured desktop.

## Use through AIFactory MCP

Reconnect AIFactory MCP after deployment to refresh its tool list.

1. Call `computer_open` with `worker` to acquire a lease and prepare the VM. Keep the returned `session`.
2. Call `computer_action` with that session. Start with a screenshot and use coordinates from the returned image.
3. Call `computer_close` to release the guest and its lease after successful cleanup.

```json
{"session":"desktop-...","action":"screenshot"}
{"session":"desktop-...","action":"click","x":300,"y":200}
{"session":"desktop-...","action":"type","text":"日本語入力テスト"}
{"session":"desktop-...","action":"key","keys":["CTRL","A"]}
{"session":"desktop-...","action":"scroll","amount":-3}
```

PNG screenshots have a maximum width of 1024 pixels on Windows and Linux and 2560 pixels on Mac; a guest display at or below that width is returned at its real resolution. The helper maps image coordinates to the physical desktop; the origin is the image's top-left corner. Windows captures the virtual desktop, while Mac captures the main display. `move` also accepts `x` and `y`. Click options are `button: left|right` and `count: 1|2`. Typing supports up to 8192 UTF-8 bytes. On Mac it replaces the guest clipboard and pastes the string; fields that prohibit pasting are unsupported. Key combinations contain up to four keys; key names are the same on all three operating systems and are case-insensitive. Scroll amounts range from -20 to 20 excluding zero; positive means up.

Direct-session screenshots are stored under `$AIFACTORY_WORKSPACE/computer/<session>/` on the control plane. Responses include the image, path, and hash. `actions.jsonl` records operation IDs and action types, without copying typed text into those audit rows. Screenshots and command output can still contain that text. Storage is not automatically pruned.

The CLI uses the same session handling:

```bash
workers/bin/computer open mac-worker-01
printf '%s' '{"action":"screenshot"}' | workers/bin/computer action desktop-...
workers/bin/computer close desktop-...
```

## Key names

`key` accepts the same 79 names on all three operating systems. Names are case-insensitive.

| Group | Names |
| --- | --- |
| Modifiers | `CTRL` `ALT` `SHIFT` `WIN` `CMD` |
| Special keys | `ENTER` `TAB` `ESC` `SPACE` `BACKSPACE` `DELETE` `LEFT` `RIGHT` `UP` `DOWN` `HOME` `END` `PAGEUP` `PAGEDOWN` |
| Function keys | `F1`-`F12` |
| Letters and digits | `A`-`Z` / `0`-`9` |
| Symbols | `=` `-` `+` `,` `.` `/` `;` `'` `[` `]` `\` `` ` `` |

- `WIN` and `CMD` both mean the local meta key (Windows key on Windows, Command on Mac, Super on Linux). Sending `CMD` does not fail on Windows.
- On Linux `CMD`/`WIN` is Super, which most applications do not treat as suppressing text input (`CMD`+`SHIFT`+`=` types a `+` instead of acting as a shortcut). Send Linux shortcuts with `CTRL` or `ALT`.
- `+` has no physical key, so it is sent as the `=` key with `SHIFT`. Passing `SHIFT` and `=` explicitly does the same thing.
- Symbol positions assume a **US layout**. On a guest with a JIS or other layout, symbol keys produce different characters.
- An unknown name returns an error that lists every accepted name (`unsupported key: <name>; supported: ...`). Pick a name from that list instead of guessing again.

```json
{"session":"desktop-...","action":"key","keys":["CMD","SHIFT","="]}
{"session":"desktop-...","action":"key","keys":["CTRL","-"]}
{"session":"desktop-...","action":"key","keys":["F5"]}
{"session":"desktop-...","action":"key","keys":["ESC"]}
```

## When a key does not work

A key press only does something when the target window has focus and the application actually binds that shortcut; a successful `key` action is not proof that the shortcut took effect. If repeating `key` does not change the screen, switch approach instead of retrying.

1. Take a `screenshot` and check the current state. If another window has focus, click the target window and send the key again.
2. Click the menu. Zoom, save, and find are usually available as menu items, and clicking lets you confirm the result on screen.
3. Use `type` when you only need to enter characters. It inserts Unicode directly, so it is unaffected by symbols or the keyboard layout.
4. Change the application's own shortcut settings to a combination that is available.
5. Keys outside the list (`INSERT`, the numeric keypad, media keys, `F13` and above) cannot be sent. Use steps 1-4 instead, or change the procedure so the key is not needed.

## Setting a display wide enough for 1400 pixels

The dedicated Mac guest boots at the base image's 1024x768, so a 1400-pixel-wide layout cannot be captured by default. Adding `display` to the project definition or the worker configuration makes `guest-prepare` run `tart set <guest> --display <width>x<height>` on the stopped clone before starting it.

```yaml
# $AIFACTORY_WORKSPACE/projects/<pj>/project.yml
backend: macos-pull
worker: mac-worker-01
display:
  width: 1600
  height: 1000
```

```json
{
  "display": {"width": 1600, "height": 1000}
}
```

The JSON above goes into the worker configuration `~/.config/aifactory-worker/config.json` and applies to every project that uses that worker.

- Precedence is project definition, then worker configuration, then nothing. A project without `display` keeps running at 1024x768.
- Allowed sizes are 800-2560 wide and 600-2560 high, and both `width` and `height` are required. Values outside that range fail project validation, and a worker configured outside it refuses to start.
- `scale` is unsupported. It waits for a hardware check of the matching `tart set` option and a separate ticket ([ADR-0057](https://github.com/akkijp-oss/aifactory/blob/main/docs/adr/0057-guest-display-resolution.md)).
- CPU and memory stay fixed at 4 CPUs and 8 GiB. Resolution does not change the allocation.
- Until the guest's `desktop-native` is replaced with the 2560-pixel build and the base VM is rebuilt, screenshots are still scaled down to 1024 pixels wide even on a larger display. Repeat the build and re-verification in "Mac setup" below.
- Larger images do not inflate step logs. Image base64 on stream-json is replaced with `[image N bytes]` before it is recorded, so the 16 MiB per-operation log limit stays out of reach regardless of resolution. The MCP `computer_action` stores images as files and records only their hashes in `actions.jsonl`.
- Windows and Linux workers have no such setting; their screenshots stay at a maximum width of 1024 pixels.

## Windows setup

First install the [Windows worker](windows-worker.md). Its ordinary task user must have a logged-in, unlocked desktop. A separate `AIFactoryDesktop` scheduled task runs at that user's logon, outside service Session 0. This installer does not configure automatic logon.

Build in `workers/`. For cross-compilation, set `GOOS=windows GOARCH=amd64 CGO_ENABLED=0`.

```powershell
go build -trimpath -o aifactory-computer.exe ./cmd/aifactory-computer
go build -trimpath -ldflags=-H=windowsgui -o aifactory-desktop.exe ./cmd/aifactory-computer
```

Copy both executables and `workers/computer/windows.ps1` into `C:\ProgramData\AIFactoryWorker\bin\`. Run `workers/computer/install-windows.ps1` as administrator. Although the installer accepts a custom Root, the current workflow and control-plane clients use the default path above.

The desktop agent listens only on `127.0.0.1:31191`, authenticates with a separate random token, and grants the task user read-only access to that token. No desktop port is exposed externally. Actions run with ordinary user privileges; locked screens and protected UAC desktops are unsupported.

Windows VM state persists. Release clears the run workspace, but does not reset applications, desktop contents, or user settings. Tickets should clean up their own input and applications. Tests requiring a pristine OS need a separate VM restore procedure.

## Mac setup

Use a dedicated [Tart Mac guest](macos-worker.md), macOS 14 or newer, with a logged-in desktop. Build with a Mac SDK:

```bash
cd workers
CGO_ENABLED=0 GOOS=darwin GOARCH=arm64 go build -trimpath -o /tmp/aifactory-computer ./cmd/aifactory-computer
swiftc -parse-as-library -O computer/macos.swift -o /tmp/desktop-native
```

Transfer both binaries to the guest and run `workers/computer/install-macos.sh <binary-directory>` as the logged-in automation user. It installs into `$HOME/.local/lib/aifactory-computer/`. Workflow integration expects the parent of `app_dir` to be this HOME, for example `/Users/admin/app`. Use `tart exec -i` when forwarding stdin.

Grant Screen Recording and Accessibility through the guest's system settings or permission dialogs. The responsible application may appear as `tart-guest-agent`. Test capture and input, shut down the guest, clone it into a new base VM, and update the worker's `base_vm`. Verify permissions again in a fresh clone. Keep the previous base for recovery.

The Cirrus Labs image used for acceptance had SIP disabled already. Setup did not disable SIP or edit the TCC database directly. Equivalent behavior on an image with standard SIP enabled requires separate verification.

## Failures and limits

Missing permissions, a stopped desktop agent, logout, and a locked desktop are errors. Do not automatically repeat input whose completion is uncertain. Inspect the session, operation history, and guest before recovery. A worker leased by another ticket cannot also be opened as a direct desktop session.

This is screenshot-based interaction. Video streaming, audio interaction, and dragging are unsupported.

## Hardware acceptance

On 2026-09-07, direct AIFactory MCP sessions were used to click, type Japanese text, capture the dedicated Windows VM, and release its lease. A `computer_use: true` research ticket then entered 31 characters in Notepad; a separate judgment step read the PNG and confirmed the match. The latest PNG (about 794 KiB), report, and summary were collected after hash verification, and the lease was released.

Scroll actions returned successfully, but the one-line Windows document did not provide visible scrolling evidence. Japanese `type` uses Unicode input; this test did not exercise IME candidate conversion.

On Mac, a fresh guest cloned from the configured base was used to click and type Japanese text in TextEdit through AIFactory MCP. Capture and input worked without additional permission prompts.

The Mac research ticket also collected its Japanese-input PNG, report, and judgment, then released the lease. That test revealed character loss with long synthetic-key input. After changing Mac typing to paste, an AIFactory MCP test entered 41 lines / 942 characters with Japanese, emoji, and newlines. Selecting and copying the TextEdit contents reproduced the original text exactly. The clipboard was cleared before copying to avoid reading back the input clipboard instead of the document.

Symbol, function, and special key support (ticket 344) is unverified on real Mac and Windows hardware. What is verified: an automated test that the key-name tables agree across the three operating systems, plus the swiftc build and the PowerShell syntax check in CI. Only on Linux is a symbol key actually sent to a text field under Xvfb and compared against the resulting document.

Linux uses the same MCP interface. See [standalone Linux setup and limits](linux-worker.md).
