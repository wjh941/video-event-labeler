# Folder Picker Error Detection

## Goal

Prevent the native folder-picker action from waiting for minutes when Windows starts the dialog process but does not expose a visible, interactive window.

## Root Cause

The current Windows implementation waits synchronously for `powershell.exe` for up to 300 seconds. In the affected runtime environment, the process remains alive with no visible top-level window (`MainWindowHandle = 0`). The HTTP server remains healthy, but the import request and browser status remain pending because the child process never returns.

## Backend Behavior

- Start the Windows picker with `subprocess.Popen` so its startup state can be observed.
- For the first 3 seconds, poll for either process exit or a visible top-level window owned by the picker process.
- Detect visible windows using Win32 `EnumWindows`, `GetWindowThreadProcessId`, and `IsWindowVisible` through `ctypes`.
- If the process exits during startup, parse its output exactly as the existing picker does.
- If no visible window appears within 3 seconds, terminate the picker process, collect its output, and raise a clear `ValueError` telling the user to use path import.
- Once a visible window is detected, allow up to 300 seconds for selection. On timeout, terminate the picker and return a distinct timeout error.
- Ensure termination is followed by a bounded wait so no new orphan picker process remains.
- Preserve the Tk fallback on non-Windows platforms.

## Browser Behavior

- Disable the native picker button while its request is pending.
- Restore the button in a `finally` block on success, cancellation, or error.
- Display the backend error in the existing status area.
- Keep the manual path input and `按路径导入` action available throughout the native picker request.
- Do not clear the manual path input on picker failure.

## Error Messages

- Invisible window: `系统文件夹选择器不可用，请在路径框中输入视频目录并点击“按路径导入”`
- Selection timeout: `文件夹选择超时，请重试或使用“按路径导入”`
- Process launch/failure: preserve the existing `folder picker is unavailable: ...` diagnostic prefix.

## Testing

- Simulate a picker process that stays alive without a visible window; assert it is terminated and the invisible-window error is raised.
- Simulate a visible picker that exits successfully; assert the selected path is returned.
- Simulate a visible picker that exceeds the selection timeout; assert termination and the timeout error.
- Verify the browser button is disabled before the request and restored in `finally`.
- Run the full unittest suite, Python compilation, embedded JavaScript syntax validation, and whitespace checks.
