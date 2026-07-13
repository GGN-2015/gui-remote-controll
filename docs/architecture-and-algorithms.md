# Architecture and Algorithms

## System model

GUI Remote Controll is a bitmap remote-control system. It does not mirror a native window tree
or expose remote DOM elements. The server captures desktop pixels, encodes them as JPEG, and
injects validated browser input into the signed-in operating-system session.

```mermaid
flowchart LR
    Browser["Browser UI"] -->|"JSON control messages"| WS["FastAPI WebSocket"]
    WS --> Validator["Protocol validator"]
    Validator --> Desktop["Desktop backend"]
    Desktop --> OS["Signed-in desktop"]
    OS --> Capture["MSS capture"]
    Capture --> JPEG["Pillow JPEG encoder"]
    JPEG --> Dedup["BLAKE2b frame check"]
    Dedup -->|"changed binary frames"| WS
    WS --> Browser
```

## Package layout

| Module | Responsibility |
| --- | --- |
| `gui_remote_controll.__main__` | CLI parsing, validation, elevation, browser opening, and Uvicorn startup. |
| `gui_remote_controll.config` | Validated runtime settings and protocol size limits. |
| `gui_remote_controll.auth` | PIN comparison, signed Cookie tokens, and login attempt limiting. |
| `gui_remote_controll.protocol` | Strict normalization and validation of client JSON messages. |
| `gui_remote_controll.desktop` | Screen enumeration/capture, JPEG encoding, input injection/monitoring, clipboard, IME, DPI, and platform diagnostics. |
| `gui_remote_controll.ime` | Native Windows, Linux, and macOS input method status and switching. |
| `gui_remote_controll.app` | HTTP routes, security headers, backend lifecycle, connection gate, and WebSocket loops. |
| `gui_remote_controll.static` | Packaged browser UI with no CDN or runtime frontend dependency. |

## Process lifecycle

1. Parse arguments and validate the TLS file pair.
2. Unless disabled or already attempted, relaunch through `py-admin-launch`.
3. Convert parsed values to `Settings` and validate ranges.
4. Create the FastAPI application and Uvicorn server.
5. Serve the login UI, control UI, health endpoint, and static files without opening the desktop.
6. On the first accepted WebSocket, initialize the shared desktop backend in a worker thread.
7. Enumerate displays and start receive, capture, and control-state loops for the client.
8. Cancel the peer tasks and release held inputs when any loop finishes.

Lazy desktop initialization keeps CLI help, health checks, packaging, and PIN authentication
usable even when the process is temporarily outside a graphical session.

## Elevation algorithm

The public CLI implements a single-relaunch invariant:

```text
if no_elevate or elevation_attempted:
    start server
else:
    launch([python, -m, gui_remote_controll,
            --elevation-attempted, *original_arguments], wait=True)
    exit parent
```

The marker is inserted by the program rather than stored in process environment, so it survives
the platform launcher without requiring environment mutation support. Even when the first
process is already administrator/root, `py-admin-launch` directly launches the marked child and
the invariant remains at most one launch call.

## Authentication algorithm

### PIN comparison

PIN values are compared with `hmac.compare_digest` over UTF-8 bytes. This avoids ordinary
short-circuit string comparison behavior.

### Cookie token

At application creation, the server generates a random 32-byte secret. With a configured PIN:

```text
token = HMAC-SHA256(process_secret, pin_utf8)
```

The hexadecimal token is stored in `gui_remote_auth` with:

- `HttpOnly` enabled;
- `SameSite=Strict`;
- path `/`;
- a maximum age of seven days;
- `Secure` enabled when direct TLS is configured.

The secret is never persisted, so all authentication cookies become invalid after restart.
Without a configured PIN, request and WebSocket authentication checks allow access.

### Failed-login limiter

Failures are stored in an in-memory deque per client IP. Before each attempt, timestamps at or
before `now - 60 seconds` are discarded. A client with five remaining timestamps is rejected
until one expires. Successful authentication clears that client's deque.

This is a process-local rolling-window defense, not a replacement for firewall or proxy rate
limiting on an internet-facing deployment.

## HTTP security boundary

Every HTTP response receives:

- a Content Security Policy restricted to packaged same-origin resources plus WebSocket
  connections and blob/data images;
- `Cross-Origin-Opener-Policy: same-origin`;
- `Referrer-Policy: no-referrer`;
- `X-Content-Type-Options: nosniff`;
- `X-Frame-Options: DENY`.

The main and authentication pages use `Cache-Control: no-store`. Static file access uses a fixed
filename allowlist rather than arbitrary path resolution.

Authentication redirect targets must start with one `/`, must not start with `//`, and are
truncated to 2,048 characters.

## WebSocket admission

Admission checks run before the socket is accepted:

1. Validate the PIN Cookie, closing with `4401` on failure.
2. Validate Origin, closing with `4403` on failure.
3. Reserve a connection slot, closing with `4429` when full.
4. Accept the socket.
5. Initialize the desktop and close with `4500` if it is unavailable.

An absent Origin is allowed for non-browser clients. A browser Origin is accepted when its
network location matches the HTTP Host header or when the complete normalized origin appears in
the configured trusted-origin set.

## Connection and concurrency model

`BackendManager` creates one desktop backend per FastAPI application and protects creation with
an `asyncio.Lock`. Blocking desktop operations run through `asyncio.to_thread`, so capture,
clipboard, and native input calls do not block the event loop.

Each accepted client has:

- one `ClientState` with its display selection and held keys/buttons;
- one frame streaming task;
- one message receiving task;
- one control-state streaming task;
- one lock serializing JSON and binary WebSocket sends.

The first completed task cancels the others. Cleanup decrements the connection counter and asks
the backend to release all inputs still held by that client.

## Display model

MSS returns an ordered monitor collection:

- monitor `0` is the bounding rectangle of all monitors;
- monitor `N > 0` is a physical display.

A `Screen` contains `index`, `left`, `top`, `width`, `height`, and `name`. Negative `left` or
`top` values are valid when a monitor is positioned to the left or above the primary display.

On Windows, per-monitor DPI awareness is requested before importing input libraries. MSS is
imported before `pynput` to keep capture and input coordinate systems aligned.

## Capture and frame algorithm

The capture loop runs once per WebSocket:

1. Select the current monitor from the client's state.
2. Acquire BGRA pixels through a thread-local MSS instance.
3. Convert MSS RGB bytes to a Pillow RGB image.
4. Encode JPEG with the configured quality and chroma subsampling.
5. Compute `BLAKE2b(frame_bytes, digest_size=8)`.
6. Send display metadata when the selected monitor changes.
7. Send the JPEG as a binary WebSocket message only when its digest differs from the last sent
   frame.
8. Sleep for `max(0.001, 1/fps - capture_elapsed)`.

Hashing encoded bytes means the comparison reflects exactly what the client would receive. The
64-bit digest is used as a fast change detector rather than a security primitive. A theoretical
collision would skip one changed frame; a later differing frame would resume transmission.

Thread-local MSS instances avoid sharing a platform capture object across worker threads. The
instance remains associated with that worker thread for reuse.

## Pointer coordinate mapping

The browser measures the rendered image rectangle and converts a pointer event into normalized
coordinates:

```text
nx = clamp((client_x - image_left) / image_width,  0, 1)
ny = clamp((client_y - image_top)  / image_height, 0, 1)
```

The server converts them to absolute desktop coordinates:

```text
x = screen.left + round(nx * max(0, screen.width  - 1))
y = screen.top  + round(ny * max(0, screen.height - 1))
```

Using normalized values makes Fit and 1:1 modes equivalent and supports negative multi-monitor
origins. Subtracting one keeps `1.0` inside the final pixel rather than one pixel beyond the
display.

## Pointer movement and buttons

The browser sends at most one pending pointer movement per animation frame. Down/up events carry
`left`, `middle`, or `right`. The server first moves the native pointer to the mapped coordinate,
then applies the button operation.

Both browser and server track held buttons. Browser blur, pointer cancellation, WebSocket
disconnect, and server cleanup release them so a lost connection does not leave a native button
pressed.

## Wheel accumulation

Browser deltas are normalized to a bounded range of -20 to 20. The backend keeps two fractional
remainders:

```text
remainder_x -= incoming_dx
remainder_y -= incoming_dy
step_x = trunc(remainder_x)
step_y = trunc(remainder_y)
remainder_x -= step_x
remainder_y -= step_y
```

Only nonzero integer steps are passed to `pynput`. The sign inversion converts browser-positive
down/right deltas to the controller's native convention.

## Keyboard and text algorithms

The browser does not send ordinary printable characters as key events. Instead, a hidden text
input collects regular and composed text and sends it through `text`. Named keys and modified
shortcuts use `key` down/up messages.

This avoids duplicate input from the common sequence of keydown, composition events, and input.
Ctrl/Cmd+V is handled as local pasted text instead of forwarding both a remote shortcut and the
local clipboard contents.

The backend maps browser named keys to `pynput.keyboard.Key` and single characters to
`KeyCode.from_char`. Repeated keydown messages are not injected again. The native operating
system provides repeat behavior while the key remains held.

## Physical-input priority state machine

`ControlArbiter` is shared by every WebSocket and uses a process-wide reentrant lock plus a
monotonic deadline. `pynput` mouse and keyboard listeners report whether an event was injected.
Only events marked as physical renew the local-input deadline:

```text
on physical mouse or keyboard event:
    local_active_until = max(local_active_until, monotonic_now + 0.8 seconds)

if view_only:
    state = restricted
else if physical listeners are unavailable:
    state = restricted
else if monotonic_now < local_active_until:
    state = local_active
else:
    state = available
```

The status task samples this state at 150 ms while stable and 50 ms during local activity. It
sends `control_state` only on transitions. Entering either non-available state clears the
connection's held-input sets and releases those keys/buttons through the native controller.

Before injecting each `pointer`, `wheel`, `key`, `text`, or IME operation, the worker thread acquires the
arbiter lock and recomputes the state. The backend operation runs under that lock. A physical
callback that arrives concurrently waits for the already-started native call to finish, then
immediately establishes the local lease. Messages received after that point are discarded.

The browser mirrors the authoritative state but is not the security boundary. It clears its
held-input state and stops creating input messages whenever permission is lost. A modified client
that continues sending input is still rejected by the server.

## Input method algorithm

The IME controller always sets an explicit target and queries the resulting state. It never
simulates a user-configurable keyboard shortcut.

- Windows remembers an IME keyboard layout, restores it through `WM_INPUTLANGCHANGEREQUEST` when
  necessary, and updates both the foreground IMM32 context and its default IME window. It queries
  the window again and uses the Chinese, Japanese, Korean, or Thai system IME toggle only when the
  explicit update did not reach the requested state.
- Fcitx 4/5 uses the remote command's explicit active/inactive operations.
- IBus reads machine-parseable IDs with `list-engine --name-only`, prioritizes engines from the
  user's configured preload list, remembers both direct and conversion engines, and restores them
  symmetrically.
- macOS combines Text Input Source type and ASCII capability, remembers the previous conversion
  source ID, and prefers a direct source in the same input-method bundle. When the server is
  elevated, a helper performs these operations inside the original Aqua user session.

Platform APIs can report that no controllable source exists. That result is sent as an unsupported
IME capability; a rejected state change becomes a recoverable WebSocket `error`.

A single application-level synchronizer polls the native state every 750 ms while clients are
connected. It serializes polling with remote changes, suppresses unchanged results, and broadcasts
each transition or requested change to every WebSocket client.

## Clipboard algorithm

Clipboard get/set operations run in worker threads through `pyperclip`. Text is limited to
1,000,000 characters. A get truncates an unexpectedly longer native clipboard value; the
protocol validator rejects an oversized set before calling the backend.

Clipboard operations are independent of view-only input control. They are completely rejected
only when clipboard synchronization is disabled.

### Automatic client state machine

Automatic synchronization begins with independent client and server baselines, so enabling it
does not immediately overwrite either clipboard. The client then tracks the last observed text
from both sides. A new local value is sent to the server and optimistically becomes the expected
server value. A new server value is written locally while an `applyingServerClipboard` guard
suppresses the resulting browser change event.

If the client changes before the first server baseline response arrives, a baseline-pending flag
causes that stale response to be ignored. This makes active local input win the only interval in
which ordering is otherwise unknowable.

The client prefers the standard `clipboardchange` event. When it is unavailable, a one-second
poll reads the client clipboard. Server polling continues in either case, but only while the page
is visible and focused. Browser permission loss disables the switch instead of silently running
one-way synchronization.

### Digest negotiation

Server clipboard text is hashed as `BLAKE2b(UTF8(text), digest_size=8)`. Automatic
`clipboard_get` messages include the last known hexadecimal digest:

- a match returns `clipboard_unchanged` with only the digest;
- a mismatch or missing digest returns `clipboard` with text and digest.

This digest is a bandwidth optimization, not an authentication primitive. `clipboard_set`
requests carry a short `requestId`; acknowledgements echo it so the UI can distinguish automatic
writes from explicit manual writes.

## Client-to-server protocol

All client messages are JSON objects. The maximum encoded message size is 1,100,000 bytes.
Numbers must be finite; booleans are not accepted as numeric values.

| Type | Fields | Meaning |
| --- | --- | --- |
| `pointer` | `event`: `move/down/up`, `x`, `y`, and `button` for down/up | Absolute normalized pointer event. |
| `wheel` | `dx`, `dy` in `[-20, 20]` | Normalized scroll delta. |
| `key` | `event`: `down/up`, `key`, `code`, `repeat` | Named key or modified character event. |
| `text` | `text` | Ordinary or composed text to type. |
| `monitor` | nonnegative integer `index` | Change this connection's captured display. |
| `clipboard_get` | optional `knownDigest` | Read server clipboard text, or confirm that a known digest is current. |
| `clipboard_set` | `text`, optional `requestId` | Replace server clipboard text. |
| `ime_set` | boolean `enabled` | Enable or disable the server's current input method mode. |
| `ping` | none | Application-level liveness request. |

Unknown types, invalid ranges, excessive strings, NaN, infinity, and malformed JSON produce an
`error` response without terminating the connection.

## Server-to-client protocol

JPEG frames are binary messages. All other messages are JSON.

| Type | Important fields | Meaning |
| --- | --- | --- |
| `hello` | `protocol`, `title`, `platform`, `viewOnly`, `clipboard`, `control`, `ime`, `screens`, `monitor` | Initial capabilities, permission state, and display list. Current protocol is `1`. |
| `screen` | screen fields | Metadata for the binary frames that follow. |
| `control_state` | `state`, `reason`, `detail` | Authoritative `available`, `local_active`, or `restricted` input permission. |
| `ime_state` | `supported`, `enabled`, `detail` | Authoritative input method state broadcast to every client. |
| `clipboard` | `text`, `digest` | Changed or explicitly requested server clipboard text. |
| `clipboard_unchanged` | `digest` | The supplied server clipboard digest is still current. |
| `clipboard_saved` | `digest`, optional `requestId` | Confirmation of `clipboard_set`. |
| `pong` | none | Response to `ping`. |
| `error` | `message` | Recoverable protocol or desktop operation error. |
| `fatal` | `message` | Desktop capture cannot continue. |

## HTTP route contract

| Route | Authentication | Purpose |
| --- | --- | --- |
| `GET /healthz` | no | Returns `status` and package `version`. |
| `GET /` | PIN Cookie when enabled | Packaged control UI. |
| `GET /auth` | no | Packaged PIN form. |
| `POST /auth` | rate-limited PIN | Sets the authentication Cookie. |
| `POST /logout` | no | Deletes the authentication Cookie. |
| `GET /api/status` | PIN Cookie when enabled | Runtime version, title, platform, control state, modes, and client counts. |
| `GET /static/{filename}` | no | Serves four allowlisted CSS/JavaScript assets. |
| `WS /ws` | PIN Cookie and Origin | Screen and control protocol. |

## Extension points

`create_app` accepts a desktop backend factory. A replacement backend implements initialization,
screen listing, capture, input execution/release, local-input callback/health, clipboard, IME,
and shutdown methods. Tests use this boundary to run the full HTTP and WebSocket stack without
capturing or controlling a real desktop.
