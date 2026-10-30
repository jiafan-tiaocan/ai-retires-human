#!/usr/bin/env python3
"""Task-scoped Chrome relay. Python 3.9+, macOS; no model calls or credentials."""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from urllib.parse import urlsplit
import uuid

TERMINAL = {"completed", "needs_attention", "timed_out", "cancelled"}
SENT = {"submitting", "waiting"}


class BrowserError(Exception):
    pass


class Attention(Exception):
    pass


class OwnershipLost(Exception):
    pass


def read(job):
    return json.loads((job / "status.json").read_text())


def atomic(path, value):
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temp.open("x", encoding="utf-8") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


@contextmanager
def state_mutex(job):
    with (job / "state.lock").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def check_owner(job, state, terminal_ok=False):
    if (job / "status.json").exists():
        current = read(job)
        if current.get("owner_token") != state.get("owner_token"):
            raise OwnershipLost()
        if current["status"] in TERMINAL and not terminal_ok:
            raise OwnershipLost()


def persist(job, state):
    state["updated_at"] = time.time()
    atomic(job / "status.json", json.dumps(state, ensure_ascii=False, indent=2))


def save(job, state):
    with state_mutex(job):
        check_owner(job, state)
        persist(job, state)


def emit_event(job, state):
    if (job / "event.json").exists():
        return
    atomic(job / "event.json", json.dumps({
        "job_id": state["job_id"], "status": state["status"], "reason": state["reason"],
        "result_file": str(job / "result.txt") if state["status"] == "completed" else None,
    }, indent=2))


def repair_event(job, state):
    with state_mutex(job):
        check_owner(job, state, terminal_ok=True)
        emit_event(job, state)


def finish(job, state, status, reason, result=None):
    with state_mutex(job):
        check_owner(job, state)
        if result is not None:
            atomic(job / "result.txt", result)
        state.update(status=status, reason=reason)
        persist(job, state)
        emit_event(job, state)


def partial(job, state, value):
    with state_mutex(job):
        check_owner(job, state)
        atomic(job / "partial.txt", value)


def allowed_url(url):
    p = urlsplit(url)
    return ((p.scheme == "https" and p.netloc == "chatgpt.com") or
            (p.scheme == "http" and p.hostname == "127.0.0.1" and p.port is not None))


def applescript(source):
    try:
        out = subprocess.run(["osascript", "-"], input=source, text=True,
                             capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BrowserError("Chrome bridge unavailable or timed out") from exc
    if out.returncode:
        if "JavaScript" in out.stderr and "(12)" in out.stderr:
            raise Attention("Enable Chrome View > Developer > Allow JavaScript from Apple Events")
        raise BrowserError("Chrome operation failed; original tab may be unavailable")
    return out.stdout.strip()


class Chrome:
    def open(self, url):
        raw = applescript('tell application "Google Chrome"\n'
                          'if (count of windows) is 0 then make new window\n'
                          'set w to front window\n'
                          'set t to make new tab at end of tabs of w with properties {URL:' +
                          json.dumps(url) + '}\n'
                          'return (id of w as text) & "," & (id of t as text)\nend tell')
        return [int(x) for x in raw.split(",")]

    def evaluate(self, tab, code):
        wid, tid = tab
        raw = applescript('tell application "Google Chrome"\n'
                          'tell tab id ' + str(int(tid)) + ' of window id ' + str(int(wid)) + '\n'
                          'return execute javascript ' + json.dumps(code, ensure_ascii=False) +
                          '\nend tell\nend tell')
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise BrowserError("Chrome returned an invalid page observation") from exc

    def probe(self, tab, origin):
        guard = "if(location.origin!==" + json.dumps(origin) + ")return {url:location.href,origin_changed:true};"
        return self.evaluate(tab, "JSON.stringify((()=>{" + guard + """
          const visible=e=>!!e && !!(e.offsetWidth||e.offsetHeight||e.getClientRects().length);
          const text=s=>[...document.querySelectorAll(s)].filter(visible).map(e=>e.innerText);
          return {url:location.href, ready:document.readyState,
            editor:[...document.querySelectorAll('[contenteditable=true][role=textbox],#prompt-textarea,textarea')].some(visible),
            users:text('[data-message-author-role=user],[data-message-role=user]'),
            answers:text('[data-message-author-role=assistant],[data-message-role=assistant]'),
            busy:[...document.querySelectorAll('[data-testid=stop-button],button[aria-label="Stop generating"],button[aria-label="停止生成"]')].some(visible),
            challenge:!!document.querySelector('#challenge-running,iframe[src*="challenges.cloudflare.com"]'),
            login:[...document.querySelectorAll('[data-testid=login-button],a[href*="/auth/login"]')].some(visible)};
        })())""")

    def fill(self, tab, prompt, origin):
        return self.evaluate(tab, "JSON.stringify((()=>{"
            "if(location.origin!==" + json.dumps(origin) + ")return {ok:false,origin_changed:true};"
            "const e=[...document.querySelectorAll('[contenteditable=true][role=textbox],#prompt-textarea,textarea')]"
            ".find(x=>x.offsetWidth||x.offsetHeight||x.getClientRects().length);"
            "if(!e)return {ok:false};e.focus();const p=" + json.dumps(prompt) + ";"
            "if(e.tagName==='TEXTAREA'){"
            "Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(e,p);"
            "e.dispatchEvent(new Event('input',{bubbles:true}));"
            "}else{const s=getSelection();const r=document.createRange();r.selectNodeContents(e);"
            "s.removeAllRanges();s.addRange(r);document.execCommand('insertText',false,p);}"
            "return {ok:(e.value??e.innerText).trim()===p.trim()};})())")

    def send(self, tab, origin):
        guard = "if(location.origin!==" + json.dumps(origin) + ")return {clicked:false,origin_changed:true};"
        return self.evaluate(tab, "JSON.stringify((()=>{" + guard + """
          const s='[data-testid=send-button],button[aria-label="Send prompt"],button[aria-label="发送提示"],button[aria-label="发送"],button[aria-label="Send"]';
          const buttons=[...document.querySelectorAll(s)].filter(e=>!e.disabled&&(e.offsetWidth||e.offsetHeight||e.getClientRects().length));
          if(buttons.length!==1)return {clicked:false};buttons[0].click();return {clicked:true};
        })())""")


def own_lock(path):
    stream = path.open("a")
    try:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        stream.close()
        return None
    return stream


def observe(job, state, browser, parent_pid):
    """One browser worker. A restart in SENT phases can only observe, never send."""
    if os.getppid() != parent_pid:
        return
    deadline = state["deadline"]
    packet = (job / "prompt.md").read_text()
    marker = "[[relay-task:" + state["job_id"] + "]]"
    end = "[[relay-complete:" + state["job_id"] + "]]"
    prompt = marker + "\n" + packet + "\n\nEnd your answer with this exact final line:\n" + end
    if not state.get("tab"):
        if state["status"] in SENT:
            finish(job, state, "needs_attention", "Submission uncertain; inspect the saved conversation before retrying")
            return
        state["tab"] = browser.open(state["url"])
        if os.getppid() != parent_pid:
            return
        save(job, state)
    recovered = False
    uncertain_since = time.time()
    expected = urlsplit(state["url"])
    origin = expected.scheme + "://" + expected.netloc
    while time.time() < deadline:
        if os.getppid() != parent_pid:
            return  # Supervisor died; leave durable state for explicit resume.
        if (job / "cancel").exists():
            finish(job, state, "cancelled", "Cancellation requested; remote conversation retained")
            return
        try:
            info = browser.probe(state["tab"], origin)
        except BrowserError:
            url = state.get("conversation_url", "")
            if state["status"] in SENT and "/c/" in url and not recovered and allowed_url(url):
                state["tab"] = browser.open(url)
                if os.getppid() != parent_pid:
                    return
                recovered = True
                save(job, state)
                continue
            raise
        if os.getppid() != parent_pid:
            return
        actual = urlsplit(info["url"])
        if info["url"] == "about:blank" and state["status"] not in SENT and time.time() - uncertain_since < 10:
            time.sleep(state["poll_seconds"])
            continue
        if (actual.scheme, actual.netloc) != (expected.scheme, expected.netloc):
            raise Attention("Browser left the configured provider; complete login manually")
        state["conversation_url"] = info["url"]
        save(job, state)
        if info["challenge"] or info["login"]:
            raise Attention("Login or a human-only browser challenge is required")
        if state["status"] not in SENT:
            if not info["editor"]:
                time.sleep(state["poll_seconds"])
                continue
            filled = browser.fill(state["tab"], prompt, origin)
            if os.getppid() != parent_pid:
                return
            if filled.get("origin_changed"):
                raise Attention("Task tab changed origin before fill; no packet written")
            if not filled.get("ok"):
                raise BrowserError("Could not fill the task-owned composer")
            # Save before the external action: even a lost click receipt cannot cause a resend.
            state["status"] = "submitting"
            save(job, state)
            time.sleep(0.3)
            if os.getppid() != parent_pid or (job / "cancel").exists():
                return
            sent = browser.send(state["tab"], origin)
            if os.getppid() != parent_pid:
                return
            if not sent.get("clicked"):
                raise Attention("Submission uncertain or Send selector changed; inspect original tab")
            state["status"] = "waiting"
            save(job, state)
            uncertain_since = time.time()
        else:
            matched = any(marker in text for text in info["users"])
            if matched:
                for answer in reversed(info["answers"]):
                    if answer.strip().endswith(end) and not info["busy"]:
                        finish(job, state, "completed", "Matched marked answer saved; task correctness still needs review", result=answer)
                        return
                if info["answers"]:
                    partial(job, state, info["answers"][-1])
            elif time.time() - uncertain_since > 15:
                raise Attention("Original submitted task cannot be confirmed; no automatic resend")
        time.sleep(state["poll_seconds"])
    finish(job, state, "timed_out", "Overall deadline reached; original conversation and partial output retained")


def worker(job, owner_pid):
    if os.getppid() != owner_pid:
        return 0
    lock = own_lock(job / "worker.lock")
    if lock is None:
        return 75
    with lock:
        state = read(job)
        try:
            observe(job, state, Chrome(), owner_pid)
        except Attention as exc:
            if os.getppid() == owner_pid:
                finish(job, state, "needs_attention", str(exc))
        except BrowserError as exc:
            if os.getppid() == owner_pid:
                state["last_error"] = str(exc)
                save(job, state)
            return 1
    return 0


def stop_child(child):
    if child.poll() is None:
        os.killpg(child.pid, signal.SIGTERM)
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=3)


def supervise(job):
    lock = own_lock(job / "supervisor.lock")
    if lock is None:
        return 0
    child = None
    with lock:
        try:
            with state_mutex(job):
                state = read(job)
                if state["status"] not in TERMINAL:
                    state["owner_token"] = uuid.uuid4().hex
                    persist(job, state)
            while True:
                state = read(job)
                if state["status"] in TERMINAL:
                    repair_event(job, state)
                    return 0
                if (job / "cancel").exists():
                    finish(job, state, "cancelled", "Cancellation requested; remote conversation retained")
                    return 0
                if time.time() >= state["deadline"]:
                    finish(job, state, "timed_out", "Persisted overall deadline reached")
                    return 0
                # An orphaned worker exits on parent change. Never overlap it during resume.
                available = own_lock(job / "worker.lock")
                if available is None:
                    time.sleep(0.5)
                    continue
                available.close()
                if state["attempts"] >= state["max_attempts"]:
                    finish(job, state, "needs_attention", state.get("last_error", "Browser retry budget exhausted"))
                    return 0
                state["attempts"] += 1
                save(job, state)
                child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "_worker", str(job),
                                          "--owner-pid", str(os.getpid())],
                                         stdin=subprocess.DEVNULL, start_new_session=True)
                while child.poll() is None:
                    state = read(job)
                    if (job / "cancel").exists() or time.time() >= state["deadline"]:
                        stop_child(child)
                        state = read(job)
                        if state["status"] not in TERMINAL:
                            cancelled = (job / "cancel").exists()
                            finish(job, state, "cancelled" if cancelled else "timed_out",
                                   "Cancellation requested" if cancelled else "Overall deadline reached")
                        return 0
                    # Detect a hung worker even though the main agent is absent.
                    if time.time() - state["updated_at"] > 45:
                        stop_child(child)
                        state = read(job)
                        state["last_error"] = "Worker heartbeat stalled"
                        save(job, state)
                        break
                    time.sleep(0.5)
                child.wait()
                child = None
                state = read(job)
                if state["status"] not in TERMINAL:
                    time.sleep(min(2 ** state["attempts"], max(0, state["deadline"] - time.time())))
        finally:
            if child is not None:
                stop_child(child)


def launch(job):
    with (job / "worker.log").open("a") as log:
        proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "_supervise", str(job)],
                                stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    return {"job_dir": str(job), "supervisor_pid": proc.pid,
            "completion_event": str(job / "event.json"),
            "collection": "No host wakeup configured; collect on the next interaction. Do not agent-poll."}


def status(job):
    state = read(job)
    if state["status"] in TERMINAL:
        repair_event(job, state)
    if state["status"] not in TERMINAL and time.time() - state["created_at"] > 3:
        lock = own_lock(job / "supervisor.lock")
        if lock is not None:
            lock.close()
            state["recorded_status"] = state["status"]
            state["status"] = "interrupted"
            state["reason"] = "Supervisor absent; explicit resume preserves the original deadline and attempts"
    return state


def main(argv=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start")
    start.add_argument("job_dir", type=Path)
    start.add_argument("--prompt-file", type=Path, required=True)
    start.add_argument("--url", default="https://chatgpt.com/")
    start.add_argument("--timeout", type=float, default=1800)
    start.add_argument("--attempts", type=int, default=3)
    start.add_argument("--poll", type=float, default=3)
    for name in ("status", "collect", "cancel", "resume", "_supervise"):
        sub.add_parser(name).add_argument("job_dir", type=Path)
    internal_worker = sub.add_parser("_worker")
    internal_worker.add_argument("job_dir", type=Path)
    internal_worker.add_argument("--owner-pid", type=int, required=True)
    args = parser.parse_args(argv)
    job = args.job_dir.resolve()
    if args.command == "start":
        if not allowed_url(args.url) or not 1 <= args.timeout <= 7200 or not 1 <= args.attempts <= 10 or not 0.2 <= args.poll <= 30:
            parser.error("Invalid URL or budget: ChatGPT/loopback only; timeout 1–7200s, attempts 1–10, poll 0.2–30s")
        packet = args.prompt_file.read_text()
        if not packet.strip():
            parser.error("Task packet must not be empty")
        job.mkdir(parents=True, exist_ok=False)
        atomic(job / "prompt.md", packet)
        now = time.time()
        save(job, {"job_id": uuid.uuid4().hex, "status": "queued", "created_at": now,
                   "deadline": now + args.timeout, "attempts": 0, "max_attempts": args.attempts,
                   "poll_seconds": args.poll, "url": args.url})
        print(json.dumps(launch(job)))
    elif args.command == "_worker":
        return worker(job, args.owner_pid)
    elif args.command == "_supervise":
        return supervise(job)
    elif args.command == "status":
        print(json.dumps(status(job), ensure_ascii=False, indent=2))
    elif args.command == "collect":
        st = status(job)
        if st["status"] == "completed":
            print((job / "result.txt").read_text())
        else:
            print(json.dumps(st, ensure_ascii=False, indent=2))
            return 2
    elif args.command == "cancel":
        (job / "cancel").touch(exist_ok=True)
        print(json.dumps({"job_dir": str(job), "cancellation_requested": True}))
    elif args.command == "resume":
        st = status(job)
        if st["status"] != "interrupted":
            print(json.dumps(st, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(launch(job)))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except OwnershipLost:
        sys.exit(0)
    except (OSError, ValueError) as exc:
        print("Relay error: " + str(exc), file=sys.stderr)
        sys.exit(2)
