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
     reports the job finished (or errored).
  4. find_output_file() -- walks the history entry's outputs looking for a
     file reference, resolves it to an absolute path under output_dir, and
     confirms it actually exists on disk (this requires COMFYUI_OUTPUT_DIR
     to point at the same directory ComfyUI itself writes into -- a shared
     filesystem, since both run on the same machine).

Deliberately stdlib-only (urllib, not requests) so this doesn't add a new
pip dependency to a project that otherwise has none.
"""
import json
import os
import time
import uuid
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError


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

    def wait_for_completion(self, prompt_id, should_cancel=None):
        """Polls /history/{prompt_id} until the job shows up as finished.

        should_cancel: optional zero-arg callable; if it returns True, the
        wait raises ComfyClientError right away. This does NOT interrupt the
        actual ComfyUI job -- call interrupt() separately for that.
        """
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            if should_cancel and should_cancel():
                raise ComfyClientError("cancelled")
            if time.monotonic() > deadline:
                raise ComfyClientError(f"timed out after {self.timeout_seconds:.0f}s waiting for prompt {prompt_id}")

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
            time.sleep(self.poll_interval)

    def find_output_file(self, history_entry):
        """Walks the history entry's 'outputs' looking for any node output
        entry with filename/subfolder keys (covers SaveVideo, SaveImage, VHS
        combine nodes, etc. regardless of the specific key they file under),
        resolves it to an absolute path under output_dir, and confirms it
        exists on disk."""
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

        for c in candidates:
            path = os.path.join(self.output_dir, c.get("subfolder", ""), c["filename"])
            if os.path.isfile(path):
                return path

        if candidates:
            attempted = [
                os.path.join(self.output_dir, c.get("subfolder", ""), c["filename"])
                for c in candidates
            ]
            raise ComfyClientError(
                f"ComfyUI reported output file(s) but none were found at the path(s) "
                f"Scene Forge checked: {attempted} -- check the COMFYUI_OUTPUT_DIR setting, "
                f"or that ComfyUI is writing to the same folder Scene Forge is reading from"
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
