"""Failure invariants; no provider requests, API keys or real account data."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

RUNNER = Path(__file__).resolve().parents[1] / "skills/web-task-relay/scripts/relay.py"
spec = importlib.util.spec_from_file_location("relay", RUNNER)
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)


class FakeBrowser:
    def __init__(self, state, lost_receipt=False, wrong_answer=False):
        self.state = state
        self.submissions = 0
        self.lost_receipt = lost_receipt
        self.wrong_answer = wrong_answer

    def open(self, url):
        return [1, 2]

    def probe(self, tab, origin=None):
        job_id = self.state["job_id"]
        return {"url": "https://chatgpt.com/c/test", "editor": True,
                "challenge": False, "login": False, "busy": False,
                "users": ["[[relay-task:" + job_id + "]]"] if self.submissions else [],
                "answers": ["answer\n[[relay-complete:" + ("other-job" if self.wrong_answer else job_id) + "]]"]
                if self.submissions else []}

    def fill(self, tab, prompt, origin=None):
        return {"ok": True}

    def send(self, tab, origin=None):
        self.submissions += 1
        if self.lost_receipt:
            self.lost_receipt = False
            raise relay.BrowserError("Lost receipt after external submission")
        return {"clicked": True}


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.job = Path(self.tmp.name)
        (self.job / "prompt.md").write_text("Return answer.")
        self.state = {"job_id": "unique-job", "status": "queued", "created_at": time.time() - 5,
                      "deadline": time.time() + 1, "attempts": 1, "max_attempts": 3,
                      "poll_seconds": 0.001, "url": "https://chatgpt.com/"}
        relay.save(self.job, self.state)

    def test_result_survives_new_reader(self):
        browser = FakeBrowser(self.state)
        relay.observe(self.job, self.state, browser, os.getppid())
        persisted = relay.read(self.job)
        self.assertEqual(persisted["status"], "completed")
        self.assertEqual(browser.submissions, 1)
        self.assertEqual(json.loads((self.job / "event.json").read_text())["job_id"], "unique-job")
        self.assertIn("answer", (self.job / "result.txt").read_text())

    def test_lost_send_receipt_recovers_without_second_submission(self):
        browser = FakeBrowser(self.state, lost_receipt=True)
        with self.assertRaises(relay.BrowserError):
            relay.observe(self.job, self.state, browser, os.getppid())
        persisted = relay.read(self.job)
        self.assertEqual(persisted["status"], "submitting")
        relay.observe(self.job, persisted, browser, os.getppid())
        self.assertEqual(relay.read(self.job)["status"], "completed")
        self.assertEqual(browser.submissions, 1)

    def test_crash_before_click_does_not_resend_on_recovery(self):
        browser = FakeBrowser(self.state)
        original = relay.save

        class Crash(BaseException):
            pass

        def crash_after_persist(job, state):
            original(job, state)
            if state["status"] == "submitting":
                raise Crash()

        with patch.object(relay, "save", side_effect=crash_after_persist):
            with self.assertRaises(Crash):
                relay.observe(self.job, self.state, browser, os.getppid())
        self.assertEqual(browser.submissions, 0)
        persisted = relay.read(self.job)
        persisted["deadline"] = time.time() + 0.025
        relay.observe(self.job, persisted, browser, os.getppid())
        self.assertEqual(browser.submissions, 0)
        self.assertEqual(relay.read(self.job)["status"], "timed_out")

    def test_another_jobs_answer_is_not_success(self):
        self.state["status"] = "waiting"
        self.state["tab"] = [1, 2]
        self.state["deadline"] = time.time() + 0.025
        browser = FakeBrowser(self.state, wrong_answer=True)
        browser.submissions = 1
        relay.observe(self.job, self.state, browser, os.getppid())
        self.assertEqual(relay.read(self.job)["status"], "timed_out")
        self.assertFalse((self.job / "result.txt").exists())

    def test_supervisor_interruption_does_not_reset_budget(self):
        before = relay.read(self.job)
        report = relay.status(self.job)
        self.assertEqual(report["status"], "interrupted")
        self.assertEqual(relay.read(self.job), before)
        before["attempts"] = before["max_attempts"]
        relay.save(self.job, before)
        relay.supervise(self.job)
        after = relay.read(self.job)
        self.assertEqual(after["status"], "needs_attention")
        self.assertEqual(after["deadline"], before["deadline"])
        self.assertEqual(after["attempts"], before["attempts"])

    def test_cancel_does_not_need_a_running_agent_or_browser(self):
        (self.job / "cancel").touch()
        with patch.object(relay, "Chrome") as chrome:
            relay.supervise(self.job)
        chrome.assert_not_called()
        self.assertEqual(relay.read(self.job)["status"], "cancelled")

    def test_lock_prevents_two_supervisors(self):
        lock = relay.own_lock(self.job / "supervisor.lock")
        self.addCleanup(lock.close)
        with patch.object(relay.subprocess, "Popen") as popen:
            relay.supervise(self.job)
        popen.assert_not_called()
        self.assertEqual(relay.read(self.job)["attempts"], 1)

    def test_duplicate_start_cannot_spawn_a_second_job(self):
        with patch.object(relay, "launch") as launch:
            with self.assertRaises(FileExistsError):
                relay.main(["start", str(self.job), "--prompt-file", str(self.job / "prompt.md")])
        launch.assert_not_called()

    def test_old_worker_stops_writing_after_supervisor_dies(self):
        browser = FakeBrowser(self.state)
        with patch.object(relay.os, "getppid", return_value=1234):
            relay.observe(self.job, self.state, browser, 4321)
        self.assertEqual(browser.submissions, 0)
        self.assertEqual(relay.read(self.job)["status"], "queued")

    def test_late_browser_failure_cannot_overwrite_terminal_state(self):
        self.state.update(status="waiting", owner_token="old-owner", tab=[1, 2])
        relay.persist(self.job, self.state)
        current = dict(self.state, owner_token="new-owner")

        class LateFailureBrowser:
            def probe(_, tab, origin=None):
                relay.persist(self.job, current)
                relay.finish(self.job, current, "timed_out", "New supervisor timed out")
                raise relay.BrowserError("Old in-flight call failed late")

        with patch.object(relay, "Chrome", return_value=LateFailureBrowser()):
            with self.assertRaises(relay.OwnershipLost):
                relay.worker(self.job, os.getppid())
        self.assertEqual(relay.read(self.job)["status"], "timed_out")
        self.assertEqual(json.loads((self.job / "event.json").read_text())["status"], "timed_out")

    def test_worker_cannot_adopt_pid_one_as_new_supervisor(self):
        with patch.object(relay.os, "getppid", return_value=1):
            with patch.object(relay, "Chrome") as chrome:
                relay.worker(self.job, 4321)
        chrome.assert_not_called()

    def test_terminal_event_is_repaired_after_interrupted_commit(self):
        original = relay.atomic

        def fail_event(path, value):
            if path.name == "event.json":
                raise OSError("Simulated crash between terminal state and event")
            original(path, value)

        with patch.object(relay, "atomic", side_effect=fail_event):
            with self.assertRaises(OSError):
                relay.finish(self.job, self.state, "completed", "Answer saved", result="answer")
        self.assertEqual(relay.read(self.job)["status"], "completed")
        self.assertFalse((self.job / "event.json").exists())
        relay.supervise(self.job)
        event = self.job / "event.json"
        self.assertEqual(json.loads(event.read_text())["status"], "completed")
        stamp = event.stat().st_mtime_ns
        relay.status(self.job)
        self.assertEqual(event.stat().st_mtime_ns, stamp)

    def test_redirect_cannot_send_task_to_another_origin(self):
        browser = FakeBrowser(self.state)
        original = browser.probe

        def redirect(tab, origin=None):
            info = original(tab, origin)
            info["url"] = "http://127.0.0.1:9999/unrelated"
            return info

        browser.probe = redirect
        with patch.object(browser, "fill") as fill:
            with self.assertRaises(relay.Attention):
                relay.observe(self.job, self.state, browser, os.getppid())
        fill.assert_not_called()
        self.assertEqual(browser.submissions, 0)

    def test_page_side_origin_guard_runs_before_any_dom_access(self):
        # Execute the actual generated JavaScript in macOS JavaScriptCore, without Chrome.
        browser = relay.Chrome()

        def evaluate(tab, code):
            prelude = ('var location={origin:"https://untrusted.invalid",href:"https://untrusted.invalid/"};'
                       'var document={get querySelectorAll(){throw new Error("Unexpected DOM access")}};')
            result = subprocess.run(["osascript", "-l", "JavaScript", "-"], input=prelude + code,
                                    text=True, capture_output=True, check=True, timeout=5)
            return json.loads(result.stdout)

        with patch.object(browser, "evaluate", side_effect=evaluate):
            self.assertTrue(browser.probe([1, 2], "https://chatgpt.com")["origin_changed"])
            self.assertTrue(browser.fill([1, 2], "authorized packet", "https://chatgpt.com")["origin_changed"])
            self.assertTrue(browser.send([1, 2], "https://chatgpt.com")["origin_changed"])

    def test_supervisor_kills_its_hung_child_at_the_deadline(self):
        self.state.update(attempts=0, deadline=time.time() + 0.3)
        relay.save(self.job, self.state)
        popen = relay.subprocess.Popen
        children = []

        def hang(*args, **kwargs):
            proc = popen([relay.sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
            children.append(proc)
            return proc

        with patch.object(relay.subprocess, "Popen", side_effect=hang):
            relay.supervise(self.job)
        self.assertEqual(relay.read(self.job)["status"], "timed_out")
        self.assertIsNotNone(children[0].poll())

    def test_pre_submission_failure_retries_and_sends_once(self):
        self.state.update(attempts=0, deadline=time.time() + 10)
        relay.save(self.job, self.state)
        browser = FakeBrowser(self.state)
        launches = []

        class Exited:
            def poll(self):
                return 0

            def wait(self, **kwargs):
                return 0

        def launch(*args, **kwargs):
            launches.append(args)
            state = relay.read(self.job)
            if len(launches) == 1:
                state["last_error"] = "Navigation temporarily unavailable before send"
                relay.save(self.job, state)
            else:
                relay.observe(self.job, state, browser, os.getppid())
            return Exited()

        with patch.object(relay.subprocess, "Popen", side_effect=launch):
            with patch.object(relay.time, "sleep"):
                relay.supervise(self.job)
        self.assertEqual(relay.read(self.job)["status"], "completed")
        self.assertEqual(relay.read(self.job)["attempts"], 2)
        self.assertEqual(browser.submissions, 1)


if __name__ == "__main__":
    unittest.main()
