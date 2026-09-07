"""AI investigation: hand a device dossier to a local AI CLI and stream the
report back. Developed for and by frig.

Uses whichever AI CLI is installed (claude, then gemini, then codex), all in
non-interactive streaming mode. No network code of our own; the CLI talks to
its own provider.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from typing import Callable, Optional

from . import recon
from .recon import Dossier


# (binary, builder) — first available wins. Each builder returns argv; the
# prompt is fed on stdin.
def _claude_cmd() -> list[str]:
    return ["claude", "-p", "--output-format", "stream-json", "--verbose",
            "--include-partial-messages"]


def _gemini_cmd() -> list[str]:
    return ["gemini", "-o", "stream-json"]


def _codex_cmd() -> list[str]:
    return ["codex", "exec", "--skip-git-repo-check", "-s", "read-only", "-"]


BACKENDS = [
    ("claude", _claude_cmd),
    ("gemini", _gemini_cmd),
    ("codex", _codex_cmd),
]


def available_backend() -> Optional[str]:
    for name, _ in BACKENDS:
        if shutil.which(name):
            return name
    return None


def _extract_claude_delta(obj: dict) -> str:
    if obj.get("type") == "stream_event":
        ev = obj.get("event", {})
        if ev.get("type") == "content_block_delta":
            delta = ev.get("delta", {})
            if delta.get("type") == "text_delta":
                return delta.get("text", "")
    return ""


def _extract_gemini_delta(obj: dict) -> str:
    # gemini stream-json emits assistant content chunks; be liberal
    if obj.get("type") in ("content", "assistant", "message"):
        c = obj.get("text") or obj.get("content") or ""
        if isinstance(c, str):
            return c
    return ""


def investigate(
    dossier: Dossier,
    on_delta: Callable[[str], None],
    on_done: Callable[[str, str], None],
    stop: Optional[threading.Event] = None,
    backend: Optional[str] = None,
) -> None:
    """Run the AI investigation in the CURRENT thread, streaming text through
    on_delta. Calls on_done(full_text, error) at the end (error '' on success).
    Meant to be launched inside a worker thread by the GUI.
    """
    backend = backend or available_backend()
    if not backend:
        on_done("", "no AI CLI found (looked for claude, gemini, codex)")
        return

    prompt = recon.SYSTEM_BRIEF + "\n\n--- EVIDENCE ---\n" + recon.build_prompt(dossier) + \
        "\n\nWrite the report now."

    cmd = dict(BACKENDS)[backend]()
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

    def pump_stdin():
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
        except (OSError, BrokenPipeError):
            pass

    threading.Thread(target=pump_stdin, daemon=True).start()

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
                    err = obj.get("result") or "AI returned an error"
                    on_done("".join(collected), str(err)[:200])
                    return
            else:
                text = _extract_gemini_delta(obj)
            if text:
                collected.append(text)
                on_delta(text)
    except Exception as e:  # keep the GUI alive whatever the CLI does
        on_done("".join(collected), str(e)[:200])
        return

    code = proc.wait()
    err = ""
    if code != 0 and not collected:
        tail = (proc.stderr.read() or "").strip()[-200:]
        err = f"{backend} exited {code}: {tail}" if tail else f"{backend} exited {code}"
    on_done("".join(collected), err)
