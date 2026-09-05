---
name: background-desktop
description: Control installed Linux desktop apps through their GUI in an isolated background workspace on Omarchy/Hyprland. Use for desktop tasks such as operating DaVinci Resolve or other native apps when GUI interaction is needed.
---

Use the `background-desktop` MCP server for desktop GUI work. It allocates an unused workspace automatically and keeps input on a private display. Prefer a suitable app API or browser tool when the task does not need desktop GUI interaction.

1. Create a dedicated output directory for the task. Call `desktop_start` with its absolute path. The runtime reserves a free workspace; do not choose a numbered workspace manually or use a host launcher.
2. Call `desktop_launch` with an executable and argument array. For Resolve, the executable is `/opt/resolve/bin/resolve`. Apps use temporary profiles, so first-run setup may appear.
3. Inspect `desktop_tools` for Cua argument schemas. Use `desktop_call` with `list_windows`, then `get_window_state` for the exact returned PID/window ID. Coordinates are pixels in the latest window screenshot; Cua applies its recorded resize ratio. Do not rescale them a second time. Desktop screenshots use screen coordinates, so capture the target window again before window-addressed clicks. Refresh the window list after dialogs open or close.
4. Act, then inspect the resulting state. The runtime sets foreground input only inside the private display. Tool delivery success alone does not prove the app changed. Do not replay an action after a timeout without observing first.
5. Save/export deliverables to the supplied output directory, verify the files, and call `desktop_stop` when finished or abandoning the task. Profiles and app-local project databases are temporary. For Resolve, export the project as `.drp` and any rendered media before stopping.

During a long render or wait, periodically capture the app state so the active task remains leased and you can verify completion before cleanup.

An existing app instance cannot move between X displays. Open the necessary files in a new isolated instance. Existing host files are readable; writes are limited to the output directory and disposable profile. Never relaunch on the host as a fallback for a failed isolated launch.

If the user enters the reserved workspace or puts another window there, input pauses. Explain the condition; do not move the user's window or switch their workspace. Disconnect, process death, and 15 minutes without desktop activity trigger cleanup. Explicit `desktop_stop` is still the normal completion path.

This is input isolation for trusted applications, not a security sandbox. It shares networking and GPU resources. Native Wayland-only applications, host audio/clipboard integration, and transferring an already-running host instance are outside this implementation.
