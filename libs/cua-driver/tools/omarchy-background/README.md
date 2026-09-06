# Background desktop tasks on Omarchy

This fork adds task-owned background desktops to Cua Driver on Omarchy with Hyprland's Lua configuration. An agent reserves an unused workspace, launches apps in a private Xwayland display, controls them through Cua, exports its results, then leaves reviewable apps open for the user. The user's active workspace, keyboard focus, pointer, and existing app instances stay separate.

This is an experimental Linux integration, tested on Omarchy with Hyprland 0.56.2, NVIDIA RTX 4070 Laptop graphics, and DaVinci Resolve 21.0.4. It is not an official Cua or Omarchy release. See [validation](VALIDATION.md) for the actual coverage and limits.

## Why a private display

Moving an ordinary app to an inactive Hyprland workspace does not create a separate input seat. During development, Cua's MPX fallback created a uinput pointer that Hyprland treated as a real device. A background click moved the host cursor and selected the app's workspace. The fork now refuses that uinput path on Wayland/XWayland.

Qt applications also do not reliably accept synthetic XSendEvent clicks. Cua's ordinary foreground XTEST controls work inside a private X server, where activation, dragging, shortcuts, and clipboard operations affect only that task. This implementation uses:

- **Rootful Xwayland**, one independent X11 display per task, presented as one Wayland window. “Rootful” means it has a root window, not root privileges.
- **Openbox** inside that display, providing window discovery, activation, stacking, and dialogs for multiple applications.
- **Bubblewrap** with private PID, `/tmp`, `/dev`, and runtime mounts. The GPU render devices are available; physical input, uinput, the host D-Bus, and host display filesystem sockets are not.
- **A narrow Hyprland rule** that stages the reserved Xwayland display range on an inactive special workspace, rejects focus requests, and keeps frames rendering. The manager checks the owning PID and rule properties, then moves only that window silently to a free numbered workspace.
- **A Python stdio MCP manager and discoverable Codex skill**, connecting natural desktop requests to the same session lifecycle.

The upstream native Hyprland plugin experiment would require version-matched compositor integration and did not cover this machine's XWayland apps. A nested Weston prototype proved input isolation but lacked the window-manager behavior needed for reliable multiple-app control. Neither alternative remains in this implementation.

## What changed in Cua Driver

1. MPX/uinput refuses shared Wayland seats, including XWayland detected through the X server extension. The check applies even if a caller removes `WAYLAND_DISPLAY`.
2. Foreground Unicode typing maps missing characters to distinct temporary keycodes for the whole string. Reusing a keycode before Qt processes its mapping notifications produced repeated or wrong letters. All temporary mappings are restored after delivery.
3. Explicit foreground typing uses keyboard events instead of falling through to AT-SPI insertion. GTK and Qt interpret the insertion length differently: the documented UTF-8 byte count fixed GTK truncation but padded Qt text with NUL characters. The experimental blanket length change was removed. Element-addressed accessibility operations retain upstream behavior.
4. The task manager always routes supported Cua input as foreground **inside the private display**. There is no host input fallback.
5. Reading an unowned X11 clipboard returns an empty format list instead of a BadAtom error.
6. The private accessibility bus explicitly uses `dbus-daemon`. Arch's `dbus-broker` accessibility launcher otherwise attempts to activate services through a systemd user manager that is deliberately absent in the task namespace.

The manager provides persistent XTEST press/move/release and hover within the private display. Cua's upstream held-button tools only supported synthetic background events, which Qt ignores. Other controls use the driver directly. The manager's `desktop_tools` returns the applicable argument schemas for capture, window discovery/activation, text, keys, shortcuts, mouse controls, scrolling, clipboard, accessibility, and state verification. It filters out unrelated host setup and install tools. `desktop_call` forwards results, including native MCP images.

## Install

Prerequisites are an Omarchy Hyprland Lua session, Python 3.11+, Bubblewrap, Xwayland with rootful support, Xauth, Xprop, D-Bus, Cua's Rust build dependencies, and an authenticated/configured Codex CLI. Check `omarchy install --help` before acquiring dependencies; use a dedicated installer when one exists.

Build the driver from this fork:

```bash
cd libs/cua-driver/rust
cargo build --release --locked -p cua-driver --features portal-input
cd ../tools/omarchy-background
python install.py --driver ../../rust/target/release/cua-driver --private-openbox
```

`--private-openbox` downloads pinned Openbox, imlib2, and startup-notification Arch x86_64 packages, checks their signatures against the installed Arch keyring, and extracts them into the integration directory. It does not replace system packages. The runtime overlays those files read-only in the task namespace. If Openbox is already installed, omit the flag. Both installations use the same runtime.

The installer creates `~/.local/share/cua-background`, adds one marked include in `~/.config/hypr/hyprland.lua`, registers `background-desktop` with Codex, and installs the automatic skill. It replaces the previous `cua-driver` MCP registration, if present, to avoid leaving the shared-seat route as a competing default. An installation manifest retains that registration for uninstall. Other MCP servers and desktop rules are preserved.

Start a new Codex task to load the tools and skill. Ask for the app task normally. The agent uses `desktop_start`, `desktop_launch`, `desktop_tools`, `desktop_call`, and `desktop_stop`; it does not pick a fixed workspace. A session can contain multiple apps. Separate MCP processes get separate workspace/display leases.

To remove it:

```bash
python install.py --uninstall
```

The installer refuses updates/uninstall while sessions are active. Uninstall removes its marked config, runtime and skill, and restores the previous Cua registration when applicable. It preserves exported task outputs.

## Ownership and cleanup

Workspace allocation runs under an interprocess lock. It excludes occupied workspaces, the active workspace on every monitor even if empty, and other task reservations. Display numbers come from the reserved range 62001–62999.

Each desktop has an independent supervisor and Unix control socket. MCP clients attach to it; they do not own the app processes. The supervisor and worker still use PID start times and a pidfd guardian, so actual process failure cleans up verified owned resources without signalling a reused PID.

`desktop_start` defaults to `lifetime="review"`. `desktop_finish` defaults to `keep_open=true`: it stops agent mutations, enables human input to the private display, and leaves the apps open. The same handoff happens if the agent disconnects, dies, or goes idle for 15 minutes. There is no idle expiry after handoff. Closing the last app or explicitly stopping the desktop releases its workspace, processes, profile, sockets, and logs. An empty desktop is not retained. For throwaway tests, `lifetime="disposable"` keeps the earlier EOF/death/idle cleanup behavior. `desktop_finish(keep_open=false)` and `desktop_stop` explicitly destroy the desktop.

Exports remain in the task output directory. Session profiles remain available during review but are removed when the desktop is closed. Save/export important work there, including a Resolve `.drp` and its media; a retained app is not a permanent backup. The filesystem mounts and private sockets are unchanged.

### Watching and taking control

You can visit the reserved workspace while the agent works. Visibility and other windows on that workspace no longer pause private input. In agent mode, XI2 disables the private Xwayland keyboard, pointer, relative-pointer, and gesture devices. XTEST and master devices stay enabled for Cua. The gate verifies device state before driver operations and rejects unknown slave devices. It never opens the host X display or physical device nodes.

`desktop_control(mode="user")` restores the private physical devices and makes the owned desktop focusable when you click it. Captures still work, but agent edits, keys, mouse input, clipboard writes, and launches are rejected. `mode="agent"` suppresses physical input before resuming automation. Neither transition focuses a window or switches a host workspace. The Hyprland override uses `hl.dsp.window.set_prop` with the verified owned address and readback; this build's legacy `hyprctl setprop` returns `unknown request` despite advertising command help.

`desktop_sessions` lists retained desktops. `desktop_attach(session_id=...)` reconnects to one without changing control mode; another live agent cannot take over an attached desktop. The user can ask the agent to hand over or resume control.

For retained desktops, a terminal also supports:

```bash
python ~/.local/share/cua-background/supervisor.py list
python ~/.local/share/cua-background/supervisor.py status SESSION_ID
python ~/.local/share/cua-background/supervisor.py stop SESSION_ID
```

Update the integration with the usual installer command. It reuses an existing private dependency bundle, so Python-only updates do not download or replace system packages. `--private-openbox` explicitly refreshes that bundle.

## Limits

- This is **input isolation for trusted apps**, not a security boundary against malicious applications. The host filesystem is readable, networking is shared, and GPU resources are shared. The task is not a VM.
- Existing host app instances cannot migrate between displays. Launch a new instance and open/import the required files. An app with a system-wide singleton or unusual licensing may need separate support.
- Apps must support X11. Qt, GTK, and supported browser backends can run through Xwayland. Native Wayland-only apps are outside this implementation.
- Host audio, clipboard, desktop portals, keyrings, and notifications are not bridged. The private clipboard works between apps within a task. Screenshot and visual controls remain available when an app exposes little accessibility information.
- Automated skill selection is a model capability, not an OS guarantee. The runtime guarantees the session behavior when called. The agent still needs to choose the tool, inspect results, export outputs, and choose whether the user needs the app left open.
- Rootful Xwayland and Hyprland Lua behavior are version-sensitive. Other compositor versions and GPU drivers need their own validation. The default private desktop size is 1800 × 1000.

## Sources and alternatives

The design follows [Xwayland's rootful documentation](https://man.archlinux.org/man/extra/xorg-xwayland/Xwayland.1.en), [Olivier Fourdan's explanation](https://ofourdan.blogspot.com/2023/10/xwayland-rootful-part1.html), [Hyprland window rules](https://wiki.hypr.land/Configuring/Basics/Window-Rules/), and [AT-SPI's byte-length contract](https://gnome.pages.gitlab.gnome.org/gtk/atspi2/method.EditableText.insert_text.html). See also [Cua's capture/delivery distinction](https://cua.ai/docs/concepts/capture-and-delivery-modalities), [its limits](https://cua.ai/docs/reference/cua-driver/limits), and the [native Hyprland experiment](https://github.com/trycua/cua/pull/3572).

The upstream Cua project and its contributors retain their original credit and licenses. This fork carries the upstream history and adds the Linux changes described here. No native Hyprland plugin code is included in this integration.
