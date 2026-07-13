# User Guide

## Overview

GUI Remote Controll runs an HTTP/WebSocket server on the computer being controlled. A client
opens the server URL in a normal browser. The browser receives JPEG screen frames and sends
validated control messages back to the server.

The client does not install an extension, receive operating-system credentials, or open a
native remote-desktop handle. Runtime frames, key events, and clipboard text are not persisted
by the application.

## Before starting

- Install Python 3.10 or newer.
- Use a signed-in graphical desktop session.
- Decide which network interfaces should accept connections.
- Choose a PIN whenever the service is not strictly local.
- Complete the platform steps in [Platform support](platform-support.md).

For a trusted LAN, a typical command is:

```console
gui-remote-controll --pin 123456
```

For a local reverse proxy or SSH tunnel, restrict the listener:

```console
gui-remote-controll --host 127.0.0.1 --pin 123456
```

## Startup and elevation

The public CLI asks for administrator/root privileges once through `py-admin-launch` before
starting the web server. The relaunched process receives an internal marker, so it does not ask
again. Platform behavior is:

- Windows uses the UAC `runas` flow.
- Linux prefers `pkexec` and falls back to `sudo` while preserving desktop session variables.
- macOS uses an administrator prompt through `osascript` and can fall back to `sudo`.

Use `--no-elevate` for containers, service managers, development sessions, or environments
where elevation is managed externally. Elevation does not bypass the Windows secure desktop,
macOS privacy controls, or Wayland compositor policy.

## Connecting from a browser

The server prints a local URL after startup. When the bind address is `0.0.0.0` or `::`, the
printed URL uses `127.0.0.1` so it can be opened locally. Remote devices must use an address
that resolves to the server computer.

If a PIN is configured, the first page contains a PIN form. Successful authentication creates
an HTTP-only SameSite cookie. The cookie lasts up to seven days, but it is signed with a random
process-local secret and therefore becomes invalid whenever the server restarts.

Use **Sign out** to delete the browser authentication cookie.

## Browser interface

### Connection status

The header reports `Connecting`, `Connected`, `Disconnected`, `Server busy`, or
`Desktop unavailable`. A dropped connection is retried automatically with an increasing delay,
up to eight seconds between attempts.

The client sends an application-level ping every 15 seconds. Uvicorn also uses WebSocket ping
and timeout intervals of 20 seconds.

### Screen selector

The **Screen** menu lists every detected physical display and a combined virtual desktop:

- Index `0` is **All displays** and covers the bounding rectangle of every monitor.
- Index `1` and above identify physical displays in the order reported by the operating system.

The `--monitor` option selects the initial display. If that index is unavailable, the server
chooses the first physical display, then falls back to the combined desktop.

Changing the selector affects only that browser connection. Other clients may watch another
display at the same time.

### Fit and 1:1 modes

- **Fit** scales the whole remote frame into the available browser area while preserving its
  aspect ratio.
- **1:1** displays one remote pixel as one CSS pixel. The stage becomes scrollable when the
  remote display is larger than the browser viewport.

Input coordinates remain normalized in both modes, so scaling does not change where pointer
events are injected.

### Fullscreen

**Fullscreen** requests browser fullscreen for the remote desktop stage. Browser and operating
system policies still control whether the request is accepted. Exit fullscreen using the
browser's normal fullscreen shortcut.

### Status bar

The lower status bar shows the selected display name and pixel dimensions. It also reports
`Control enabled` or `View only`.

## Screen streaming

The server captures the selected display at up to `--fps` frames per second and encodes each
capture as JPEG using `--jpeg-quality`. Unchanged JPEG frames are not retransmitted, reducing
network usage while the desktop is idle.

Higher frame rates and JPEG quality consume more CPU and network bandwidth. Start with the
defaults (`10` FPS and quality `80`) and increase them only when the network and server have
enough capacity.

On Linux/X11, the system cursor is merged into captured frames by default when the capture
backend supports it. Use `--no-cursor` to disable that behavior. Windows and macOS clients see
their browser pointer while controlling the desktop, but the capture backend does not merge a
separate system cursor image on those platforms.

## Mouse and touch control

Pointer events are attached to the displayed frame:

- Movement sets the absolute operating-system pointer position.
- Left, middle, and right button down/up events are supported.
- Pointer events also cover browser touch input.
- The browser suppresses image dragging and its context menu over the frame.
- Movement is limited to one message per browser animation frame.

The server tracks buttons held by each WebSocket. When a connection closes or the browser loses
focus, held buttons are released to avoid a stuck drag.

## Wheel control

Browser wheel deltas may be reported in pixels, lines, or pages. The web client normalizes them
before transmission. The server accumulates fractional horizontal and vertical deltas and emits
only whole native scroll steps, preserving small trackpad movements without passing unsupported
floating-point values to the operating system.

## Keyboard, shortcuts, and text input

The browser uses two input paths:

- Non-printable keys and shortcuts use explicit key down/up events.
- Ordinary text uses a hidden text input and a `text` message.

This separation supports normal printable text and composition input methods without typing the
same text twice. Composition text is sent after composition ends. A local Ctrl/Cmd+V paste is
captured as text and typed remotely instead of combining a local paste with the remote
clipboard's contents.

Supported named keys include Alt, AltGraph, arrows, Backspace, Caps Lock, Control, Delete, End,
Enter, Escape, F1-F12, Home, Insert, Meta/OS, Num Lock, Page Up/Down, Pause, Print Screen,
Scroll Lock, Shift, and Tab. Single-character shortcut keys are also supported.

Some browser or operating-system shortcuts cannot be intercepted. Examples commonly include
the secure attention sequence, switching out of the browser, browser-reserved shortcuts, and
platform security dialogs.

Held keys are tracked per connection and released when the connection closes or the browser
loses focus. Repeated browser keydown messages are ignored because the operating system already
handles repetition for a held key.

## Clipboard synchronization

Clipboard synchronization handles plain text only.

### Automatic synchronization

The **Auto sync** switch maintains a bidirectional clipboard bridge while the browser tab is
visible and focused. It is off by default and the preference is stored in browser local storage.

When the remote session opens, the page immediately requests browser clipboard read access.
Some browsers only allow the prompt after a user gesture; enabling **Auto sync** retries the
request from the switch interaction. The page does not perform a no-op write to request write
access because that could destroy non-text formats in an existing clipboard item. Write access
is exercised only when a remote text change actually needs to be applied.

The first successful enable reads both clipboards as independent baselines and does not overwrite
either side. After that:

- a client clipboard change replaces the server clipboard;
- a server clipboard change replaces the client clipboard;
- a client change detected while the initial server baseline is still arriving wins that race;
- values written by synchronization are recorded before the next check, preventing feedback
  loops between the two sides;
- a standard `clipboardchange` event is used when the browser provides it, with polling as the
  compatibility fallback;
- polling pauses while the page is hidden or unfocused and resumes when focus returns;
- the client sends the last server-content digest, so unchanged polls do not retransmit the
  complete clipboard text.

Async Clipboard access is restricted to secure browser contexts. Automatic synchronization is
therefore available over HTTPS and on trusted localhost origins. A plain LAN URL such as
`http://192.168.1.10:8000` cannot receive this permission in conforming browsers; configure TLS
or an HTTPS reverse proxy for automatic synchronization across LAN devices.

Clipboard permission can be revoked in browser site settings. The switch turns off and reports
the failed permission when a later read or write is denied.

### Manual clipboard controls

1. Open **Clipboard**.
2. Choose **Read remote** to load text from the server computer.
3. Edit the text if needed.
4. Choose **Write remote** to replace the server clipboard.

**Paste local** and **Copy local** access the browser-side clipboard. The manual textarea remains
usable when the browser Clipboard API is unavailable, but the user must paste or copy through
the browser/operating-system UI.

Use `--no-clipboard` to disable all server clipboard reads and writes. Linux requires an
available `pyperclip` backend, commonly `xclip` or `xsel` on X11. Clipboard messages are limited
to 1,000,000 characters by the current server settings.

## View-only mode

Start with `--view-only` to stream frames while ignoring pointer, wheel, keyboard, and text
messages. Display selection, pings, and clipboard synchronization remain available unless
clipboard access is separately disabled with `--no-clipboard`.

For a strictly observational deployment, combine both options:

```console
gui-remote-controll --pin 123456 --view-only --no-clipboard
```

## Multiple clients

The default limit is four simultaneous WebSockets. Set `--max-clients` from 1 to 32. A client
above the limit is rejected with WebSocket close code `4429` and the UI reports `Server busy`.

Each browser connection selects its own display and streams its own frames. Input is applied to
the same operating-system desktop, so multiple controlling clients can affect one another. Use
view-only mode or a low client limit when exclusive control is required.

## Network security

### PIN

`--pin` gates the main page, status API, and WebSocket. It is strongly recommended for every
listener reachable by another device. Use a long, nontrivial PIN when the service is exposed to
more than a private local environment.

### TLS

Provide both files together:

```console
gui-remote-controll --pin 123456 --tls-certfile server.crt --tls-keyfile server.key
```

TLS enables secure authentication cookies and browser clipboard features on non-local addresses.

### Reverse proxies and tunnels

Bind to loopback when a reverse proxy, VPN, or SSH tunnel is the only intended entry point:

```console
gui-remote-controll --host 127.0.0.1 --pin 123456
```

If a reverse proxy serves the control page from a different browser origin, repeat
`--trusted-origin` for each exact allowed origin, including scheme and port where applicable.

## HTTP endpoints

- `GET /` serves the control UI after PIN authentication.
- `GET /auth` and `POST /auth` implement PIN login.
- `POST /logout` clears the authentication cookie.
- `GET /api/status` returns version, platform, mode, clipboard availability, and client counts.
- `GET /healthz` returns a minimal unauthenticated health response.
- `GET /static/{filename}` serves a fixed allowlist of packaged UI files.
- `WS /ws` carries metadata, JPEG frames, control events, and clipboard messages.

For message-level details, see [Architecture and algorithms](architecture-and-algorithms.md).

## Current limitations

- No system audio streaming.
- No file transfer or remote file picker.
- No capture of the Windows secure desktop or sign-in screen.
- No native Wayland compositor capture or global input injection.
- No bypass for macOS Screen Recording or Accessibility privacy controls.
- Plain-text clipboard only.
- No persistence of sessions across a server restart.
- JPEG frame transport is designed for practical remote operation, not lossless video delivery.
