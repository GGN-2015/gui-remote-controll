# CLI Reference

## Invocation

After installation, use either form:

```console
gui-remote-controll [OPTIONS]
python -m gui_remote_controll [OPTIONS]
```

Both entry points call the same parser and startup flow.

## Options

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| `-h`, `--help` | flag | | Print help and exit without elevation. |
| `--version` | flag | | Print the package version and exit without elevation. |
| `--title TITLE` | string | `GUI Remote Controll` | Browser tab, login-page, and control-page title. The value must contain non-whitespace text and cannot exceed 200 characters. |
| `--host HOST` | string | `0.0.0.0` | Bind address passed to Uvicorn. Use `127.0.0.1` for a local-only listener. |
| `--port PORT` | integer | `8000` | TCP port from 1 to 65535. |
| `--pin PIN` | string | disabled | Require a PIN before the UI, protected API, or WebSocket can be used. An explicitly empty PIN is rejected. |
| `--fps FPS` | integer | `10` | Maximum screen capture rate from 1 to 30 frames per second. |
| `--jpeg-quality QUALITY` | integer | `80` | JPEG encoder quality from 20 to 95. |
| `--monitor INDEX` | integer | `1` | Initial monitor. `0` is the combined virtual desktop; physical monitors start at `1`. |
| `--view-only` | flag | disabled | Ignore pointer, wheel, key, text, and IME control messages. |
| `--no-clipboard` | flag | disabled | Disable remote clipboard get/set operations. |
| `--no-cursor` | flag | disabled | Disable system cursor composition in Linux/X11 captures. |
| `--max-clients COUNT` | integer | `4` | Maximum simultaneous WebSocket clients from 1 to 32. |
| `--trusted-origin ORIGIN` | repeatable string | none | Permit an additional exact browser Origin for WebSocket connections. |
| `--tls-certfile PATH` | path | none | TLS certificate file. Must be used with `--tls-keyfile`. |
| `--tls-keyfile PATH` | path | none | TLS private key file. Must be used with `--tls-certfile`. |
| `--open-browser` | flag | disabled | Open the printed local URL about 0.8 seconds after startup. |
| `--no-elevate` | flag | disabled | Skip the single administrator/root relaunch. |
| `--log-level LEVEL` | choice | `info` | One of `critical`, `error`, `warning`, `info`, `debug`, or `trace`. |

The internal elevation marker is intentionally hidden and is not a public option. It exists only
to guarantee that the automatically relaunched process does not request elevation again.

## Validation and startup failures

The CLI exits with an argparse error when:

- the host is empty;
- the title is empty/whitespace or longer than 200 characters;
- the port is outside 1-65535;
- FPS is outside 1-30;
- JPEG quality is outside 20-95;
- the monitor index is negative;
- the client limit is outside 1-32;
- only one TLS file is supplied;
- a supplied TLS file does not exist;
- elevation fails or is cancelled on a platform that reports the failure.

If `0.0.0.0` or `::` is used without a PIN, startup prints a security warning but continues.

The desktop backend is initialized when the first WebSocket connects. A missing desktop,
permission failure, or unsupported native Wayland session is therefore reported to the browser
as `Desktop unavailable` without preventing the HTTP health endpoint from starting.

If screen capture starts but global physical-input listeners do not, the browser can still view
the desktop but remote input remains **Control access restricted**. This preserves the invariant
that a server user always has priority over remote clients.

## Elevation behavior

Unless `--no-elevate` is present, the initial process launches:

```text
CURRENT_PYTHON -m gui_remote_controll --elevation-attempted ORIGINAL_ARGUMENTS
```

The parent exits after the launcher returns. Windows elevation does not expose the elevated
child's later exit code; Linux and macOS return it when the platform launcher supports waiting.

## Common configurations

### Trusted LAN control

```console
gui-remote-controll --pin 846291
```

Listens on all interfaces at port 8000.

### Named client

```console
gui-remote-controll --pin 846291 --title "Rendering workstation"
```

The title is escaped as text before it is rendered; it cannot inject HTML into the client.

### Local-only control

```console
gui-remote-controll --host 127.0.0.1 --pin 846291
```

Use this behind an SSH tunnel, local VPN agent, or reverse proxy.

### Combined multi-monitor desktop

```console
gui-remote-controll --pin 846291 --monitor 0
```

### Higher-motion desktop

```console
gui-remote-controll --pin 846291 --fps 20 --jpeg-quality 75
```

Reducing quality can offset the bandwidth cost of a higher frame rate.

### High-detail, low-motion desktop

```console
gui-remote-controll --pin 846291 --fps 5 --jpeg-quality 92
```

### Strict view-only sharing

```console
gui-remote-controll --pin 846291 --view-only --no-clipboard --max-clients 8
```

### HTTPS

```console
gui-remote-controll --pin 846291 --tls-certfile server.crt --tls-keyfile server.key
```

### Reverse proxy with an additional origin

```console
gui-remote-controll --host 127.0.0.1 --pin 846291 --trusted-origin https://remote.example.com
```

Repeat `--trusted-origin` when more than one exact origin is required.

### Development without elevation

```console
gui-remote-controll --host 127.0.0.1 --pin 846291 --no-elevate --log-level debug
```

## Runtime server settings

The CLI configures Uvicorn with:

- a 1,100,000-byte WebSocket message limit;
- WebSocket ping interval and timeout of 20 seconds;
- graceful shutdown timeout of three seconds;
- automatic WebSocket implementation selection;
- no reload process.

Ctrl+C and ordinary process termination are handled by Uvicorn. Active WebSockets close during
shutdown and their held input state is released where cleanup can run.
