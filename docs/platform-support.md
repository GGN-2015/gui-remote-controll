# Platform Support

## Support matrix

| Platform | Screen capture | Input injection/monitoring | IME control | Clipboard | Required session |
| --- | --- | --- | --- | --- | --- |
| Windows 10+ | Supported | Supported | Foreground IMM32 context | Supported | Signed-in interactive desktop |
| Linux/X11 | Supported | Supported | Fcitx 4/5 or IBus | Supported with a `pyperclip` backend | Signed-in X11 desktop |
| Linux/native Wayland | Not supported for the full desktop | Not supported globally | Environment-dependent | Environment-dependent | Use an X11 session instead |
| macOS | Supported after permission grant | Supported after Accessibility grant | Text Input Source API | Supported | Signed-in Aqua session |

The application is a user-session remote desktop server. Administrator/root privileges do not
turn it into a pre-login, lock-screen, secure-desktop, or compositor-level service.

## Windows

### Requirements

- Windows 10 or newer.
- A signed-in interactive user session.
- Python installed for the user or system.
- UAC access when the default elevation flow is used.

### DPI and multiple displays

The process requests per-monitor DPI awareness before loading native input libraries. MSS is
also imported before `pynput`. This keeps physical capture pixels and pointer coordinates in the
same coordinate space on monitors with different Windows scaling values.

Monitor positions may be negative. For example, a display to the left of the primary display
can begin at `x = -1920`. The normalized coordinate algorithm preserves that offset.

### Physical input and IME

Low-level Windows mouse and keyboard hooks distinguish physical events from events injected by
the server. Physical activity temporarily blocks remote input for every connected client.

The IME button targets the foreground window through IMM32. When enabling, it restores the most
recent or first loaded IME input layout if the application is currently using direct input. It
then updates both the input context and the thread's default IME window. If Windows still reports
the opposite state, the controller uses the installed language's IME system toggle and verifies
the result again.

Modern Text Services Framework-only applications, elevated windows above the server's integrity
level, console windows, and custom controls may expose no compatible input context or may reject
an input-language request. In those cases the button is unavailable or reports the failed change.
The feature does not stop the Windows input service.

### Security desktop limitation

Windows switches UAC consent and sign-in UI to a secure desktop. A normal user-session capture
process cannot read or control it, even when the process itself is elevated. The remote stream
may freeze, blank, or continue showing the last ordinary desktop frame while the secure desktop
is active.

### Troubleshooting

- **Desktop access failed:** confirm the server was started inside the signed-in user's session,
  not from a noninteractive Windows service.
- **Pointer offset:** stop programs that changed DPI awareness before importing this package,
  then restart the server directly through its CLI.
- **Firewall prompt:** allow only the network profiles that should reach the server. Bind to
  `127.0.0.1` when LAN access is unnecessary.

## Linux with X11

### Compatible desktop environments

The controlling factor is the display protocol, not the desktop brand. The following desktop
environments are supported when the login session is X11:

- GNOME on Xorg;
- KDE Plasma (X11 session);
- Xfce;
- Cinnamon on X11;
- MATE;
- LXQt/LXDE;
- other conventional X11 window managers.

Check the current session:

```console
echo "$XDG_SESSION_TYPE"
echo "$DISPLAY"
```

The first value should normally be `x11`, and `DISPLAY` should be nonempty.

### Elevation and desktop variables

The Linux `py-admin-launch` flow prefers `pkexec` and falls back to `sudo`. It explicitly carries
desktop variables such as `DISPLAY`, `XAUTHORITY`, `WAYLAND_DISPLAY`, `XDG_RUNTIME_DIR`, and
`DBUS_SESSION_BUS_ADDRESS` so the elevated process can still reach the user's graphical session.

The X server must also accept the credentials represented by `XAUTHORITY`. Start the server from
a terminal inside the target desktop rather than a remote root shell with no session context.

### Clipboard backends

`pyperclip` commonly uses `xclip` or `xsel`. Install one with the operating system package
manager when clipboard operations report that no mechanism is available. Typical package names
are `xclip` and `xsel`.

Clipboard support can be disabled without affecting screen or input control:

```console
gui-remote-controll --pin 123456 --no-clipboard
```

### Cursor capture

MSS can merge the X11 system cursor into captured frames. This is enabled by default and may be
disabled with `--no-cursor`. The capture backend can turn it off automatically when the required
X11 cursor facilities are unavailable.

### Physical input priority

The X11 `pynput` listeners monitor global mouse and keyboard events and distinguish XTest events
injected by the controller. If the X server denies listener access, screen streaming can remain
available but the client is placed in **Control access restricted** because physical-input
priority cannot be guaranteed.

### Input method control

The server detects input method frameworks in this order:

1. `fcitx5-remote`;
2. `fcitx-remote`;
3. `ibus`.

Fcitx uses its explicit inactive/active commands. For IBus, the server records the current
non-XKB engine, selects an enabled `xkb:*` engine for direct input, and restores the recorded
engine when IME is turned back on. If no prior engine is recorded, it selects the first enabled
non-XKB engine reported by `ibus list-engine`.

The command and its desktop-session D-Bus connection must be available to the server process.
This is another reason to launch from the signed-in X11 session and preserve
`DBUS_SESSION_BUS_ADDRESS` during elevation.

### Display diagnostics

- **No X11 DISPLAY:** start from a terminal emulator inside the desktop session and verify
  `DISPLAY`.
- **Permission denied:** verify `XAUTHORITY` points to the signed-in user's X authority file.
- **No physical display detected:** ensure the X server exposes at least one active screen.
- **Clipboard failure:** install or configure a supported `pyperclip` backend.
- **IME unavailable:** confirm Fcitx or IBus is running and its remote-control command is in
  `PATH` for the elevated process.

## Wayland

Native Wayland intentionally does not expose a universal API for arbitrary global capture and
input injection. Root privileges do not override compositor protocol policy.

If both `WAYLAND_DISPLAY` and `DISPLAY` are present, the process may reach XWayland, but XWayland
does not guarantee capture of native Wayland windows or the complete compositor output. If only
`WAYLAND_DISPLAY` is present, the server rejects desktop initialization with a clear diagnostic.

For full desktop control, choose an X11/Xorg session at the display manager before starting GUI
Remote Controll. A future Wayland implementation would require desktop portals and
compositor-specific remote-desktop protocols rather than the current MSS/pynput backend.

## macOS

### Required permissions

macOS privacy controls are independent of Unix root. Grant permissions to the program that
launches Python, which may be Terminal, iTerm, another terminal application, an IDE, or the
Python executable itself:

1. Open **System Settings**.
2. Open **Privacy & Security**.
3. Enable **Screen Recording** for screen capture.
4. Enable **Accessibility** for keyboard and pointer control.
5. Quit and restart the terminal/application and the server.

The administrator prompt cannot approve these TCC permissions automatically.

### Physical input and IME

Accessibility permission is also required for global physical-input monitoring. `pynput` tags
the server's generated Quartz events, allowing the priority controller to ignore them while
reacting to physical mouse and keyboard activity.

IME off selects an enabled, selectable, ASCII-capable Text Input Source. The current non-ASCII
source is retained in memory and restored when IME is turned on. If the server starts while a
direct-input source is active, turning IME on selects the first enabled, selectable non-ASCII
source. This changes the current input source but does not disable macOS input services.

### Troubleshooting

- **Black or empty frames:** recheck Screen Recording and restart the launching application.
- **Frames work but input does not:** enable Accessibility and restart.
- **Permission remains attached to another Python:** remove stale entries and add the actual
  terminal or interpreter used by the installed CLI.
- **Server started through a background daemon:** run it from the signed-in Aqua session or
  explicitly grant the daemon's executable the required permissions.

## Networking on every platform

### Local-only

```console
gui-remote-controll --host 127.0.0.1 --pin 123456
```

### LAN

The default `0.0.0.0` listener accepts IPv4 connections on available interfaces. Use the server
computer's LAN address from the client and allow the selected TCP port through the host firewall.

### IPv6

Bind `--host ::` where the operating system and network support IPv6. The printed local URL uses
an IPv6 bracket form when necessary.

### Internet access

Do not expose the plain HTTP listener directly. Use a VPN, SSH tunnel, authenticated reverse
proxy, or direct TLS with both certificate and key files. A PIN authenticates a browser but does
not encrypt frames, key events, or clipboard text.
