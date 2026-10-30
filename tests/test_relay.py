"""Failure invariants; no provider requests, API keys or real account data."""
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import socket
import shutil
import tempfile
import time
import threading
import unittest
from unittest.mock import patch

NODE_BINARY = os.environ.get('RELAY_TEST_NODE') or shutil.which('node')
RUNNER = Path(__file__).resolve().parents[1] / "skills/web-task-relay/scripts/relay.py"
spec = importlib.util.spec_from_file_location("relay", RUNNER)
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)


class FakeBrowser:
    def close(self):
        pass

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


def serve_hung_fixture(endpoint, files, started):
    class Blank:
        def close(self):
            pass

    class HungBrowser:
        def __init__(self, state):
            self.page, self.context, self.driver = Blank(), object(), object()

        def open(self, url):
            return 'fixture-page'

        def probe(self, *args):
            started.touch()
            time.sleep(120)
            return {}

        def close(self):
            pass

    relay.session_files = lambda ignored: files
    relay.PlaywrightBrowser = HungBrowser
    relay.serve_session(endpoint)


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

    def test_ego_receipt_on_stderr_and_durable_page(self):
        browser = relay.EgoBrowser(self.state, self.job)
        receipt = subprocess.CompletedProcess([], 0, stdout='', stderr='RELAY_JSON:[1,"p1"]\n')
        with patch.object(relay.subprocess, 'run', return_value=receipt) as run:
            self.assertEqual(browser.open('https://chatgpt.com/'), [1, 'p1'])
            script = run.call_args.kwargs['input']
            self.assertIn('await taskSpace(cfg.space)', script)
            self.assertIn('await guard()', script)
            self.assertIn(str(self.job), script)
        # A fresh worker evaluates the durable label; no goto, newPage or send.
        browser = relay.EgoBrowser(self.state, self.job)
        receipt.stderr = 'RELAY_JSON:{"url":"https://chatgpt.com/c/original"}\n'
        with patch.object(relay.subprocess, 'run', return_value=receipt) as run:
            browser.probe([1, 'p1'], 'https://chatgpt.com')
            script = run.call_args.kwargs['input']
            self.assertIn('"space": 1', script)
            self.assertIn('"label": "p1"', script)
            self.assertNotIn('page.goto(', script)
            self.assertNotIn('page.click(', script)

    def test_ego_user_control_stops_instead_of_retrying(self):
        browser = relay.EgoBrowser(self.state, self.job)
        receipt = subprocess.CompletedProcess([], 1, stdout='', stderr='EGO_TASK_SPACE_NOT_CONTROLLED')
        with patch.object(relay.subprocess, 'run', return_value=receipt):
            with self.assertRaises(relay.Attention):
                browser.probe([1, 'p1'], 'https://chatgpt.com')

    @unittest.skipUnless(NODE_BINARY, 'Node required for native-document regression')
    def test_ego_navigation_destroys_native_input_target(self):
        self.state['deadline'] = time.time() + 20
        self.state['owner_token'] = 'test-owner'
        relay.persist(self.job, self.state)
        browser = relay.EgoBrowser(self.state, self.job)
        stub = r"""
let navigated=false;
const button={offsetWidth:1,disabled:false,setAttribute(){}};
const editor={offsetWidth:1,setAttribute(){},parentElement:{
  querySelector(){return button},querySelectorAll(){return [button]}}};
globalThis.location={origin:'https://chatgpt.com'};
globalThis.document={querySelectorAll(){return [editor]}};
const native=async selector=>{
  // The replacement page has a matching generic composer/button, but none
  // of the original document's node attributes. Generic input would leak.
  if(navigated && selector.startsWith('[data-relay-target=')) throw new Error('TARGET_GONE');
  throw new Error('PACKET_LEAKED_TO_REPLACEMENT_PAGE');
};
globalThis.taskSpace=async()=>({page(){return {
  async evaluate(fn,arg){const value=fn(arg);navigated=true;return value},
  fill:native,click:native
}}});
"""
        for action in ('fill', 'send'):
            receipt = subprocess.CompletedProcess([], 0, stdout='RELAY_JSON:{}', stderr='')
            with patch.object(relay.subprocess, 'run', return_value=receipt) as run:
                browser.native_target([1, 'p1'], 'https://chatgpt.com', action, 'private packet')
                script = run.call_args.kwargs['input']
            result = subprocess.run([NODE_BINARY, '--input-type=module', '-e', stub+script],
                                    text=True, capture_output=True, timeout=5)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('TARGET_GONE', result.stderr)
            self.assertNotIn('PACKET_LEAKED', result.stderr)

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

    def test_input_failure_stops_without_reconnect_or_submission(self):
        browser = FakeBrowser(self.state)
        with patch.object(browser, 'fill', return_value={'ok': False}):
            with patch.object(relay, 'Chrome', return_value=browser):
                relay.worker(self.job, os.getppid())
        self.assertEqual(relay.read(self.job)['status'], 'needs_attention')
        self.assertIn('no automatic reconnect', relay.read(self.job)['reason'])
        self.assertEqual(browser.submissions, 0)

    def test_confirming_slow_message_keeps_original_deadline_without_resend(self):
        clock = [1000.0]
        self.state['deadline'] = 1060.0
        browser = FakeBrowser(self.state)
        original = browser.probe

        def slow_probe(tab, origin=None):
            clock[0] += 5
            info = original(tab, origin)
            if clock[0] < 1030:
                info['users'], info['answers'] = [], []
            return info

        browser.probe = slow_probe
        with patch.object(relay.time, 'time', side_effect=lambda: clock[0]), patch.object(relay.time, 'sleep'):
            relay.observe(self.job, self.state, browser, os.getppid())
        self.assertEqual(relay.read(self.job)['status'], 'completed')
        self.assertEqual(relay.read(self.job)['deadline'], 1060.0)
        self.assertEqual(browser.submissions, 1)

    def test_missing_session_page_before_submission_reopens_safely(self):
        self.state['tab'] = 'old-session-page'
        fake = FakeBrowser(self.state)

        class LostPage(relay.SessionBrowser):
            def __init__(_):
                _.first_probe = True

            def probe(_, tab, origin):
                if _.first_probe:
                    _.first_probe = False
                    raise relay.BrowserError('Session lost its page mapping')
                return fake.probe(tab, origin)

            def open(_, url):
                return fake.open(url)

            def fill(_, tab, prompt, origin):
                return fake.fill(tab, prompt, origin)

            def send(_, tab, origin):
                return fake.send(tab, origin)

        with patch.object(fake, 'open', wraps=fake.open) as opened:
            relay.observe(self.job, self.state, LostPage(), os.getppid())
        opened.assert_called_once()
        self.assertEqual(relay.read(self.job)['status'], 'completed')
        self.assertEqual(fake.submissions, 1)

    def test_stale_account_before_send_refreshes_once_in_same_browser(self):
        browser = FakeBrowser(self.state)
        browser.refreshed = False
        original = browser.probe

        def stale_probe(tab, origin=None):
            info = original(tab, origin)
            info['errors'] = [] if browser.refreshed else ['你的账户已切换。请重新发送消息']
            return info

        def refresh(tab):
            browser.refreshed = True

        browser.probe, browser.refresh = stale_probe, refresh
        with patch.object(browser, 'refresh', wraps=browser.refresh) as refreshed:
            relay.observe(self.job, self.state, browser, os.getppid())
        refreshed.assert_called_once()
        self.assertEqual(relay.read(self.job)['status'], 'completed')
        self.assertEqual(relay.read(self.job)['attempts'], 2)
        self.assertEqual(browser.submissions, 1)

    def test_account_error_with_existing_answer_never_refreshes_or_resubmits(self):
        self.state.update(status='waiting', tab=[1, 2])
        relay.save(self.job, self.state)
        browser = FakeBrowser(self.state)
        browser.submissions = 1
        original = browser.probe

        def rejected(tab, origin=None):
            info = original(tab, origin)
            info['errors'] = ['你的账户已切换。请重新发送消息']
            return info

        browser.probe = rejected
        with patch.object(browser, 'open') as opened, patch.object(relay, 'Chrome', return_value=browser):
            relay.worker(self.job, os.getppid())
        opened.assert_not_called()
        self.assertEqual(relay.read(self.job)['status'], 'needs_attention')
        self.assertEqual(browser.submissions, 1)

    def test_account_change_without_resend_instruction_does_not_retry(self):
        browser = FakeBrowser(self.state)
        original = browser.probe

        def account_changed(tab, origin=None):
            info = original(tab, origin)
            info['errors'] = ['你的账户已切换。']
            return info

        browser.probe = account_changed
        with patch.object(relay, 'Chrome', return_value=browser):
            relay.worker(self.job, os.getppid())
        self.assertEqual(relay.read(self.job)['status'], 'needs_attention')
        self.assertEqual(browser.submissions, 0)

    def test_queued_ipc_action_rejects_cancelled_stale_or_disconnected_owner(self):
        self.state.update(status='submitting', owner_token='test-owner')
        relay.persist(self.job, self.state)
        client, server = socket.socketpair()
        self.addCleanup(client.close)
        self.addCleanup(server.close)
        request = {'method': 'send', 'job_id': self.state['job_id'],
                   'auth': {'job_dir': str(self.job), 'owner_token': 'test-owner',
                            'deadline': self.state['deadline']}}
        relay.validate_session_job(request, server)
        (self.job / 'cancel').touch()
        with self.assertRaises(relay.Attention):
            relay.validate_session_job(request, server)
        (self.job / 'cancel').unlink()
        self.state['owner_token'] = 'replacement-owner'
        relay.persist(self.job, self.state)
        with self.assertRaises(relay.Attention):
            relay.validate_session_job(request, server)
        self.state['owner_token'] = 'test-owner'
        relay.persist(self.job, self.state)
        client.close()
        with self.assertRaises(relay.Attention):
            relay.validate_session_job(request, server)

    def run_hung_session_case(self, stop_requested):
        files = {kind: self.job / ('session' + suffix) for kind, suffix in
                 [('socket', '.sock'), ('state', '.json'), ('lock', '.lock'), ('log', '.log'), ('stop', '.stop')]}
        started = self.job / 'operation-started'
        self.state.update(owner_token='test-owner', deadline=time.time() + (30 if stop_requested else 3))
        relay.persist(self.job, self.state)
        authority = {'job_dir': str(self.job), 'owner_token': 'test-owner', 'deadline': self.state['deadline']}
        proc = multiprocessing.get_context('fork').Process(target=serve_hung_fixture,
                                                           args=('test', files, started))
        proc.start()
        result = []

        def blocked_call():
            try:
                relay.session_rpc('test', 'probe', [None, 'https://chatgpt.com'], self.state['job_id'], authority)
            except relay.BrowserError as exc:
                result.append(str(exc))

        try:
            until = time.time() + 2
            while not files['socket'].exists() and time.time() < until:
                time.sleep(0.01)
            self.assertTrue(files['socket'].exists())
            with patch.object(relay, 'session_files', return_value=files):
                relay.session_rpc('test', 'open', ['https://chatgpt.com/'], self.state['job_id'], authority)
                thread = threading.Thread(target=blocked_call, daemon=True)
                thread.start()
                until = time.time() + 2
                while not started.exists() and time.time() < until:
                    time.sleep(0.01)
                self.assertTrue(started.exists())
                if stop_requested:
                    files['stop'].touch()
                thread.join(timeout=4)
                self.assertFalse(thread.is_alive(), 'Hung session did not disconnect within its bound')
            proc.join(timeout=2)
            self.assertFalse(proc.is_alive())
            self.assertTrue(result)
            self.assertFalse(json.loads(files['state'].read_text())['connected'])
        finally:
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=2)

    def test_session_deadline_stops_hung_browser_operation(self):
        self.run_hung_session_case(False)

    def test_session_stop_is_not_queued_behind_a_hung_operation(self):
        self.run_hung_session_case(True)

    def test_explicit_rejection_retries_once_in_same_browser_with_original_budget(self):
        self.state.update(status='waiting', tab=[1, 2])
        relay.save(self.job, self.state)
        browser = FakeBrowser(self.state)
        browser.submissions = 1
        browser.refreshed = False
        original = browser.probe

        def rejected_once(tab, origin=None):
            info = original(tab, origin)
            if not browser.refreshed:
                info['errors'] = ['你的账户已切换。请重新发送消息']
                info['answers'] = []
            return info

        def refresh(tab):
            browser.refreshed = True

        browser.probe, browser.refresh = rejected_once, refresh
        with patch.object(browser, 'open') as opened, patch.object(relay, 'Chrome', return_value=browser):
            relay.worker(self.job, os.getppid())
        opened.assert_not_called()
        result = relay.read(self.job)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['attempts'], 2)
        self.assertEqual(result['deadline'], self.state['deadline'])
        self.assertEqual(browser.submissions, 2)

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
            def close(_):
                pass

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

    def test_attached_browser_is_disconnected_without_closing_user_context(self):
        from unittest.mock import Mock
        browser = relay.PlaywrightBrowser({'browser': 'existing', 'cdp_endpoint': 'chrome'})
        browser.context = Mock()
        browser.driver = Mock()
        browser.close()
        browser.context.close.assert_not_called()
        browser.driver.stop.assert_called_once()
        self.assertIsNone(browser.profile_lock)

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
            with patch.object(relay.time, "sleep"), patch.object(relay, "stop_child"):
                relay.supervise(self.job)
        self.assertEqual(relay.read(self.job)["status"], "completed")
        self.assertEqual(relay.read(self.job)["attempts"], 2)
        self.assertEqual(browser.submissions, 1)

    def test_dead_leader_does_not_leave_a_term_ignoring_descendant(self):
        ready = self.job / 'descendant-ready'
        descendant = ('import signal,time,pathlib; signal.signal(signal.SIGTERM,signal.SIG_IGN); '
                      'pathlib.Path(' + repr(str(ready)) + ').touch(); time.sleep(30)')
        leader = ('import subprocess,sys,time,pathlib; '
                  'p=subprocess.Popen([sys.executable,"-c",' + repr(descendant) + '],'
                  'stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); '
                  '\nwhile not pathlib.Path(' + repr(str(ready)) + ').exists(): time.sleep(0.01)\n'
                  'print(p.pid,flush=True)')
        child = subprocess.Popen([relay.sys.executable, '-c', leader], stdout=subprocess.PIPE,
                                 text=True, start_new_session=True)
        try:
            pid = int(child.stdout.readline())
            child.wait(timeout=5)
            relay.stop_child(child)
            until = time.time() + 2
            while time.time() < until:
                state = subprocess.run(['ps', '-p', str(pid), '-o', 'stat='], text=True, capture_output=True)
                if not state.stdout.strip() or state.stdout.strip().startswith('Z'):
                    break
                time.sleep(0.05)
            else:
                self.fail('Owned descendant survived cleanup after its leader exited')
        finally:
            try:
                os.killpg(child.pid, relay.signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.stdout.close()


if __name__ == "__main__":
    unittest.main()
