# Main-Thread Folder Picker Broker

## Goal

Keep the one-click native folder picker while removing the background
PowerShell dialog path that can lose its window and leave an HTTP request
blocked for five minutes.

## Confirmed Failure Mode

The HTTP handler currently starts an STA PowerShell child process and waits for
`FolderBrowserDialog.ShowDialog()`. Live inspection confirmed that the dialog
can initially be visible, topmost, on-screen, and not DWM-cloaked, then lose
all top-level windows while PowerShell remains blocked in `ShowDialog()`.
Startup visibility polling cannot make this modal child-process lifecycle
reliable.

The earlier Tk error, `main thread is not in main loop`, came from invoking Tk
inside an HTTP worker thread. Tk itself remains appropriate when its root,
event loop, and dialog all stay on the Python main thread.

## Architecture

The Python main thread will own a hidden Tk root and run `mainloop()`. The
`ThreadingHTTPServer` will run in one background thread. A
`TkFolderPickerBroker` will bridge HTTP worker threads to the Tk main thread
through an in-process request queue.

The broker is injected into `LabelerHTTPServer`; request handlers do not call a
global picker implementation. Manual path imports bypass the broker and keep
their current behavior.

No subprocess, PowerShell, Win32 window polling, or new dependency remains in
the picker path.

## Components

### Picker Request

A small request object contains a completion event plus `result` and `error`
fields. The HTTP worker creates the request, places it on the broker queue, and
waits for completion. The Tk main thread fills exactly one outcome and signals
the event in a `finally` block.

### TkFolderPickerBroker

The broker is created on the Python main thread with an injected Tk root and
directory chooser. Production uses `tkinter.filedialog.askdirectory`; tests
use fakes without opening a desktop window.

Responsibilities:

- Poll the request queue with `root.after`.
- Call the directory chooser only from the thread that created the broker.
- Parent the native dialog to the hidden root and request topmost placement.
- Convert a selected string to `Path`, and return `None` on cancellation.
- Convert Tk errors into a clear `ValueError` that directs the user to manual
  path import.
- Reject a second concurrent picker request immediately with
  `文件夹选择器已打开，请先完成当前选择`.
- Mark pending requests unavailable during shutdown so no HTTP worker remains
  blocked.

Only the native picker is serialized. All other HTTP endpoints remain
concurrent through `ThreadingHTTPServer`.

### HTTP Server

`create_server` accepts a folder-picker callable and stores it on the server.
For an empty `/api/import-folder` request, the handler calls that injected
callable. A request containing `video_root` continues to import the supplied
path directly.

### Application Lifecycle

Startup order on desktop-capable systems:

1. Parse arguments and build `AppState`.
2. Create the Tk root and `TkFolderPickerBroker` on the Python main thread.
3. Create the HTTP server with `broker.choose`.
4. Start `server.serve_forever()` in one background thread.
5. Print the local URL and run the Tk main loop on the main thread.
6. On shutdown, close the broker, stop and join the HTTP thread, destroy the Tk
   root, and close the server socket.

If Tk cannot initialize, the HTTP server still starts. Its injected picker
callable fails immediately with a manual-path fallback message, while path
imports and all annotation features remain available.

## Browser Behavior

The existing browser control remains unchanged: the native picker button is
disabled while its own request is pending and restored in `finally`. Manual
path input remains enabled. While a visible native dialog is open, the status
continues to show `正在打开文件夹选择器...`; this is expected and ends as soon
as the user selects or cancels.

## Error Handling

- Concurrent request: fail immediately with
  `文件夹选择器已打开，请先完成当前选择`.
- User cancellation: preserve the existing `no video folder was selected`
  response.
- Tk initialization or chooser failure: fail immediately with
  `系统文件夹选择器不可用，请在路径框中输入视频目录并点击“按路径导入”`.
- Application shutdown: release every waiting request with a clear unavailable
  error before stopping the server.

No arbitrary selection timeout is used. A request waits until the
main-thread chooser returns. The dialog and request share one in-process
lifecycle, so there is no child process or pipe that can remain blocked after
the dialog is gone.

## Testing

- A worker-thread `choose()` request is completed by a fake main-thread queue
  poll, and the chooser records the broker creator thread ID.
- Selection returns a `Path`; cancellation returns `None`; chooser exceptions
  become the documented `ValueError`.
- A second concurrent picker request fails immediately without invoking a
  second chooser.
- Broker shutdown releases a waiting worker.
- HTTP path imports bypass the broker, while empty imports use it.
- `/api/status` remains responsive while a picker request is waiting.
- Application lifecycle tests verify that the server runs off the Tk main
  thread and is stopped and joined during cleanup.
- Existing unit tests, Python compilation, embedded JavaScript parsing, and
  whitespace checks remain green.
- A bounded Windows diagnostic opens the real Tk directory dialog, closes it
  programmatically, and verifies that the broker request returns without a
  child process.

## Out Of Scope

- Browser directory upload and video copying.
- A tray application or separate GUI executable.
- Changes to CSV schemas, annotation behavior, or manual path import.
