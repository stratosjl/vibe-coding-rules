"""Bounded stdio client for the installed Codex app-server protocol."""

from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time


class CodexRpc:
    def __init__(self, cwd: Path, overrides: list[str] | None = None):
        launcher = shutil.which("codex") or shutil.which("codex.cmd")
        if not launcher:
            raise RuntimeError("Codex executable was not found")
        if os.name == "nt":
            package = Path(launcher).parent / "node_modules/@openai/codex/bin/codex.js"
            if not package.is_file():
                raise RuntimeError("The installed npm launcher was not found")
            command = [shutil.which("node") or "node", str(package)]
        else:
            command = [launcher]
        command += ["app-server", "--stdio"]
        for value in overrides or []:
            command += ["-c", value]
        self.process = subprocess.Popen(
            command, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self.messages: queue.Queue = queue.Queue()
        self.notifications: list[dict] = []
        self.sequence = 0
        threading.Thread(target=self._read, daemon=True).start()
        try:
            self.initialized = self.call("initialize", {
                "clientInfo": {"name": "vc_roe_validation", "version": "1.0"},
                "capabilities": {"experimentalApi": True},
            })
            self.send({"method": "initialized", "params": {}})
        except BaseException:
            self.close()
            raise

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    self.messages.put(json.loads(line))
                except json.JSONDecodeError:
                    self.messages.put({"protocol_error": "Non-JSON server output"})
        finally:
            self.messages.put({"protocol_error": "Server output closed"})

    def send(self, value: dict):
        self.process.stdin.write(json.dumps(value) + "\n")
        self.process.stdin.flush()

    def call(self, method: str, params: dict, timeout: float = 45):
        self.sequence += 1
        request_id = self.sequence
        self.send({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while True:
            message = self.messages.get(timeout=max(0.01, deadline - time.monotonic()))
            if "protocol_error" in message:
                raise RuntimeError(message["protocol_error"])
            if message.get("id") == request_id and "method" not in message:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message["result"]
            if "id" in message and "method" in message:
                self.send({"id": message["id"], "error": {
                    "code": -32601, "message": "Interactive requests are not supported by this audit",
                }})
            else:
                self.notifications.append(message)
            if time.monotonic() >= deadline:
                raise TimeoutError(method)

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(timeout=5)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
