# Self-Owned Windows Folder Picker

## Goal

Make the Windows folder picker open reliably when the HTTP server runs in the
background, without depending on whichever application currently owns the
global foreground window.

## Confirmed Root Cause

The picker currently calls `GetForegroundWindow()` and refuses to start when
the result is zero. That value is timing-dependent for a background HTTP
process, so a valid interactive Windows session can be rejected before
PowerShell is launched. When a valid Edge window handle is available, the same
PowerShell picker creates a visible window successfully.

## Chosen Design

PowerShell will create a tiny WinForms owner form for the lifetime of the
folder dialog. The owner is hidden from the taskbar, transparent, centered,
and topmost. It is shown and activated immediately before
`FolderBrowserDialog.ShowDialog($owner)`. The dialog therefore has a stable
native owner without relying on browser focus or a cross-process window
handle.

The Python process will no longer call `GetForegroundWindow()` or reject a
request because no foreground handle is available. Existing process startup
observation remains unchanged: Python must still see a visible window within
three seconds, otherwise it terminates and reaps PowerShell and returns the
manual-path fallback error. The five-minute selection timeout and its cleanup
also remain unchanged.

The owner form and dialog are disposed in PowerShell after selection or
cancellation. No dependency is added; the implementation continues to use
Python and Windows standard components only.

## Request Flow

1. The browser disables only the native-picker button and posts to
   `/api/import-folder`.
2. Python starts an STA PowerShell child process.
3. PowerShell creates and activates its own topmost owner form.
4. PowerShell opens the folder dialog with that owner.
5. Python observes the visible window, then waits for selection or timeout.
6. PowerShell prints the selected path, or prints nothing on cancellation, and
   disposes both native objects.
7. Python imports the selected directory or returns the existing fallback
   error while the manual path control remains usable.

## Tests

- With no foreground window handle, the picker still launches.
- The generated PowerShell uses a self-owned topmost form and passes it to
  `ShowDialog`.
- Existing invisible-window termination, successful selection, selection
  timeout, HTTP concurrency, and browser button-restoration tests remain
  green.
- A bounded real-Windows diagnostic confirms that a visible picker window is
  created and then cleaned up.
- Run the complete unit suite, Python compilation, embedded JavaScript syntax
  validation, whitespace checks, and a live HTTP status check before pushing.

## Rejected Alternatives

- Retrying `GetForegroundWindow()` keeps the same global focus race.
- A separate desktop launcher would provide stronger ownership but adds a new
  distribution and startup path that is not required for this fix.
- Browser directory upload cannot provide an absolute local path and would
  require redesigning video access and serving.
