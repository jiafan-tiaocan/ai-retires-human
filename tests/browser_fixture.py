"""Opt-in real Chrome transport check against a synthetic loopback page only."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time

RUNNER = Path(__file__).resolve().parents[1] / "skills/web-task-relay/scripts/relay.py"
spec = importlib.util.spec_from_file_location("relay", RUNNER)
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)
HTML = b'''<!doctype html><meta charset="utf-8"><title>Relay synthetic transport fixture</title>
<h1>Local synthetic fixture; no model calls</h1>
<textarea id="prompt-textarea"></textarea><button data-testid="send-button">Send</button>
<main id="messages"></main><script>
let submissions=0;
document.querySelector('button').onclick=()=>{
  submissions++;
  const p=document.querySelector('textarea').value;
  const u=document.createElement('div');u.dataset.messageAuthorRole='user';u.innerText=p;
  document.querySelector('main').append(u);
  const stop=document.createElement('button');stop.dataset.testid='stop-button';stop.textContent='Generating';
  document.body.append(stop);
  const a=document.createElement('div');a.dataset.messageAuthorRole='assistant';a.innerText='partial';
  document.querySelector('main').append(a);
  const delay=new URL(location.href).searchParams.get('slow')?4000:500;
  setTimeout(()=>{a.innerText='fixture-answer: 2\\n'+p.trim().split('\\n').pop();stop.remove()},delay);
};
</script>'''


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(HTML)

    def log_message(self, *args):
        pass


def command(*args):
    return subprocess.run([sys.executable, str(RUNNER), *map(str, args)],
                          text=True, capture_output=True, check=True, timeout=25)


def await_state(job, predicate, seconds=20):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if (job / "status.json").exists():
            state = relay.read(job)
            if predicate(state):
                return state
        time.sleep(0.1)  # Test code waits; no agent or model involved.
    raise AssertionError("Fixture deadline exceeded: " + (job / "status.json").read_text())


def run():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = "http://127.0.0.1:" + str(server.server_port)
    with tempfile.TemporaryDirectory(prefix="relay-fixture-") as tmp:
        root = Path(tmp)
        packet = root / "packet.md"
        packet.write_text("Objective: add 1 and 1. Deliverable: 2. Acceptance: answer must include 2.")
        jobs = []
        try:
            for scenario in ("normal", "worker-crash", "supervisor-crash"):
                job = root / scenario
                receipt = json.loads(command("start", job, "--prompt-file", packet,
                                             "--url", url + ("/?slow=1" if scenario != "normal" else "/"),
                                             "--timeout", "25", "--poll", "0.2").stdout)
                jobs.append((job, receipt))
                if scenario != "normal":
                    waiting = await_state(job, lambda s: s["status"] == "waiting")
                    budget = (waiting["deadline"], waiting["attempts"])
                    if scenario == "worker-crash":
                        # Only the worker launched by this test's supervisor is targeted.
                        children = subprocess.check_output(["pgrep", "-P", str(receipt["supervisor_pid"])], text=True)
                        worker_pid = int(children.strip().splitlines()[0])
                        os.kill(worker_pid, signal.SIGKILL)
                    else:
                        os.kill(receipt["supervisor_pid"], signal.SIGKILL)
                        time.sleep(3.1)
                        interrupted = json.loads(command("status", job).stdout)
                        assert interrupted["status"] == "interrupted", interrupted
                        assert (interrupted["deadline"], interrupted["attempts"]) == budget
                        receipt = json.loads(command("resume", job).stdout)
                        jobs[-1] = (job, receipt)
                state = await_state(job, lambda s: s["status"] in relay.TERMINAL)
                assert state["status"] == "completed", state
                output = command("collect", job).stdout
                assert "fixture-answer: 2" in output, output
                sends = relay.Chrome().evaluate(state["tab"], "JSON.stringify(submissions)")
                assert sends == 1, (scenario, sends)
                assert (job / "event.json").is_file()
                print(json.dumps({"scenario": scenario, "status": state["status"],
                                  "submissions": sends, "attempts": state["attempts"],
                                  "evidence": "synthetic loopback page in real Chrome; no real model"}))
        finally:
            for job, receipt in jobs:
                if (job / "status.json").exists() and relay.read(job)["status"] not in relay.TERMINAL:
                    (job / "cancel").touch()
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    run()
