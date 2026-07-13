# Changelog

## 0.1.3

- Fix Windows IME re-enabling when `ImmSetOpenStatus` reports success without changing the
  foreground IME window.
- Restore an installed Windows IME input layout before opening when the foreground application is
  using a direct-input layout.
- Fall back to the language-specific Windows IME system toggle only when explicit open-status
  updates do not reach the requested state.
- Verify delayed Windows IME state changes before reporting the operation result.

## 0.1.2

- Add `--title` with the default client title `GUI Remote Controll`.
- Add native server IME status and enable/disable controls for Windows, Linux, and macOS.
- Monitor physical server input and temporarily suspend remote input injection while it is active.
- Filter injected `pynput` events so remote operations do not trigger the physical-input lease.
- Add a WebSocket-driven three-state remote-control permission indicator.
- Release remotely held keys and buttons whenever physical input takes priority.
- Version static asset URLs so upgraded browser clients cannot reuse stale JavaScript or CSS.

## 0.1.1

- Add opt-in bidirectional automatic clipboard synchronization.
- Request browser clipboard access when the remote UI opens and retry from the switch gesture.
- Prefer `clipboardchange` events with a focused-page polling fallback.
- Establish independent initial baselines and suppress synchronization feedback loops.
- Add digest-aware server polling and request-correlated clipboard write acknowledgements.
- Redirect stale authenticated pages to PIN login after a server restart.
- Document secure-context requirements and clipboard privacy considerations.

## 0.1.0

- Initial cross-platform browser remote desktop server.
