---
name: background-desktop
description: Control installed Linux desktop apps through their GUI in an isolated background workspace on Omarchy/Hyprland. Use for desktop tasks such as operating DaVinci Resolve or other native apps when GUI interaction is needed.
---

Use the `background-desktop` MCP server for desktop GUI work. It allocates an unused workspace automatically and keeps input on a private display. Prefer a suitable app API or browser tool when the task does not need desktop GUI interaction.

1. Create a dedicated output directory for the task. Call `desktop_start` with its absolute path. The runtime reserves a free workspace; do not choose a numbered workspace manually or use a host launcher.
2. Call `desktop_launch` with an executable and argument array. For Resolve, the executable is `/opt/resolve/bin/resolve`. Apps use session profiles, so first-run setup may appear. The default review lifetime retains open apps after the agent disconnects. Use lifetime="disposable" only for throwaway tests or tasks that explicitly require automatic destruction.
3. Inspect `desktop_tools` for Cua argument schemas. Use `desktop_call` with `list_windows`, then `get_window_state` for the exact returned PID/window ID. Coordinates are pixels in the latest window screenshot; Cua applies its recorded resize ratio. Do not rescale them a second time. Desktop screenshots use screen coordinates, so capture the target window again before window-addressed clicks. Refresh the window list after dialogs open or close.
4. Act, then inspect the resulting state. The runtime sets foreground input only inside the private display. Tool delivery success alone does not prove the app changed. Do not replay an action after a timeout without observing first.
5. Save/export deliverables to the supplied output directory and verify them. For editing, design, or other tasks with an app the user may want to inspect, call `desktop_finish` with `keep_open=true`. It stops agent input, hands control to the user, and keeps the app and its session profile alive. Report the workspace and session ID. Use `keep_open=false` or `desktop_stop` only when closing the apps is appropriate to the request, such as a disposable test. Task completion alone is not permission to destroy reviewable app state. Export Resolve `.drp` projects and media even when retaining the app.

During a long render or wait, periodically capture the app state so the active task remains leased and you can verify completion before cleanup.

An existing app instance cannot move between X displays. Open the necessary files in a new isolated instance. Existing host files are readable; writes are limited to the output directory and disposable profile. Never relaunch on the host as a fallback for a failed isolated launch.

The user may visit the workspace and watch while automation continues. In agent mode the private display's physical Wayland input devices are disabled; its XTEST input remains active. Never ask the user to switch away just to observe. To let the user interact, call `desktop_control` with `mode="user"`; read-only capture remains available but agent mutations are blocked. Resume `mode="agent"` only when the user asks to continue. Neither transition switches the host workspace or focuses the desktop.

Use `desktop_sessions` to find retained desktops and `desktop_attach` with the exact session ID when continuing work there. Attaching preserves the current control mode. A task finish, agent disconnect/death, or 15-minute agent idle timeout hands open apps to the user in the default review lifetime. Retained desktops have no idle expiry: close their last app or explicitly call `desktop_stop` when done. Worker/supervisor failure still reclaims owned processes, and output files remain. The runtime profile is not a permanent backup.

The user can also manage retained sessions from a terminal with `python ~/.local/share/cua-background/supervisor.py list`, `status SESSION_ID`, or `stop SESSION_ID`.

This is input isolation for trusted applications, not a security sandbox. It shares networking and GPU resources. Native Wayland-only applications, host audio/clipboard integration, and transferring an already-running host instance are outside this implementation.
