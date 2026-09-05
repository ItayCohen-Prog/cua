-- Only the reserved display range used by background.py, never normal Xwayland apps.
-- All root windows stage off-screen before the manager moves its verified PID.
o.window({ initial_title = "^Xwayland on :62[0-9]{3}$" }, {
  workspace = "special:cua-staging silent",
  no_initial_focus = true,
  no_focus = true,
  no_follow_mouse = true,
  focus_on_activate = false,
  suppress_event = "activate activatefocus",
  render_unfocused = true,
  float = true,
  size = { 1800, 1000 },
  min_size = { 1800, 1000 },
  no_max_size = true,
  no_anim = true,
  no_shortcuts_inhibit = true,
})
