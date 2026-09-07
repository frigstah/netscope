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
    if obj.get("type") in ("content", "assistant", "message"):
        c = obj.get("text") or obj.get("content") or ""
        if isinstance(c, str):
            return c
    return ""


def _stream_cli(backend: str, prompt: str, on_delta, on_done, stop) -> None:
    cmd = _CLI[backend]()
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1,
        )
    except OSError as e:
        on_done("", f"could not launch {backend}: {e}")
        return

    collected: list[str] = []
    plain = backend == "codex"

    def pump():
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
        except (OSError, BrokenPipeError):
            pass

    threading.Thread(target=pump, daemon=True).start()

    try:
        for line in proc.stdout:
            if stop and stop.is_set():
                proc.terminate()
                on_done("".join(collected), "stopped")
                return
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
                    on_done("".join(collected), str(obj.get("result") or "AI error")[:200])
                    return
            else:
                text = _extract_gemini_delta(obj)
            if text:
                collected.append(text)
                on_delta(text)
    except Exception as e:
        on_done("".join(collected), str(e)[:200])
        return

    code = proc.wait()
    err = ""
    if code != 0 and not collected:
        tail = (proc.stderr.read() or "").strip()[-200:]
        err = f"{backend} exited {code}: {tail}" if tail else f"{backend} exited {code}"
    on_done("".join(collected), err)


def _stream_ollama(prompt: str, on_delta, on_done, stop, model: str) -> None:
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
