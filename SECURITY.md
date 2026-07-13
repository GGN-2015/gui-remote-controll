# Security Policy

## Supported versions

Security fixes are applied to the latest released version.

## Reporting a vulnerability

Do not open a public issue for an undisclosed vulnerability. Use GitHub's private security
advisory feature for this repository, or contact the repository owner privately.

Include the affected version, platform, configuration, reproduction steps, impact, and any
suggested mitigation. Please allow a reasonable time for investigation before disclosure.

## Security model

GUI Remote Controll gives authenticated clients the ability to observe and operate the signed-in
desktop. Its PIN protects the web boundary; it does not provide end-to-end encryption. Use TLS
or an encrypted tunnel whenever traffic leaves a trusted network.

The server intentionally does not persist the PIN, authentication cookies, frames, keystrokes,
or clipboard text. Authentication tokens are signed with a random process-local secret and
become invalid after restart. Login failures are rate-limited in memory.

Automatic clipboard synchronization is disabled by default because clipboard text may contain
passwords, tokens, private messages, or other sensitive data. Enabling it authorizes plain-text
clipboard contents to move in both directions while the browser tab is active. Use HTTPS or an
encrypted tunnel so that clipboard data is not exposed in transit.

Administrator/root elevation increases impact if the service is exposed incorrectly. The server
requests elevation at most once per startup and supports `--no-elevate`, but operators remain
responsible for restricting network reachability and choosing a strong PIN.

Physical-input priority is a safety and usability mechanism, not authentication. The server
rejects remote input while physical activity is detected and releases remotely held inputs, but
operating-system hook delivery can still be delayed or unavailable. When listeners cannot start,
remote control is restricted instead of running without local priority. Operators should still
disconnect or stop the server before performing sensitive local work.

The IME button changes the active input mode of the foreground server application. It does not
stop an input-method service, and it cannot override secure desktops, higher-integrity windows,
macOS privacy controls, or compositor policy.
