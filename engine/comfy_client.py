"""
Thin client for talking to a locally-running ComfyUI instance.

Flow used by app.py's scene-run job:
  1. convert_to_api_format() -- POSTs the UI/"Save"-format workflow JSON
     that template_engine.py produces to ComfyUI's /workflow/convert
     endpoint (added by the community "Workflow to API Converter Endpoint"
     custom node: https://github.com/SethRobinson/comfyui-workflow-to-api-converter-endpoint)
     and gets back API-format JSON.
  2. queue_prompt() -- POSTs the API-format workflow to /prompt, gets a
     prompt_id back.
  3. wait_for_completion() -- polls /history/{prompt_id} until ComfyUI
     reports the job finished (or errored). If the optional websocket-client
     package is installed, also opens a persistent connection to ComfyUI's
     /ws endpoint (reusing the same client_id already sent with /prompt) and
     reports live sampler-step progress via the on_progress callback --
     ComfyUI only pushes step-by-step progress over that WebSocket, /history
     alone only ever shows done/not-done.
  4. find_output_file() -- walks the history entry's outputs looking for a
     file reference, resolves it to an absolute path under output_dir, and
     confirms it actually exists on disk (this requires COMFYUI_OUTPUT_DIR
     to point at the same directory ComfyUI itself writes into -- a shared
     filesystem, since both run on the same machine).

Deliberately stdlib-only for everything except live progress, which is the
one thing the standard library genuinely can't do (no WebSocket client) --
the optional dependency is websocket-client. Without it, or if it can't
connect, ComfyClient still works fully via REST polling alone; it just
won't have live per-step progress numbers.
"""
import json
import os
import time
import uuid
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

try:
    import websocket  # pip install websocket-client -- optional, see module docstring
    _HAS_WEBSOCKET = True
except ImportError:
    _HAS_WEBSOCKET = False


class ComfyClientError(Exception):
    """Raised for any failure talking to ComfyUI or locating its output."""


def _post_json(url, payload, timeout=30):
    data = json.dumps(payload).encode("utf-8")
    req = urlrequest.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise ComfyClientError(f"POST {url} failed ({e.code}): {body}") from e
    except URLError as e:
        raise ComfyClientError(f"could not reach ComfyUI at {url}: {e.reason}") from e

    if not raw.strip():
        # Some endpoints (e.g. /interrupt) return a success status with no
        # body at all -- that's not a failure, just nothing to parse.
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ComfyClientError(f"POST {url} returned a non-JSON response: {raw[:200]!r}") from e


def _get_json(url, timeout=30):
    try:
        with urlrequest.urlopen(url, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise ComfyClientError(f"GET {url} failed ({e.code}): {body}") from e
    except URLError as e:
        raise ComfyClientError(f"could not reach ComfyUI at {url}: {e.reason}") from e

    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ComfyClientError(f"GET {url} returned a non-JSON response: {raw[:200]!r}") from e


class ComfyClient:
    def __init__(self, base_url, output_dir, poll_interval=3.0, timeout_seconds=1800):
        self.base_url = base_url.rstrip("/")
        self.output_dir = output_dir
        self.poll_interval = poll_interval
        self.timeout_seconds = timeout_seconds
        self.client_id = str(uuid.uuid4())
        self._ws = None  # lazily connected on first wait_for_completion() call,
                          # reused across every sequence in a scene run

    def _ws_url(self):
        """http(s)://host:port -> ws(s)://host:port/ws?clientId=<self.client_id>
        -- same client_id already sent with every /prompt submission, which
        is what associates this WebSocket session with this client on
        ComfyUI's side."""
        scheme, _, rest = self.base_url.partition("://")
        ws_scheme = "wss" if scheme == "https" else "ws"
        return f"{ws_scheme}://{rest}/ws?clientId={self.client_id}"

    def _ensure_ws(self):
        """Returns a connected WebSocket, connecting lazily on first use.
        Returns None (never raises) if websocket-client isn't installed or
        the connection attempt fails -- callers fall back to REST-only
        polling with no live progress in that case."""
        if not _HAS_WEBSOCKET:
            return None
        if self._ws is not None:
            return self._ws
        try:
            ws = websocket.WebSocket()
            ws.connect(self._ws_url(), timeout=10)
            self._ws = ws
            return ws
        except Exception:
            return None

    def close(self):
        """Closes the WebSocket connection, if one was opened. Safe to call
        even if none was ever established."""
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None

    def convert_to_api_format(self, ui_workflow):
        """POSTs a UI/save-format workflow to /workflow/convert and returns
        the API-format workflow dict. Requires the Workflow to API Converter
        Endpoint custom node to be installed in the target ComfyUI."""
        result = _post_json(f"{self.base_url}/workflow/convert", ui_workflow)
        # Different versions of the converter node wrap the result under
        # slightly different keys -- handle the shapes actually seen.
        if isinstance(result, dict):
            if isinstance(result.get("prompt"), dict):
                return result["prompt"]
            if isinstance(result.get("output"), dict):
                return result["output"]
            return result
        raise ComfyClientError(f"unexpected response shape from /workflow/convert: {result!r}")

    def queue_prompt(self, api_workflow):
        """Submits an API-format workflow for execution. Returns prompt_id."""
        result = _post_json(f"{self.base_url}/prompt", {
            "prompt": api_workflow,
            "client_id": self.client_id,
        })
        prompt_id = result.get("prompt_id")
        if not prompt_id:
            raise ComfyClientError(f"/prompt did not return a prompt_id: {result!r}")
        return prompt_id

    def wait_for_completion(self, prompt_id, should_cancel=None, on_progress=None):
        """Waits for prompt_id to finish, confirmed via /history polling
        (the authoritative source for completion/error status). If a
        WebSocket connection is available, also listens for ComfyUI's
        'progress' messages scoped to this prompt_id and reports them via
        on_progress(value, max) -- e.g. sampler step 12 of 30. Falls back to
        pure REST polling with no progress callbacks if websocket-client
        isn't installed or the connection fails; completion detection is
        unaffected either way.

        should_cancel: optional zero-arg callable; if it returns True, the
        wait raises ComfyClientError right away. This does NOT interrupt the
        actual ComfyUI job -- call interrupt() separately for that.
        on_progress: optional callable(value, max) invoked whenever a
        'progress' WebSocket message for THIS prompt_id arrives.
        """
        ws = self._ensure_ws()

        deadline = time.monotonic() + self.timeout_seconds
        next_history_poll = 0.0
        while True:
            if should_cancel and should_cancel():
                raise ComfyClientError("cancelled")
            if time.monotonic() > deadline:
                raise ComfyClientError(f"timed out after {self.timeout_seconds:.0f}s waiting for prompt {prompt_id}")

            if ws is not None:
                try:
                    ws.settimeout(min(self.poll_interval, 2.0))
                    raw = ws.recv()
                    if raw:
                        message = json.loads(raw)
                        data = message.get("data", {}) or {}
                        if data.get("prompt_id") == prompt_id and message.get("type") == "progress" and on_progress:
                            on_progress(data.get("value"), data.get("max"))
                except json.JSONDecodeError:
                    pass  # binary/preview frame or malformed message -- ignore, not fatal
                except Exception:
                    # connection dropped or timed out waiting for a message --
                    # either way, fall through to the REST poll below and,
                    # on a real drop, stop trying the WS for the rest of this wait
                    if ws.connected is False:
                        self.close()
                        ws = None

            if time.monotonic() >= next_history_poll:
                next_history_poll = time.monotonic() + self.poll_interval
                history = _get_json(f"{self.base_url}/history/{prompt_id}")
                entry = history.get(prompt_id)
                if entry:
                    status = entry.get("status", {})
                    if status.get("completed") is True or status.get("status_str") == "success":
                        return entry
                    if status.get("status_str") == "error":
                        raise ComfyClientError(
                            f"ComfyUI reported an error for prompt {prompt_id}: {status.get('messages', [])}"
                        )

    def find_output_file(self, history_entry, grace_seconds=15, grace_poll_interval=1.0):
        """Walks the history entry's 'outputs' looking for any node output
        entry with filename/subfolder keys (covers SaveVideo, SaveImage, VHS
        combine nodes, etc. regardless of the specific key they file under),
        resolves it to an absolute path under output_dir, and confirms it
        exists on disk.

        ComfyUI can mark a prompt "completed" in history slightly before a
        video file is fully muxed/flushed to disk, so a file that isn't
        there on the very first check might still just be finishing -- this
        retries for up to grace_seconds before giving up, rather than
        failing on what may just be a beat of write latency."""
        outputs = history_entry.get("outputs", {})
        candidates = []

        def _walk(node):
            if isinstance(node, dict):
                if "filename" in node and "subfolder" in node:
                    candidates.append(node)
                else:
                    for v in node.values():
                        _walk(v)
            elif isinstance(node, list):
                for v in node:
                    _walk(v)

        _walk(outputs)
        # prefer "output"-type entries over "temp"/"input" previews
        candidates.sort(key=lambda c: 0 if c.get("type", "output") == "output" else 1)

        deadline = time.monotonic() + grace_seconds
        while True:
            for c in candidates:
                path = os.path.join(self.output_dir, c.get("subfolder", ""), c["filename"])
                if os.path.isfile(path):
                    return path
            if not candidates or time.monotonic() >= deadline:
                break
            time.sleep(grace_poll_interval)

        if candidates:
            attempted = [
                {
                    "raw_subfolder": c.get("subfolder", ""),
                    "raw_filename": c.get("filename"),
                    "raw_type": c.get("type", "output"),
                    "checked_path": os.path.join(self.output_dir, c.get("subfolder", ""), c["filename"]),
                }
                for c in candidates
            ]
            raise ComfyClientError(
                f"ComfyUI reported output file(s) but none appeared on disk within "
                f"{grace_seconds}s of completion. output_dir={self.output_dir!r}. What "
                f"ComfyUI's history reported, and the exact path checked for each: {attempted} "
                f"-- check the COMFYUI_OUTPUT_DIR setting, or that ComfyUI is writing to the "
                f"same folder H3SceneManager is reading from"
            )
        raise ComfyClientError("ComfyUI history entry contained no output file references")

    def interrupt(self):
        """Best-effort: ask ComfyUI to stop whatever it's currently running.
        Called from error-handling paths, so this must never itself raise --
        catches broadly, not just ComfyClientError."""
        try:
            _post_json(f"{self.base_url}/interrupt", {})
        except Exception:
            pass
