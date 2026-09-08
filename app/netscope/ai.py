"""AI investigation: hand a device (or the whole network) to an AI and stream
the report back. Developed for and by frig.

Two kinds of engine:
  - a cloud CLI already installed and logged in (claude, then gemini, codex);
    the plugin only invokes it, never touches its config or credentials.
  - a local Ollama model over http://localhost:11434, so a private
    investigation never leaves your machine.
"""

from __future__ import annotations

import json
import os
import queue
import signal
from collections import deque
import os
import shutil
import subprocess
import threading
import urllib.error
import urllib.request
from typing import Callable, Optional

from . import recon
from .recon import Dossier


# --------------------------------------------------------------------------- #
# cloud CLI engines
# --------------------------------------------------------------------------- #

def _claude_cmd() -> list[str]:
    return ["claude", "-p", "--output-format", "stream-json", "--verbose",
            "--include-partial-messages"]


def _gemini_cmd() -> list[str]:
    return ["gemini", "-o", "stream-json"]


def _codex_cmd() -> list[str]:
    return ["codex", "exec", "--skip-git-repo-check", "-s", "read-only", "-"]


CLI_BACKENDS = [
    ("claude", _claude_cmd),
    ("gemini", _gemini_cmd),
    ("codex", _codex_cmd),
]
_CLI = dict(CLI_BACKENDS)


# --------------------------------------------------------------------------- #
# local Ollama engine
# --------------------------------------------------------------------------- #

def ollama_host() -> str:
    return os.environ.get("NETSCOPE_OLLAMA_HOST", "http://localhost:11434").rstrip("/")


def ollama_available() -> bool:
    try:
        with urllib.request.urlopen(ollama_host() + "/api/tags", timeout=1.5) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def ollama_models() -> list[str]:
    try:
        with urllib.request.urlopen(ollama_host() + "/api/tags", timeout=2) as r:
            data = json.loads(r.read())
        return [m.get("name", "") for m in data.get("models", []) if m.get("name")]
    except (urllib.error.URLError, OSError, ValueError):
        return []


def ollama_model() -> str:
    env = os.environ.get("NETSCOPE_OLLAMA_MODEL")
    if env:
        return env
    models = ollama_models()
    return models[0] if models else "llama3.2"


# --------------------------------------------------------------------------- #
# engine discovery
# --------------------------------------------------------------------------- #

def cloud_backend() -> Optional[str]:
    for name, _ in CLI_BACKENDS:
        if shutil.which(name):
            return name
    return None


def available_backend() -> Optional[str]:
    """Default engine: a cloud CLI if present, else local Ollama."""
    return cloud_backend() or ("ollama" if ollama_available() else None)


def engines() -> list[tuple[str, str]]:
    """(id, label) for every engine available right now, for a selector."""
    out = []
    for name, _ in CLI_BACKENDS:
        if shutil.which(name):
            out.append((name, name + "  (cloud)"))
    if ollama_available():
        out.append(("ollama", f"ollama · {ollama_model()}  (local)"))
    return out


# --------------------------------------------------------------------------- #
# streaming
# --------------------------------------------------------------------------- #

def _extract_claude_delta(obj: dict) -> str:
    if obj.get("type") == "stream_event":
        ev = obj.get("event", {})
        if ev.get("type") == "content_block_delta":
            delta = ev.get("delta", {})
            if delta.get("type") == "text_delta":
                return delta.get("text", "")
    return ""


def _extract_gemini_delta(obj: dict) -> str:
    # an echoed user/system turn is not report text
    if obj.get("role") in ("user", "system"):
        return ""
    if obj.get("type") in ("content", "assistant", "message"):
        c = obj.get("text") or obj.get("content") or ""
        if isinstance(c, str):
            return c
    return ""


def _kill_group(proc, sig) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), sig)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill() if sig == signal.SIGKILL else proc.terminate()
        except OSError:
            pass


def _stream_cli(backend: str, prompt: str, on_delta, on_done, stop) -> None:
    cmd = _CLI[backend]()
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1,
            start_new_session=True,   # own process group, so STOP can kill descendants
        )
    except OSError as e:
        on_done("", f"could not launch {backend}: {e}")
        return

    collected: list[str] = []
    plain = backend == "codex"
    lines = queue.Queue()
    errors = deque(maxlen=8)
    error = ""

    def pump():
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
        except (OSError, ValueError):
            pass

    def read_stdout():
        try:
            for line in proc.stdout:
                lines.put(line)
        finally:
            lines.put(None)

    def read_stderr():
        while True:
            chunk = proc.stderr.read(1024)
            if not chunk:
                break
            errors.append(chunk)

    workers = [threading.Thread(target=fn, daemon=True)
               for fn in (pump, read_stdout, read_stderr)]
    for worker in workers:
        worker.start()

    try:
        while True:
            if stop and stop.is_set():
                error = "stopped"
                break
            try:
                line = lines.get(timeout=0.1)
            except queue.Empty:
                continue
            if line is None:
                # stdout may close before the process exits; keep STOP responsive.
                while proc.poll() is None:
                    if stop and stop.wait(0.1):
                        error = "stopped"
                        break
                    if not stop:
                        try:
                            proc.wait(timeout=0.1)
                        except subprocess.TimeoutExpired:
                            pass
                break
            line = line.rstrip("\n")
            if not line:
                continue
            if plain:
                collected.append(line + "\n")
                on_delta(line + "\n")
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if backend == "claude":
                text = _extract_claude_delta(obj)
                if not text and obj.get("type") == "result" and obj.get("is_error"):
                    error = str(obj.get("result") or "AI error")[:200]
                    break
            else:
                text = _extract_gemini_delta(obj)
            if text:
                collected.append(text)
                on_delta(text)
    except Exception as e:
        error = str(e)[:200]
    finally:
        # a CLI may spawn helpers that inherit the pipes; killing only the parent
        # leaves the reader blocked, so signal the whole group
        if proc.poll() is None:
            _kill_group(proc, signal.SIGTERM)
        try:
            code = proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            _kill_group(proc, signal.SIGKILL)
            try:
                code = proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                code = -9
        for worker in workers:
            worker.join(timeout=1)
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            stream.close()
    if code != 0 and not error:
        tail = "".join(errors).strip()[-200:]
        error = f"{backend} exited {code}: {tail}" if tail else f"{backend} exited {code}"
    on_done("".join(collected), error)


def _stream_ollama(prompt: str, on_delta, on_done, stop, model: str) -> None:
    # NOTE: urlopen blocks until the first bytes arrive; STOP is checked per chunk.
    body = json.dumps({"model": model, "prompt": prompt, "stream": True}).encode()
    req = urllib.request.Request(ollama_host() + "/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    collected: list[str] = []
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            for raw in r:
                if stop and stop.is_set():
                    on_done("".join(collected), "stopped")
                    return
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except ValueError:
                    continue
                if obj.get("error"):
                    on_done("".join(collected), str(obj["error"])[:200])
                    return
                chunk = obj.get("response", "")
                if chunk:
                    collected.append(chunk)
                    on_delta(chunk)
                if obj.get("done"):
                    break
    except (urllib.error.URLError, OSError) as e:
        on_done("".join(collected), f"ollama: {e}")
        return
    on_done("".join(collected), "")


def _stream(prompt: str, on_delta, on_done, stop, backend: Optional[str]) -> None:
    backend = backend or available_backend()
    if not backend:
        on_done("", "no AI engine available (install claude/gemini/codex, or run ollama)")
        return
    if backend == "ollama" or backend.startswith("ollama:"):
        model = backend.split(":", 1)[1] if ":" in backend else ollama_model()
        _stream_ollama(prompt, on_delta, on_done, stop, model)
    elif backend in _CLI:
        _stream_cli(backend, prompt, on_delta, on_done, stop)
    else:
        on_done("", f"unknown engine: {backend}")


# --------------------------------------------------------------------------- #
# public entry points
# --------------------------------------------------------------------------- #

def investigate(dossier: Dossier, on_delta: Callable[[str], None],
                on_done: Callable[[str, str], None],
                stop: Optional[threading.Event] = None,
                backend: Optional[str] = None) -> None:
    """Investigate a single device. Runs in the calling thread."""
    prompt = (recon.SYSTEM_BRIEF + "\n\n--- EVIDENCE ---\n"
              + recon.build_prompt(dossier) + "\n\nWrite the report now.")
    _stream(prompt, on_delta, on_done, stop, backend)


def investigate_network(devices: list, public: dict,
                        on_delta: Callable[[str], None],
                        on_done: Callable[[str, str], None],
                        stop: Optional[threading.Event] = None,
                        backend: Optional[str] = None) -> None:
    """Investigate the whole network from the stored inventory. Runs in the
    calling thread."""
    prompt = (recon.NETWORK_BRIEF + "\n\n--- INVENTORY ---\n"
              + recon.build_network_prompt(devices, public) + "\n\nWrite the report now.")
    _stream(prompt, on_delta, on_done, stop, backend)
