# Changelog

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
