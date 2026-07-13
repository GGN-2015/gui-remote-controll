# Documentation

This directory contains the complete GUI Remote Controll manual. Start with the user guide
unless you are looking for a specific command-line option or implementation detail.

| Manual | Contents |
| --- | --- |
| [User guide](user-guide.md) | Browser controls, sessions, displays, input, clipboard, view-only mode, reconnection, TLS, and operational workflows. |
| [CLI reference](cli-reference.md) | Every public command-line option, defaults, validation rules, and deployment examples. |
| [Architecture and algorithms](architecture-and-algorithms.md) | Process lifecycle, authentication, capture pipeline, frame deduplication, coordinate mapping, input algorithms, HTTP routes, and WebSocket protocol. |
| [Platform support](platform-support.md) | Windows, Linux/X11 desktop environments, Wayland limitations, macOS permissions, and platform troubleshooting. |
| [Development and release](development.md) | Local setup, repository layout, tests, linting, CI, building, artifact inspection, and PyPI release steps. |
| [Security policy](../SECURITY.md) | Threat model, operational requirements, and vulnerability reporting. |
| [Changelog](../CHANGELOG.md) | User-visible changes by package version. |

## Support boundary

GUI Remote Controll controls the currently signed-in interactive desktop. It is not a service
for the Windows secure desktop, pre-login screens, native Wayland compositors, DRM-protected
content, system audio, file transfer, or unattended operating-system installation.
