# Development and Release Guide

## Repository layout

```text
gui_remote_controll/
  __main__.py             CLI and elevation
  app.py                  FastAPI routes and WebSocket sessions
  auth.py                 PIN Cookie and rate limiter
  config.py               runtime settings
  desktop.py              capture, input, clipboard, platform handling
  protocol.py             client message validation
  static/                 packaged HTML, CSS, and JavaScript
tests/                    unit and ASGI/WebSocket integration tests
docs/                     user, CLI, algorithm, platform, and release manuals
pyproject.toml            PEP 621 metadata and Poetry build configuration
```

## Local setup

Do not develop in a global Python installation. Create a repository-local virtual environment.

### Windows PowerShell

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -e . pytest httpx2 ruff build
```

### Linux or macOS

```console
python3 -m venv venv
venv/bin/python -m pip install -e . pytest httpx2 ruff build
```

The desktop dependencies are loaded lazily. Importing the package, showing CLI help, and running
the fake-backend web tests do not require a graphical session.

## Running from source

Use `--no-elevate` during ordinary development to avoid restarting under an administrator
launcher:

```powershell
.\venv\Scripts\python.exe -m gui_remote_controll `
  --host 127.0.0.1 --pin 2468 --no-elevate --log-level debug
```

Use `--view-only` while inspecting the browser UI when accidental input injection would be
unsafe.

## Test suite

```console
python -m pytest
```

The test modules cover:

- PIN validation, Cookie attributes, and login rate-window expiry;
- CLI defaults, validation, one elevation attempt, and recursion prevention;
- virtual desktop offsets and fractional wheel accumulation;
- every protocol family and malformed-message rejection;
- health/security headers, PIN form flow, WebSocket metadata/binary frames, and Origin rejection.

The ASGI tests use a fake backend so they cannot capture the developer's screen or inject input.
A manual real-backend check should enumerate displays and capture a JPEG without sending control
messages.

## Lint and compilation

```console
ruff check .
python -m compileall -q gui_remote_controll tests
```

The project targets Python 3.10 syntax and uses a 100-column Ruff line limit for Python code.

## Browser verification

Before release, verify at least:

- PIN login and logout;
- a nonempty frame with correct natural dimensions;
- Fit and 1:1 layouts;
- display selector behavior on a multi-monitor system;
- mouse, buttons, wheel, ordinary text, shortcuts, and composition input;
- remote clipboard read/write and the disabled state;
- view-only mode;
- disconnect/reconnect and held-input cleanup;
- desktop and narrow mobile viewport layouts with no page overflow;
- no browser console errors.

## Continuous integration

`.github/workflows/ci.yml` runs on Windows, Ubuntu, and macOS with Python 3.10 and 3.13. Each job:

1. installs the package and development tools;
2. runs pytest;
3. runs Ruff;
4. builds the wheel and source distribution.

CI intentionally uses the fake desktop backend because hosted runners do not provide equivalent
interactive desktop sessions.

## Build

The repository uses standard PEP 621 metadata and Poetry's build backend.

```console
poetry check
poetry build
```

Expected artifacts:

```text
dist/gui_remote_controll-VERSION-py3-none-any.whl
dist/gui_remote_controll-VERSION.tar.gz
```

The wheel must contain all Python modules and the six files in
`gui_remote_controll/static/`. The source distribution must also contain the root readmes,
security policy, and `docs/` manuals.

Useful checks on Windows PowerShell:

```powershell
$wheel = Get-ChildItem .\dist\*.whl | Select-Object -First 1
tar -tf $wheel.FullName
.\venv\Scripts\python.exe -m pip install --dry-run --force-reinstall --no-deps $wheel.FullName
```

## Versioning

The version is defined in both:

- `project.version` in `pyproject.toml` for package metadata;
- `gui_remote_controll.__version__` for runtime and health responses.

Update both values together. Build and inspect metadata before committing a release.

## Release checklist

1. Update the version in both locations.
2. Review the user, CLI, platform, security, and algorithm manuals.
3. Run pytest, Ruff, and compileall.
4. Complete real Windows, Linux/X11, and macOS permission/capture checks where available.
5. Run `poetry check` and `poetry build`.
6. Inspect wheel contents, entry points, metadata, and SHA256 hashes.
7. Confirm the working tree contains only intended changes.
8. Commit and push the release source.
9. Publish with `poetry publish` only after explicit release authorization.
10. Install the public version into a clean virtual environment and repeat the Quick Start.

Publishing is an external, permanent action. Building a valid wheel does not itself publish a
PyPI version.
