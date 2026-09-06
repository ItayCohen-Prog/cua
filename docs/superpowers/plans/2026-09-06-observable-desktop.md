# Observable desktop implementation plan

Goal: Allow watching agent work and retain finished applications for user review.

Architecture: Keep the private Xwayland display and existing Cua driver. A per-session Unix-socket supervisor owns the worker independently of MCP clients. Agent mode disables only the private display's Wayland input devices; user mode stops agent mutations and restores human input without selecting a host workspace. Default review lifetime survives client EOF/death; explicitly disposable sessions retain automatic cleanup.

Spec: User request in the current task, 2026-09-06. Watching must not pause work; task completion must consider whether the app should remain open. Preserve dynamic allocation, isolation, and deterministic deallocation.

Research basis: Hyprland 0.56.2 local Lua dispatcher/readback test and https://wiki.hypr.land/Configuring/Basics/Window-Rules/ accessed 2026-09-06 support address-specific focus overrides. https://wayland.freedesktop.org/docs/book/Xwayland.html confirms rootful separation but host input still enters through Wayland. Merely removing the visible-workspace guard is insufficient: verify private input suppression locally. A broker is necessary because bubblewrap die-with-parent makes transferring ownership after MCP exit unreliable. No additional system packages.

- [x] Add private XI2 device control in input_gate.py; never modify master/XTEST devices or a host display. Verify enabled state after changes, retain XTEST delivery, and reject unclassified enabled slave devices. Test agent/user transitions and private device isolation.
- [x] Update Session validation and modes in background.py. Visibility is observation, not contention. User mode permits captures and blocks all mutations. Disable human input before resuming agent mode. Set only the owned window's no_focus override and never focus or switch it.
- [x] Add supervisor.py with serialized local requests, controller identity, reconnect, review-on-disconnect/idle, explicit disposable cleanup, last-app-close cleanup, and status/list/stop CLI. Keep PID stamp and guardian cleanup for actual supervisor/worker failures. Add focused lifecycle tests including real process death.
- [x] Expose finish, control, sessions and attach MCP tools. Update installer and existing background-desktop/Resolve guidance, replacing unconditional stop advice. Record behavior and verification in README and VALIDATION.
- [x] Run unit and native Qt integration tests, verify host focus/cursor isolation and observing mode without workspace switching, verify reconnect/handoff/cleanup, install the tested Python files, then run a visible observation/handoff fixture for user acceptance. Remove temporary test artifacts after verification.

Issue tracking is disabled on this personal fork. The draft PR is the decision record for this user-selected change; no upstream issue or unsolicited message is needed.
