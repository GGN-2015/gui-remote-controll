# GUI Remote Controll

[中文](https://github.com/GGN-2015/gui-remote-controll/blob/main/README.zh-CN.md)

GUI Remote Controll shares a Windows, Linux, or macOS desktop with a web browser. The
browser receives screen frames and can redirect mouse, touch, wheel, keyboard, text input,
and plain-text clipboard events to the server computer.

## Quick Start

Python 3.10 or newer is required.

1. Install the package:

   ```console
   pip install gui-remote-controll
   ```

2. Start a PIN-protected server:

   ```console
   gui-remote-controll --pin 123456
   ```

   Startup requests administrator/root privileges once. Use `--no-elevate` only when the
   desktop permissions have already been configured or elevation is inappropriate.

3. Open the control page:

   - On the server computer: `http://127.0.0.1:8000`
   - On the local network: `http://SERVER_LAN_IP:8000`

The default bind address is `0.0.0.0`. Always set `--pin` when another device can reach the
server. Use TLS or an encrypted tunnel outside a trusted LAN, and never expose the default
HTTP service directly to the public internet.

### First-run permissions

- **Windows:** accept the UAC prompt. Run the server from the signed-in interactive session.
- **Linux:** use an X11 desktop session. Native Wayland does not permit portable global
  capture and input injection.
- **macOS:** grant Screen Recording and Accessibility permissions to the terminal or Python
  executable, then restart the server. Root access does not replace these permissions.

See the [platform guide](https://github.com/GGN-2015/gui-remote-controll/blob/main/docs/platform-support.md)
for setup and troubleshooting details.

## Documentation

- [Documentation index](https://github.com/GGN-2015/gui-remote-controll/blob/main/docs/README.md)
- [Complete user guide](https://github.com/GGN-2015/gui-remote-controll/blob/main/docs/user-guide.md)
- [CLI reference](https://github.com/GGN-2015/gui-remote-controll/blob/main/docs/cli-reference.md)
- [Architecture and algorithms](https://github.com/GGN-2015/gui-remote-controll/blob/main/docs/architecture-and-algorithms.md)
- [Platform support](https://github.com/GGN-2015/gui-remote-controll/blob/main/docs/platform-support.md)
- [Development and release guide](https://github.com/GGN-2015/gui-remote-controll/blob/main/docs/development.md)
- [Changelog](https://github.com/GGN-2015/gui-remote-controll/blob/main/CHANGELOG.md)
- [Security policy](https://github.com/GGN-2015/gui-remote-controll/blob/main/SECURITY.md)

The Python import package is `gui_remote_controll`. The PyPI distribution and command name
intentionally follow the repository spelling, `gui-remote-controll`.

## License

[MIT](https://github.com/GGN-2015/gui-remote-controll/blob/main/LICENSE)
