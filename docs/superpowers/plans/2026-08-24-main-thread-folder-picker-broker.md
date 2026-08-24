# Main-Thread Folder Picker Broker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the one-click native folder picker reliable by running Tk and its dialog on Python's main thread while keeping all HTTP endpoints responsive.

**Architecture:** A `TkFolderPickerBroker` owns a queue of picker requests and is polled by a hidden Tk root on the main thread. `ThreadingHTTPServer` runs in a background thread and receives the broker's `choose` callable through constructor injection; manual path imports bypass it. If Tk cannot initialize, the server still starts with an immediately failing picker callable.

**Tech Stack:** Python 3 standard library (`tkinter`, `queue`, `threading`, `http.server`), `unittest`, Windows native Tk dialog for the final diagnostic.

## Global Constraints

- Preserve the current `/api/import-folder` contract and manual `video_root` path workflow.
- Do not add dependencies or retain PowerShell, subprocess, Win32 polling, or picker timeouts in production code.
- Keep Tk root creation, `mainloop()`, and chooser calls on the same main thread.
- Keep `ThreadingHTTPServer` so status, video, and annotation requests remain responsive while the dialog is open.
- Treat cancellation as `None`; translate picker failures to the documented Chinese manual-path fallback.
- Run every red test before implementation and every focused green test after implementation.

---

### Task 1: Replace the child-process picker with a main-thread broker

**Files:**
- Modify: `video_event_labeler.py:10-90`
- Modify: `video_event_labeler.py:765-900`
- Modify: `test_video_event_labeler.py:1-15`
- Replace: `test_video_event_labeler.py:775-918`

- [x] **Step 1: Write failing broker tests**

Replace the PowerShell-specific `FolderPickerDispatchTests` with fake-root tests that cover selection, cancellation, chooser failure, thread ownership, concurrent calls, and shutdown:

```python
class FakeTkRoot:
    def __init__(self):
        self.callbacks = []

    def after(self, _delay_ms, callback):
        self.callbacks.append(callback)

    def run_next(self):
        self.callbacks.pop(0)()


class FolderPickerBrokerTests(unittest.TestCase):
    def setUp(self):
        self.root = FakeTkRoot()

    def choose_in_worker(self, broker):
        outcome = {}
        finished = threading.Event()

        def choose():
            try:
                outcome["result"] = broker.choose()
            except Exception as error:
                outcome["error"] = error
            finally:
                finished.set()

        thread = threading.Thread(target=choose)
        thread.start()
        return outcome, finished, thread

    def wait_for_queued_request(self, broker):
        for _ in range(100):
            if not broker.requests.empty():
                return
            threading.Event().wait(0.01)
        self.fail("picker request was not queued")

    def test_choose_runs_chooser_on_creator_thread_and_returns_path(self):
        chooser_threads = []

        def chooser(**kwargs):
            chooser_threads.append(threading.get_ident())
            self.assertIs(kwargs["parent"], self.root)
            return "D:/videos"

        broker = labeler.TkFolderPickerBroker(self.root, chooser)
        outcome, finished, thread = self.choose_in_worker(broker)
        self.wait_for_queued_request(broker)
        self.root.run_next()
        self.assertTrue(finished.wait(1))
        thread.join(1)
        self.assertEqual(outcome["result"], Path("D:/videos"))
        self.assertEqual(chooser_threads, [threading.get_ident()])

    def test_cancel_returns_none(self):
        broker = labeler.TkFolderPickerBroker(self.root, lambda **_: "")
        outcome, finished, thread = self.choose_in_worker(broker)
        self.wait_for_queued_request(broker)
        self.root.run_next()
        self.assertTrue(finished.wait(1))
        thread.join(1)
        self.assertIsNone(outcome["result"])

    def test_chooser_error_points_to_manual_path_import(self):
        def fail(**_):
            raise RuntimeError("desktop unavailable")

        broker = labeler.TkFolderPickerBroker(self.root, fail)
        outcome, finished, thread = self.choose_in_worker(broker)
        self.wait_for_queued_request(broker)
        self.root.run_next()
        self.assertTrue(finished.wait(1))
        thread.join(1)
        self.assertRegex(str(outcome["error"]), "系统文件夹选择器不可用.*按路径导入")

    def test_second_concurrent_request_fails_immediately(self):
        broker = labeler.TkFolderPickerBroker(self.root, lambda **_: "")
        first, first_done, first_thread = self.choose_in_worker(broker)
        self.wait_for_queued_request(broker)
        second, second_done, second_thread = self.choose_in_worker(broker)
        self.assertTrue(second_done.wait(1))
        self.assertRegex(str(second["error"]), "文件夹选择器已打开")
        self.root.run_next()
        self.assertTrue(first_done.wait(1))
        first_thread.join(1)
        second_thread.join(1)

    def test_close_releases_waiting_request(self):
        broker = labeler.TkFolderPickerBroker(self.root, lambda **_: "")
        outcome, finished, thread = self.choose_in_worker(broker)
        self.wait_for_queued_request(broker)
        broker.close()
        self.assertTrue(finished.wait(1))
        thread.join(1)
        self.assertRegex(str(outcome["error"]), "系统文件夹选择器不可用")
```

- [x] **Step 2: Run the focused tests and confirm they fail**

Run:

```powershell
python -m unittest test_video_event_labeler.FolderPickerBrokerTests -v
```

Expected: `ERROR` because `TkFolderPickerBroker` does not exist yet.

- [x] **Step 3: Implement the broker and remove the PowerShell path**

Remove `ctypes`, `subprocess`, `time`, picker timeout constants, `_choose_video_root_tk`, `_process_has_visible_window`, `_terminate_picker_process`, and `choose_video_root`. Add `queue` and these broker types near the former picker code:

```python
PICKER_UNAVAILABLE_MESSAGE = (
    "系统文件夹选择器不可用，请在路径框中输入视频目录并点击“按路径导入”"
)


@dataclass
class PickerRequest:
    done: threading.Event = field(default_factory=threading.Event)
    result: Path | None = None
    error: BaseException | None = None


class TkFolderPickerBroker:
    def __init__(self, root: object, chooser: object, poll_interval_ms: int = 25):
        self.root = root
        self.chooser = chooser
        self.poll_interval_ms = poll_interval_ms
        self.owner_thread_id = threading.get_ident()
        self.requests: queue.Queue[PickerRequest] = queue.Queue()
        self.request_lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.closed = False
        self.pending: PickerRequest | None = None
        self.root.after(self.poll_interval_ms, self._poll)

    def choose(self) -> Path | None:
        if not self.request_lock.acquire(blocking=False):
            raise ValueError("文件夹选择器已打开，请先完成当前选择")
        request = PickerRequest()
        try:
            with self.state_lock:
                if self.closed:
                    raise ValueError(PICKER_UNAVAILABLE_MESSAGE)
                self.pending = request
                self.requests.put(request)
            request.done.wait()
            if request.error is not None:
                raise ValueError(PICKER_UNAVAILABLE_MESSAGE) from request.error
            return request.result
        finally:
            self.request_lock.release()

    def _poll(self) -> None:
        if threading.get_ident() != self.owner_thread_id:
            raise RuntimeError("folder picker broker must be polled on its creator thread")
        try:
            request = self.requests.get_nowait()
        except queue.Empty:
            request = None
        if request is not None and not request.done.is_set():
            try:
                selected = self.chooser(parent=self.root, title="选择视频文件夹")
                result = Path(selected) if selected else None
                picker_error = None
            except Exception as error:
                result = None
                picker_error = error
            with self.state_lock:
                if not request.done.is_set():
                    request.result = result
                    request.error = picker_error
                    request.done.set()
                if self.pending is request:
                    self.pending = None
        with self.state_lock:
            should_poll = not self.closed
        if should_poll:
            self.root.after(self.poll_interval_ms, self._poll)

    def close(self) -> None:
        with self.state_lock:
            self.closed = True
            request = self.pending
            self.pending = None
            if request is not None and not request.done.is_set():
                request.error = RuntimeError("folder picker broker is closed")
                request.done.set()
```

Use `Callable[..., str]` for the chooser annotation. Keep `request_lock` held for the whole request lifetime so a second request fails immediately; use `state_lock` to make queue registration and `close()` atomic. Catch `Exception`, not `BaseException`, in the final implementation so `KeyboardInterrupt` and `SystemExit` are not hidden.

- [x] **Step 4: Run the broker tests and the import/static checks**

Run:

```powershell
python -m unittest test_video_event_labeler.FolderPickerBrokerTests -v
python -m py_compile video_event_labeler.py test_video_event_labeler.py
rg -n "powershell|subprocess|ctypes|PICKER_.*TIMEOUT|choose_video_root" video_event_labeler.py
```

Expected: broker tests pass, compilation succeeds, and `rg` prints no matches.

- [x] **Step 5: Commit the broker core**

```powershell
git add video_event_labeler.py test_video_event_labeler.py
git commit -m "refactor: broker folder picker on main thread"
```

---

### Task 2: Inject the picker into the HTTP server

**Files:**
- Modify: `video_event_labeler.py:1189-1357`
- Modify: `test_video_event_labeler.py:440-535`
- Modify: `test_video_event_labeler.py:918-975`

- [x] **Step 1: Write failing HTTP injection tests**

Update `ApiTests.setUp` to create a picker spy and inject it:

```python
self.picker_calls = 0

def picker():
    self.picker_calls += 1
    return None

self.server = labeler.create_server(self.state, folder_picker=picker)
```

Replace patches of the removed global `choose_video_root` with assertions on the injected picker. Add:

```python
def test_import_folder_from_payload_bypasses_picker(self):
    import_root = self.root / "new-video-root"
    video = import_root / "pos" / "fall-pos-002.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"new video bytes")

    status, body = self.post_import({"video_root": str(import_root)})

    self.assertEqual((status, body["ok"]), (200, True))
    self.assertEqual(self.picker_calls, 0)

def test_empty_import_uses_injected_picker(self):
    status, body = self.post_import({})

    self.assertEqual((status, body["ok"]), (400, False))
    self.assertEqual(body["error"], "no video folder was selected")
    self.assertEqual(self.picker_calls, 1)
```

Update the responsiveness test to pass its `blocking_picker` directly to `create_server` instead of patching module state.

- [x] **Step 2: Run the focused tests and confirm they fail**

Run:

```powershell
python -m unittest test_video_event_labeler.ApiTests.test_import_folder_from_payload_bypasses_picker test_video_event_labeler.ApiTests.test_empty_import_uses_injected_picker test_video_event_labeler.FolderPickerBrokerTests.test_import_picker_does_not_block_other_http_requests -v
```

Expected: `TypeError` because `create_server` does not yet accept `folder_picker`.

- [x] **Step 3: Store and use the injected callable**

Change the server construction path:

```python
class LabelerHTTPServer(ThreadingHTTPServer):
    def __init__(self, address, state, folder_picker):
        super().__init__(address, Handler)
        self.state = state
        self.folder_picker = folder_picker


def create_server(state: AppState, port: int = 0, folder_picker=None) -> LabelerHTTPServer:
    if folder_picker is None:
        folder_picker = unavailable_folder_picker
    return LabelerHTTPServer(("127.0.0.1", port), state, folder_picker)
```

Add the fallback and switch the handler:

```python
def unavailable_folder_picker() -> Path | None:
    raise ValueError(PICKER_UNAVAILABLE_MESSAGE)

# In Handler.do_POST:
root = self.server.folder_picker()
```

The fallback default preserves simple test/server construction while failing quickly when no GUI runtime is attached.

- [x] **Step 4: Run focused and complete HTTP tests**

Run:

```powershell
python -m unittest test_video_event_labeler.ApiTests test_video_event_labeler.FolderPickerBrokerTests -v
```

Expected: all tests pass, including `/api/status` responsiveness while the injected picker blocks.

- [x] **Step 5: Commit HTTP injection**

```powershell
git add video_event_labeler.py test_video_event_labeler.py
git commit -m "refactor: inject folder picker into server"
```

---

### Task 3: Move the HTTP server off the Tk main thread

**Files:**
- Modify: `video_event_labeler.py:1355-1410`
- Append: `test_video_event_labeler.py` before `HtmlContractTests`

- [ ] **Step 1: Write failing lifecycle tests with fakes**

Add a fake root and server that capture thread IDs and cleanup calls:

```python
class ApplicationLifecycleTests(unittest.TestCase):
    class FakeRoot:
        def __init__(self):
            self.main_thread_id = None
            self.destroyed = False

        def mainloop(self):
            self.main_thread_id = threading.get_ident()

        def destroy(self):
            self.destroyed = True

    class FakeBroker:
        def __init__(self):
            self.closed = False

        def choose(self):
            return None

        def close(self):
            self.closed = True

    class FakeServer:
        server_port = 8765

        def __init__(self):
            self.serve_thread_id = None
            self.shutdown_called = False
            self.closed = False

        def serve_forever(self):
            self.serve_thread_id = threading.get_ident()

        def shutdown(self):
            self.shutdown_called = True

        def server_close(self):
            self.closed = True

    def test_run_desktop_app_uses_main_thread_for_tk_and_cleans_up(self):
        root = self.FakeRoot()
        broker = self.FakeBroker()
        server = self.FakeServer()

        labeler.run_desktop_app(labeler.AppState(), 8765, root, broker, server=server)

        self.assertEqual(root.main_thread_id, threading.get_ident())
        self.assertNotEqual(server.serve_thread_id, threading.get_ident())
        self.assertTrue(broker.closed)
        self.assertTrue(server.shutdown_called)
        self.assertTrue(server.closed)
        self.assertTrue(root.destroyed)
```

Also test Tk startup failure through a small factory seam:

```python
def test_main_falls_back_to_http_when_tk_initialization_fails(self):
    with (
        patch.object(labeler, "create_tk_picker", side_effect=RuntimeError("no desktop")),
        patch.object(labeler, "run_headless_app") as run_headless,
        patch.object(labeler, "build_state_from_args", return_value=labeler.AppState()),
        patch.object(labeler, "build_parser") as build_parser,
    ):
        build_parser.return_value.parse_args.return_value = argparse.Namespace(
            video_root=None, csv=None, port=8765
        )
        labeler.main()
    run_headless.assert_called_once()
```

- [ ] **Step 2: Run lifecycle tests and confirm they fail**

Run:

```powershell
python -m unittest test_video_event_labeler.ApplicationLifecycleTests -v
```

Expected: `AttributeError` because the lifecycle helpers do not exist.

- [ ] **Step 3: Implement Tk creation and explicit lifecycle helpers**

Add:

```python
def create_tk_picker() -> tuple[object, TkFolderPickerBroker]:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    return root, TkFolderPickerBroker(root, filedialog.askdirectory)


def run_desktop_app(state, port, root, broker, server=None) -> None:
    server = server or create_server(state, port, broker.choose)
    server_thread = threading.Thread(target=server.serve_forever, name="labeler-http")
    server_thread.start()
    print_startup(server, state)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        broker.close()
        server.shutdown()
        server_thread.join()
        root.destroy()
        server.server_close()


def run_headless_app(state, port) -> None:
    server = create_server(state, port, unavailable_folder_picker)
    print_startup(server, state)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
```

Extract current console output to `print_startup(server, state)`. Update `main()` to call `create_tk_picker()` and `run_desktop_app`; if Tk initialization raises, print the picker fallback message and call `run_headless_app`. Keep `root.destroy()` guarded if Tk construction can fail after creating a partial root.

In `run_desktop_app`, use a bounded `join(timeout=5)` followed by a clear `RuntimeError` if the HTTP thread remains alive; tests must not permit a silent orphan thread.

- [ ] **Step 4: Run lifecycle, picker, and HTTP tests**

Run:

```powershell
python -m unittest test_video_event_labeler.ApplicationLifecycleTests test_video_event_labeler.FolderPickerBrokerTests test_video_event_labeler.ApiTests -v
```

Expected: all focused suites pass and the recorded Tk thread differs from the HTTP thread.

- [ ] **Step 5: Commit the lifecycle change**

```powershell
git add video_event_labeler.py test_video_event_labeler.py
git commit -m "fix: run native folder picker on main thread"
```

---

### Task 4: Complete regression and real Windows verification

**Files:**
- Verify: `video_event_labeler.py`
- Verify: `test_video_event_labeler.py`
- Verify: `docs/superpowers/specs/2026-08-24-main-thread-folder-picker-broker-design.md`

- [ ] **Step 1: Run the complete automated suite**

Run:

```powershell
python -m unittest -v
python -m py_compile video_event_labeler.py test_video_event_labeler.py
node --input-type=module -e "import {execFileSync} from 'child_process'; import vm from 'node:vm'; const html=execFileSync('python',['-c','import video_event_labeler; print(video_event_labeler.HTML)'],{encoding:'utf8'}); const scripts=html.split('<script>').slice(1).map(part=>part.split('</script>')[0]); for (const source of scripts) new vm.Script(source); console.log('scripts='+scripts.length)"
git diff --check
```

Expected: all tests pass, Python compiles, embedded JavaScript parses, and whitespace checks are clean.

- [ ] **Step 2: Prove the retired implementation is gone**

Run:

```powershell
rg -n "powershell|FolderBrowserDialog|subprocess|_process_has_visible_window|_terminate_picker_process|PICKER_SELECTION_TIMEOUT" video_event_labeler.py test_video_event_labeler.py
```

Expected: no matches.

- [ ] **Step 3: Run a bounded real Windows dialog diagnostic**

Start the updated application on a free local port, open `/api/import-folder`, and use a diagnostic-only Win32 helper to post `WM_CLOSE` to the Tk directory dialog after it appears. Verify all of the following within 10 seconds:

```text
dialog title: 选择视频文件夹
dialog process: the Python application PID
HTTP result: 400 / no video folder was selected
child powershell.exe processes: 0
GET /api/status after cancellation: 200
```

Stop the diagnostic application cleanly after the assertions. This helper stays outside production code and is not committed.

- [ ] **Step 4: Restart the user-facing session and smoke-test it**

Stop only the existing labeler process on port `8765`, start the updated `video_event_labeler.py --port 8765`, wait for `/api/status` to return `200`, and open `http://127.0.0.1:8765/` in the browser. Manually confirm that selection and cancellation both restore the Import button.

- [ ] **Step 5: Review the final diff and commit any verification-only test adjustments**

Run:

```powershell
git status --short
git diff --check
git diff --stat
```

If test-only adjustments were required, commit them:

```powershell
git add test_video_event_labeler.py
git commit -m "test: cover main-thread folder picker lifecycle"
```

- [ ] **Step 6: Push the verified commits**

Run:

```powershell
git push origin main
git status --short
```

Expected: push succeeds and the worktree is clean.
