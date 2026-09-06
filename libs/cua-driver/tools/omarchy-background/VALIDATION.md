# Validation on the development machine

Verified on 2026-09-05 with Omarchy, Hyprland 0.56.2, NVIDIA RTX 4070 Laptop graphics, Python 3.14, and DaVinci Resolve 21.0.4.0005. The upstream base is `1336a5e`. The installed driver SHA-256 is `576150fd73aa0341c642628e44eaeae9e09a3c95432fa9b50395cb14f8992db9`.

## Automated checks

- Rust input tests: **18 passed, 1 ignored**. The ignored test requires a hardware uinput environment; private desktops do not expose uinput.
- Python lifecycle/installation tests: **5 passed**, including unused-workspace selection across monitors, concurrent reservations, interrupted/stale allocation recovery, PID-reuse protection, pausing on user workspace reuse, and install/uninstall preservation.
- Live control matrix: **passed**. Two separate desktops and a second app in the first desktop verified window activation and input separation. Exact assertions covered click, text including Hebrew, F5, Ctrl+Shift+K, right/double click, scrolling, drag, private clipboard, hover, and persistent press/move/release. Captures changed after edits, and desktop capture was 1800 × 1000.
- Cleanup matrix: **passed** for failed app launch, repeated stop, exported-output preservation, MCP EOF, and forcibly killing the MCP process.
- Installed MCP smoke: **passed**, using the user-local driver and signed Openbox dependency bundle. It verified real Qt input, output files under `/tmp`, output survival after stop, private-worker crash reclamation, and starting another session through the same MCP connection.
- Hyprland configuration validation and Codex skill validation: **passed**.

The control run sampled the host 305 times and observed **zero agent-window focus events**. It saw 1 user focus target and 1 visible-workspace set. The user could move the physical pointer during testing; sampled coordinates are not treated as proof that human input must remain stationary. An earlier private-display test with an idle physical pointer also observed no movement across 201 samples. That earlier result supports the isolation design but is not the final-runtime control matrix.

Machine-readable results are in [validated-results.json](tests/validated-results.json).

## Resolve workflow

Through the private desktop on an automatically reserved workspace:

1. Completed first-run setup in a disposable profile.
2. Created a project named `Cua background שלום`; the native window title confirmed the exact Hebrew text.
3. Imported a generated two-second DNxHR/PCM test clip through the GUI file dialog.
4. Dragged the clip from the media pool into a new timeline and verified the viewer and timeline capture.
5. Exported a `.drp` through Resolve's Export Project dialog.
6. Verified the ZIP structure, Hebrew project name, media reference, and sequence XML in the exported project.
7. Stopped the session and verified resource cleanup. Removed the disposable test project and generated media after verification.

This validates a real editing workflow and project export. It does **not** certify long video renders, audio playback, every Resolve feature, or all codecs. Resolve exposed little usable accessibility data; these actions used fresh screenshots and window-addressed controls.

## Reproduce

From this directory, with the Hyprland rule installed:

```bash
python -m unittest discover -s tests -p 'test_*.py'
python tests/integration.py --driver ../../rust/target/release/cua-driver
python tests/installed_smoke.py
```

For the private dependency bundle, add `--dependency-root "$HOME/.local/share/cua-background/dependencies"` to the integration command. Qt5 development files and `g++` are needed only for these GUI fixtures. The installed smoke reads the installation manifest and uses its actual command.

The full upstream macOS/Windows/Linux desktop certification matrix was **not run**. This is a tested Omarchy-specific fork, not a claim of upstream release certification or compatibility with other compositors, GPUs, or native Wayland-only applications.


## Observation and review lifecycle, 2026-09-06

The Python-only change keeps the installed Rust driver unchanged. The updated native Qt control matrix passed all existing input/capture/isolation checks, with 307 host samples, one user focus target, one visible workspace set, and no agent-window focus events. Disposable EOF and killed-client cleanup still passed.

`tests/review_integration.py` passed against Hyprland 0.56.2:

- Private Xwayland physical devices disabled in agent mode, XTEST devices enabled; user handoff restores physical devices. The installed smoke also passed real Qt input, worker-crash cleanup, /tmp output preservation, and same-client restart.
- A real Qt click succeeds with a mocked visible-workspace snapshot. No host workspace was switched for the test. This is not a claim of a human observation test.
- User mode allows captures and rejects mutations; resuming agent mode restores input.
- An open app survives MCP EOF, MCP SIGKILL, and explicit finish. A new client can reconnect; attach alone does not grant input.
- Closing the last native app reclaims the supervisor, worker, and runtime directory; explicit stop also reclaims them.
- Host focus, visible workspaces, and cursor position were unchanged across this run.

Run `python tests/review_integration.py` from this directory with the installed driver/dependency bundle. It builds a Qt test fixture inside a temporary output directory and removes it after verified cleanup. Existing Rust/macOS/Windows paths were not modified or recertified. A real human watching and interacting after handoff remains a separate acceptance check.
