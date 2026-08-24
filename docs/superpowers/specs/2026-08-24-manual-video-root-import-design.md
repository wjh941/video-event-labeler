# Manual Video Root Import

## Goal

Let users import a video directory even when the operating system folder dialog is unavailable or invisible. The browser UI must provide a direct path-based fallback without changing the existing manifest generation and validation behavior.

## Scope

- Add a text input for a local video directory path.
- Add a path-import action that sends the entered path to the backend.
- Reuse `import_video_directory` and the existing `AppState` update flow.
- Keep the native folder picker as an optional action.
- Keep the HTTP server responsive while either import action is running.
- Return clear errors for an empty path, a missing/non-directory path, scan failures, and permission failures.

## Interface and Data Flow

1. The user enters or pastes a directory path and clicks the path-import action.
2. The browser sends JSON to `POST /api/import-folder` with `{ "video_root": "..." }`.
3. The backend resolves and validates the directory, then calls `import_video_directory`.
4. The backend updates `csv_path`, `video_root`, encoding, and the in-memory snapshot under the existing state lock.
5. The response returns the normal status payload; the browser reloads rows and displays the import result.

An empty JSON body continues to invoke the optional native picker for backwards compatibility. The path payload is the primary reliable workflow on Windows.

## Validation and Errors

- Trim surrounding whitespace before validation.
- Reject an empty path with a user-facing message.
- Reject paths that do not exist or are not directories.
- Preserve existing import errors for unsupported CSV data, inaccessible files, and scan failures.
- Do not mutate `AppState` until directory import and manifest loading succeed.

## Testing

- Verify the HTML exposes the path input and action.
- Verify a valid path payload imports into a fresh state.
- Verify empty, missing, and file paths return HTTP 400 without changing state.
- Verify the native picker remains available for an empty request body.
- Run the complete unittest suite, Python compilation, and diff checks.
