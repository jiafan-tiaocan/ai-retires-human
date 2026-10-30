"""Real browser transport and recovery against a persistent loopback fixture."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid

RUNNER = Path(__file__).resolve().parents[1] / "skills/web-task-relay/scripts/relay.py"
spec = importlib.util.spec_from_file_location("relay", RUNNER)
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)
HTML = b'''<!doctype html><meta charset="utf-8"><title>Relay transport fixture</title>
<h1>Local synthetic fixture; no model calls</h1>
<textarea id="prompt-textarea"></textarea><button data-testid="send-button">Send</button>
<main id="messages"></main><script>
const id=location.pathname.split('/').pop();
async function render(){
 const s=await (await fetch('/state/'+id)).json();if(!s.prompt)return;
 const main=document.querySelector('main');main.replaceChildren();
 const u=document.createElement('div');u.dataset.messageAuthorRole='user';u.innerText=s.prompt;main.append(u);
 const a=document.createElement('div');a.dataset.messageAuthorRole='assistant';a.innerText=s.answer;main.append(a);
 if(!s.done){const stop=document.createElement('button');stop.dataset.testid='stop-button';stop.innerText='Generating';main.append(stop)}
}
document.querySelector('button').onclick=async()=>{
 await fetch('/submit/'+id,{method:'POST',body:document.querySelector('textarea').value});await render();
};
setInterval(render,200);render();
</script>'''
SCENARIOS = ('normal', 'worker-crash', 'supervisor-crash', 'borrowed-browser')


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith('/state/'):
            ident = self.path.split('/')[-1]
            state = self.server.jobs.get(ident, {})
            done = time.time() >= state.get('ready_at', float('inf'))
            prompt = state.get('prompt', '')
            result = {'prompt': prompt, 'done': done,
                      'answer': ('fixture-answer: 2\n' + prompt.strip().splitlines()[-1])
                      if done else 'partial'}
            data, content_type = json.dumps(result).encode(), 'application/json'
        else:
            data, content_type = HTML, 'text/html; charset=utf-8'
            if self.server.editor == 'contenteditable':
                data = data.replace(b'<textarea id="prompt-textarea"></textarea>',
                                    b'<div role="textbox" contenteditable="true"><p><br></p></div>')
                data = data.replace(b"document.querySelector('textarea').value",
                                    b"[...document.querySelector('[role=textbox]').childNodes].map(n=>n.textContent).join('\\n')")
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass  # The tested browser may have just crashed.

    def do_POST(self):
        ident = self.path.split('/')[-1]
        prompt = self.rfile.read(int(self.headers['Content-Length'])).decode()
        previous = self.server.jobs.get(ident, {})
        self.server.jobs[ident] = {'prompt': prompt, 'ready_at': time.time() + 5,
                                   'submissions': previous.get('submissions', 0) + 1}
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


def command(*args):
    return subprocess.run([sys.executable, str(RUNNER), *map(str, args)],
                          text=True, capture_output=True, check=True, timeout=25)


def await_state(job, predicate, seconds=45):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if (job / 'status.json').exists():
            state = relay.read(job)
            if predicate(state):
                return state
        time.sleep(0.1)  # Ordinary test code; no agent/model calls.
    raise AssertionError('Fixture deadline exceeded: ' + (job / 'status.json').read_text())


def run(scenarios=SCENARIOS, editor='contenteditable', ego_space=None):
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.jobs = {}
    server.editor = editor
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = 'http://127.0.0.1:' + str(server.server_port)
    root = Path(tempfile.mkdtemp(prefix='relay-fixture-'))
    packet = root / 'packet.md'
    packet.write_text('Objective: add 1 and 1.\nDeliverable: 2.\nAcceptance: answer must include 2.')
    jobs = []
    owner_driver = None
    owner_context = None
    fixture_endpoint = None
    passed = False
    try:
        for scenario in scenarios:
            job = root / scenario
            ident = uuid.uuid4().hex
            browser_args = ['--browser', 'chromium', '--channel', 'chrome', '--headless']
            if scenario == 'egolite-worker-crash':
                if ego_space is None:
                    raise ValueError('Pass the existing agent-owned goal --ego-space ID')
                browser_args = ['--browser', 'egolite', '--ego-space', str(ego_space)]
            if scenario == 'borrowed-browser':
                from playwright.sync_api import sync_playwright
                owner_driver = sync_playwright().start()
                owner_profile = root / 'borrowed-owner'
                owner_context = owner_driver.chromium.launch_persistent_context(
                    str(owner_profile), channel='chrome', headless=True,
                    args=['--remote-debugging-port=0'])
                # Connection metadata from this test's own anonymous profile only.
                port = int((owner_profile / 'DevToolsActivePort').read_text().splitlines()[0])
                fixture_endpoint = 'http://127.0.0.1:' + str(port)
                browser_args = ['--browser', 'existing', '--cdp-endpoint', fixture_endpoint]
            receipt = json.loads(command('start', job, '--prompt-file', packet,
                                         '--url', url + '/c/' + ident, '--timeout', '45',
                                         '--poll', '0.2', '--profile-dir', root / ('profile-' + scenario),
                                         *browser_args).stdout)
            jobs.append((job, receipt))
            if scenario in ('worker-crash', 'supervisor-crash', 'egolite-worker-crash'):
                waiting = await_state(job, lambda s: s['status'] in {'waiting'} | relay.TERMINAL)
                assert waiting['status'] == 'waiting', waiting
                until = time.time() + 3
                while ident not in server.jobs and time.time() < until:
                    time.sleep(0.05)
                assert ident in server.jobs, 'Server must receive the task before the crash'
                budget = (waiting['deadline'], waiting['attempts'])
                if scenario in ('worker-crash', 'egolite-worker-crash'):
                    children = subprocess.check_output(['pgrep', '-P', str(receipt['supervisor_pid'])], text=True)
                    os.kill(int(children.strip().splitlines()[0]), signal.SIGKILL)
                else:
                    os.kill(receipt['supervisor_pid'], signal.SIGKILL)
                    time.sleep(3.1)
                    interrupted = json.loads(command('status', job).stdout)
                    assert interrupted['status'] == 'interrupted', interrupted
                    assert (interrupted['deadline'], interrupted['attempts']) == budget
                    receipt = json.loads(command('resume', job).stdout)
                    jobs[-1] = (job, receipt)
            state = await_state(job, lambda s: s['status'] in relay.TERMINAL)
            assert state['status'] == 'completed', state
            assert 'fixture-answer: 2' in command('collect', job).stdout
            sends = server.jobs[ident]['submissions']
            assert sends == 1, (scenario, sends)
            assert packet.read_text() in server.jobs[ident]['prompt'], 'Task text was not preserved'
            assert (job / 'event.json').is_file()
            if scenario == 'borrowed-browser':
                until = time.time() + 5
                while time.time() < until:
                    released = relay.own_lock(job / 'supervisor.lock')
                    if released is not None:
                        released.close()
                        break
                    time.sleep(0.05)
                else:
                    raise AssertionError('Borrowed-browser worker did not finish disconnecting')
                assert owner_context.pages[0].evaluate('1 + 1') == 2, 'Borrowed browser was closed by worker'
                first_session = relay.session_rpc(fixture_endpoint, 'ping')['pid']
                reused_job = root / 'same-session-worker-crash'
                reused_id = uuid.uuid4().hex
                reused_receipt = json.loads(command('start', reused_job, '--prompt-file', packet,
                    '--url', url + '/c/' + reused_id, '--timeout', '45', '--poll', '0.2',
                    '--browser', 'existing', '--cdp-endpoint', fixture_endpoint).stdout)
                jobs.append((reused_job, reused_receipt))
                ready = await_state(reused_job, lambda s: s['status'] in {'waiting'} | relay.TERMINAL)
                assert ready['status'] == 'waiting', ready
                until = time.time() + 3
                while reused_id not in server.jobs and time.time() < until:
                    time.sleep(0.05)
                assert reused_id in server.jobs
                child_ids = subprocess.check_output(['pgrep', '-P', str(reused_receipt['supervisor_pid'])], text=True)
                os.kill(int(child_ids.strip().splitlines()[0]), signal.SIGKILL)
                recovered = await_state(reused_job, lambda s: s['status'] in relay.TERMINAL)
                assert recovered['status'] == 'completed', recovered
                assert server.jobs[reused_id]['submissions'] == 1
                assert relay.session_rpc(fixture_endpoint, 'ping')['pid'] == first_session
                print(json.dumps({'scenario': 'reused-session-worker-crash', 'status': 'completed',
                                  'same_browser_session': True, 'submissions': 1}), flush=True)
            print(json.dumps({'scenario': scenario, 'status': state['status'], 'submissions': sends,
                              'attempts': state['attempts'], 'editor': editor,
                              'evidence': 'real browser; synthetic loopback server; no model'}), flush=True)
        passed = True
    finally:
        for job, receipt in jobs:
            if (job / 'status.json').exists() and relay.read(job)['status'] not in relay.TERMINAL:
                (job / 'cancel').touch()
        until = time.time() + 5
        for job, receipt in jobs:
            while time.time() < until:
                lock = relay.own_lock(job / 'supervisor.lock')
                if lock is not None:
                    lock.close()
                    break
                time.sleep(0.1)
            else:
                passed = False
                print('Supervisor still cleaning up; preserving fixture directory', flush=True)
        server.shutdown()
        server.server_close()
        if fixture_endpoint is not None:
            try:
                command('session-stop', '--cdp-endpoint', fixture_endpoint)
            except subprocess.CalledProcessError:
                pass  # Failed bootstrap may never have opened its private socket.
            session_files = relay.session_files(fixture_endpoint)
            until = time.time() + 5
            while time.time() < until:
                stopped = relay.own_lock(session_files['lock'])
                if stopped is not None:
                    stopped.close()
                    for file in session_files.values():
                        file.unlink(missing_ok=True)
                    break
                time.sleep(0.1)
            assert owner_context.pages[0].evaluate('1 + 1') == 2, 'Stopping connector closed the borrowed browser'
        if owner_context is not None:
            owner_context.close()
        if owner_driver is not None:
            owner_driver.stop()
        if passed:
            shutil.rmtree(root)
        else:
            print('Failed fixture evidence preserved at ' + str(root), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', choices=SCENARIOS + ('egolite-worker-crash',), action='append')
    parser.add_argument('--editor', choices=('textarea', 'contenteditable'), default='contenteditable')
    parser.add_argument('--ego-space', type=int)
    args = parser.parse_args()
    run(args.scenario or SCENARIOS, args.editor, args.ego_space)
