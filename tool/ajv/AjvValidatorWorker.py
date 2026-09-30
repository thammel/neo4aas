import json
import subprocess
import threading
from pathlib import Path


class AjvValidatorWorker:
    def __init__(self, schema):
        script_path = Path(__file__).parent / "validate_worker.js"

        self.proc = subprocess.Popen(
            ["node", str(script_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        self._stderr_lines = []
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stderr_thread.start()

        self._send({"type": "init", "schema": schema})
        response = self._read_response()

        if not response.get("ok"):
            self.close()
            raise RuntimeError(response.get("error", "Failed to initialize validator"))

    def _read_stderr(self):
        if self.proc.stderr is None:
            return
        for line in self.proc.stderr:
            self._stderr_lines.append(line.rstrip("\n"))

    def _send(self, obj):
        if self.proc.stdin is None:
            raise RuntimeError("Worker stdin is closed")
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def _read_response(self):
        if self.proc.stdout is None:
            raise RuntimeError("Worker stdout is closed")

        line = self.proc.stdout.readline()
        if not line:
            stderr_text = "\n".join(self._stderr_lines)
            raise RuntimeError(f"Worker terminated unexpectedly.\n{stderr_text}")

        return json.loads(line)

    def validate(self, json_string):
        self._send({"type": "validate", "jsonString": json_string})
        response = self._read_response()

        if not response.get("ok"):
            raise RuntimeError(response.get("error", "Worker validation error"))

        return tuple(response["result"])

    def close(self):
        if self.proc.stdin:
            self.proc.stdin.close()
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()