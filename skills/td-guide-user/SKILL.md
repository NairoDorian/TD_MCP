---
name: td-guide-user
description: Helping a person use TouchDesigner through TD_MCP - see what they are looking at (ui_state, screenshot), point at operators in their own editor (show_in_editor), show results in a window (open_viewer), and the privacy rules for screen captures.
---

# Guiding someone through TouchDesigner

The person sees TouchDesigner; you see it through tools. Close that gap before
you explain anything.

## 1. Know what they are looking at

- `ui_state` first: the network in their editor pane, the current and
  selected operators, the operator under their mouse, the window, the monitors.
  "The level1 you have selected..." beats "find the Level TOP".
- If they mention something on screen you cannot see, `screenshot` the
  relevant operator's viewer (`path=...`).

## 2. Point, don't describe

- The editor already follows your edits: every create/set/connect moves the
  person's network editor into that operator's network and selects it (the
  result's `view` says where). After `execute_python` it cannot know what you
  changed -- call `show_in_editor` yourself.

- `show_in_editor path=... message="..."` moves their network editor to the
  operator, selects and frames it, and puts your message in the status bar at
  the bottom of TouchDesigner. One call replaces "go into feedback_trails, it is
  the node on the right...".
- Keep messages short and about the next step ("this Level TOP fades the
  trails -- lower Brightness 1 for shorter trails").
- `open_viewer path=...` opens the operator's own window (what right-click >
  View... opens) so they can watch the result while you change parameters;
  `open_viewer close=true` closes it -- always, once that step is done.

## 3. Check what they see

- `screenshot path=...` opens that viewer, captures the window, and closes it
  again unless `keep_open=true`. Use it to confirm the picture they are looking
  at -- layout, framing, "is it on screen".
- Judge colours and content from `capture_viewport` (the operator's pixels): a
  viewer can display an image brighter or in a different mode than its data
  (verified on 2025.33230: a colourful TOP looked white/grey in its viewer).

## 4. Screen captures and privacy

- Every screenshot is a capture of a screen REGION: menus and windows on top
  of it are in the picture.
- `target='td'`, `'desktop'` or `'monitor:N'` show other applications whenever
  TouchDesigner is not in front, so they need `allow_other_apps=true`. Set it
  only when the person has asked you to look at their screen and has brought
  TouchDesigner forward. If a capture shows another application anyway, do not
  read it out or act on it; say what you can see of TouchDesigner only.
- Never write screenshots anywhere the person did not ask for.

## 5. Close what you opened, and finish cleanly

- Close every viewer window you opened as soon as its step is done --
  `screenshot` does this by default. A window that disappears tells the person
  you are finished with it. Leave one open only when they asked to keep
  watching, and say so.
- When the whole job is done, save the project and close TouchDesigner if you
  opened it for the work.
- `show_in_editor` changes their view and selection: tell them where you took
  them.
