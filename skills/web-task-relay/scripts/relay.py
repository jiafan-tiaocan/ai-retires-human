#!/usr/bin/env python3
"""Task-scoped web relay. Native browser control; no model monitoring."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import select
import socket
import socketserver
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
import uuid

TERMINAL = {"completed", "needs_attention", "timed_out", "cancelled"}
SENT = {"submitting", "waiting"}
SESSION_VERSION = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


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
    def close(self):
        pass  # Existing user Chrome and its result tab are retained.

    def refresh(self, tab):
        return self.evaluate(tab, 'JSON.stringify((()=>{location.reload();return {refreshed:true};})())')

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
            users:text('[data-message-author-role=user],[data-message-role=user],[data-user-message-bubble]'),
            answers:text('[data-message-author-role=assistant],[data-message-role=assistant],[data-content-search-unit-key$=":assistant"],[data-assistant-message]'),
            errors:text('main aside[role=alert]'),
            busy:[...document.querySelectorAll('[data-testid=stop-button],button[aria-label="Stop generating"],button[aria-label="停止生成"],button[aria-label="停止"],button[aria-label="Stop"]')].some(visible),
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


class EgoBrowser(Chrome):
    """Ego Lite's official CLI; durable TaskSpace/Page labels survive workers."""
    def __init__(self, state, job):
        self.state, self.job = state, job
        self.space = state.get('ego_space') or ('web-task-relay-' + state['job_id'])

    def run(self, body, tab=None, mutation=False):
        config = {'space': tab[0] if tab else self.space, 'label': tab[1] if tab else None,
                  'job': str(self.job), 'owner': self.state.get('owner_token'),
                  'reuse': self.state.get('ego_space') is not None}
        # No shell interpolation, inherited env or module-level browser handles.
        source = 'const cfg=' + json.dumps(config) + ';\n' + r"""
const fs = await import('node:fs/promises');
async function guard() {
  const s = JSON.parse(await fs.readFile(cfg.job+'/status.json','utf8'));
  if(s.owner_token!==cfg.owner || ['completed','cancelled','timed_out','needs_attention'].includes(s.status)
     || Date.now()/1000>=s.deadline) throw new Error('RELAY_OWNERSHIP_LOST');
  try {await fs.access(cfg.job+'/cancel');} catch {return;}
  throw new Error('RELAY_OWNERSHIP_LOST');
}
const task = await taskSpace(cfg.space);
let page = cfg.label ? task.page(cfg.label) : task.page('p1');
"""
        if mutation:
            source += 'await guard();\n'
        source += body + '\nconsole.log("RELAY_JSON:"+JSON.stringify(result));\n'
        try:
            out = subprocess.run(['ego-browser', 'nodejs'], input=source, text=True,
                                 capture_output=True, timeout=min(35, max(1, self.state['deadline']-time.time())))
        except FileNotFoundError as exc:
            raise Attention('Install Ego Lite and its official ego-browser CLI first') from exc
        except subprocess.TimeoutExpired as exc:
            raise BrowserError('Ego Lite operation timed out; submission state retained') from exc
        if out.returncode:
            detail = (out.stderr + out.stdout)[-2500:]
            if 'RELAY_OWNERSHIP_LOST' in detail:
                raise OwnershipLost()
            if 'RELAY_ORIGIN_CHANGED' in detail or 'RELAY_TARGET_CHANGED' in detail:
                raise Attention('Task page origin or composer changed; original submission state retained')
            if any(x in detail for x in ('EGO_TASK_SPACE_NOT_CONTROLLED', 'EGO_USER', 'EGO_TASK_SPACE_INACTIVE',
                                         'EGO_TASK_SPACE_NAME_IN_USE', 'handOff', 'takeOver', 'control')):
                raise Attention('Ego Lite yielded control; resume only after the user hands this TaskSpace back')
            raise BrowserError('Ego Lite operation failed; inspect its TaskSpace before changing the connection')
        for line in reversed((out.stdout + "\n" + out.stderr).splitlines()):
            if line.startswith('RELAY_JSON:'):
                return json.loads(line[len('RELAY_JSON:'):])
        raise BrowserError('Ego Lite returned no operation receipt')

    def open(self, url):
        return self.run("""
// Record allocation before navigation so a retry keeps the same Page.
const mapping=cfg.job+'/ego-page.json';
let saved;
try {saved=JSON.parse(await fs.readFile(mapping,'utf8'));}
catch(e) {if(e.code!=='ENOENT') throw e;}
if(saved) {
  if(saved[0]!==task.spaceId) throw new Error('RELAY_SPACE_CHANGED');
  page=task.page(saved[1]);
} else {
  if(cfg.reuse || await page.url()!=='about:blank') page=await task.newPage();
  await guard();
  const tmp=mapping+'.tmp';
  await fs.writeFile(tmp,JSON.stringify([task.spaceId,page.label]),{mode:0o600});
  await fs.rename(tmp,mapping);
}
await guard();
await page.goto(URL_VALUE);
const result=[task.spaceId,page.label];
""".replace('URL_VALUE', json.dumps(url)), mutation=True)

    def evaluate(self, tab, code):
        return self.run('const result=JSON.parse(await page.evaluate('+json.dumps(code)+'));', tab)

    def native_target(self, tab, origin, action, prompt=None):
        config = {'origin': origin, 'token': uuid.uuid4().hex, 'send': action == 'send'}
        # Bind native input to one marked DOM node in the original document.
        # A navigation between observation and input destroys that node/selector.
        body = """
const target=await page.evaluate(({origin,token,send})=>{
  if(location.origin!==origin) return {origin_changed:true};
  const visible=e=>!!(e.offsetWidth||e.offsetHeight||e.getClientRects().length);
  const editors=[...document.querySelectorAll('[contenteditable=true][role=textbox],#prompt-textarea,textarea')].filter(visible);
  if(editors.length!==1) return {};
  let element=editors[0];
  if(send) {
    const selector='button[aria-label="发送"],button[aria-label="Send"],button[aria-label="Send prompt"],button[aria-label="发送提示"],[data-testid="send-button"]';
    let scope=element.parentElement;
    while(scope && !scope.querySelector(selector)) scope=scope.parentElement;
    const buttons=scope ? [...scope.querySelectorAll(selector)].filter(e=>visible(e)&&!e.disabled) : [];
    if(buttons.length!==1) return {};
    element=buttons[0];
  }
  element.setAttribute('data-relay-target',token);
  return {selector:'[data-relay-target="'+token+'"]'};
}, TARGET_CONFIG);
if(target.origin_changed) throw new Error('RELAY_ORIGIN_CHANGED');
if(!target.selector) throw new Error('RELAY_TARGET_CHANGED');
await guard();
""".replace('TARGET_CONFIG', json.dumps(config))
        if action == 'send':
            body += "await page.click(target.selector,{timeout:5000,label:'submit planning task'});const result={clicked:true};"
        else:
            body += 'await page.fill(target.selector,'+json.dumps(prompt)+',{timeout:10000});const result={ok:true};'
        return self.run(body, tab, mutation=True)

    def fill(self, tab, prompt, origin):
        return self.native_target(tab, origin, 'fill', prompt)

    def send(self, tab, origin):
        return self.native_target(tab, origin, 'send')

    def refresh(self, tab):
        return self.run('await page.reload(); const result={refreshed:true};', tab, mutation=True)

    def close(self):
        pass  # Keep the same native TaskSpace available for collect/recovery.


class PlaywrightBrowser(Chrome):
    """Own a dedicated browser/profile; reuse DOM operations without Apple Events."""
    def __init__(self, state):
        self.borrowed = state["browser"] == "existing"
        self.endpoint = state.get("cdp_endpoint", "chrome")
        self.profile_lock = None
        if not self.borrowed:
            self.profile = Path(state["profile_dir"])
            self.profile.parent.mkdir(parents=True, exist_ok=True)
            self.profile_lock = own_lock(self.profile.with_name(self.profile.name + ".relay.lock"))
            if self.profile_lock is None:
                raise BrowserError("Dedicated browser profile busy; close its login window or wait for its job")
        self.driver = None
        self.context = None
        self.page = None
        self.engine = state["browser"]
        self.channel = state.get("channel", "chrome")
        self.headless = state.get("headless", False)
        self.login_navigation = state.get("login_navigation", False)
        self.action_guard = None

    def open(self, url):
        try:
            if self.driver is None:
                from playwright.sync_api import sync_playwright
                self.driver = sync_playwright().start()
                if self.borrowed:
                    connected = self.driver.chromium.connect_over_cdp(self.endpoint, timeout=30000, no_defaults=True)
                    self.context = connected.contexts[0]
                else:
                    engine = getattr(self.driver, self.engine)
                    channel = {"channel": self.channel} if self.engine == "chromium" and self.channel != "chromium" else {}
                    self.context = engine.launch_persistent_context(
                        str(self.profile), headless=self.headless, timeout=20000,
                        viewport={"width": 1280, "height": 900}, **channel)
                self.context.set_default_timeout(15000)
            if self.action_guard:
                self.action_guard()
            self.page = (self.context.pages[0] if not self.borrowed and self.page is None and self.context.pages
                         else self.context.new_page())
            page = self.page
            allowed = urlsplit(url)

            def protect_task_origin(route):
                request = route.request
                target = urlsplit(request.url)
                if (request.is_navigation_request() and request.frame == page.main_frame
                        and (target.scheme, target.netloc) != (allowed.scheme, allowed.netloc)):
                    route.abort()
                else:
                    route.continue_()

            if not self.login_navigation:
                page.route('**/*', protect_task_origin)
            if self.action_guard:
                self.action_guard()
            self.page.goto(url, wait_until="domcontentloaded", timeout=20000)
            self.page.bring_to_front()
            return "playwright-page"
        except ImportError as exc:
            raise Attention("Install the skill's requirements.txt in this Python environment") from exc
        except Exception as exc:
            if self.borrowed and "DevToolsActivePort" in str(exc):
                raise Attention("In your logged-in Chrome open chrome://inspect/#remote-debugging, enable remote debugging, and allow this connection") from exc
            raise BrowserError("Direct browser launch/navigation failed (" + type(exc).__name__ + ")") from exc

    def evaluate(self, tab, code):
        if self.page is None:
            raise BrowserError("Browser process restarted; original page needs recovery")
        try:
            return json.loads(self.page.evaluate(code))
        except Exception as exc:
            raise BrowserError("Direct browser page operation failed (" + type(exc).__name__ + ")") from exc

    def fill(self, tab, prompt, origin):
        if self.page is None:
            raise BrowserError("Task page is unavailable")
        expected = urlsplit(origin)
        actual = urlsplit(self.page.url)
        if (actual.scheme, actual.netloc) != (expected.scheme, expected.netloc):
            return {"ok": False, "origin_changed": True}
        try:
            editor = self.page.locator('[contenteditable=true][role=textbox]:visible,#prompt-textarea:visible,textarea:visible').first
            editor.wait_for(state='visible', timeout=15000)
            if self.action_guard:
                self.action_guard()
            editor.fill(prompt, timeout=15000)
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error_type": type(exc).__name__}

    def refresh(self, tab):
        if self.page is None:
            raise BrowserError("Task page is unavailable")
        try:
            if self.action_guard:
                self.action_guard()
            self.page.reload(wait_until='domcontentloaded', timeout=20000)
            self.page.bring_to_front()
        except Exception as exc:
            raise BrowserError("Could not refresh the task page") from exc

    def send(self, tab, origin):
        if self.page is None:
            raise BrowserError("Task page is unavailable")
        expected, actual = urlsplit(origin), urlsplit(self.page.url)
        if (actual.scheme, actual.netloc) != (expected.scheme, expected.netloc):
            return {"clicked": False, "origin_changed": True}
        try:
            self.page.bring_to_front()
            editor = self.page.locator('[contenteditable=true][role=textbox]:visible,#prompt-textarea:visible,textarea:visible').first
            scope = editor.locator('xpath=ancestor::*[descendant::button[@aria-label="发送" or @aria-label="Send" or @aria-label="Send prompt" or @aria-label="发送提示" or @data-testid="send-button"]][1]')
            buttons = scope.locator('[data-testid=send-button]:visible,button[aria-label="Send prompt"]:visible,button[aria-label="发送提示"]:visible,button[aria-label="发送"]:visible,button[aria-label="Send"]:visible')
            if buttons.count() != 1:
                return {"clicked": False}
            buttons.first.click(trial=True, timeout=5000)
            if self.action_guard:
                self.action_guard()
            buttons.first.click(timeout=1000, no_wait_after=True)
            return {"clicked": True}
        except Exception as exc:
            return {"clicked": False, "error_type": type(exc).__name__}

    def close(self):
        try:
            if self.context is not None and not self.borrowed:
                self.context.close()
        finally:
            try:
                if self.driver is not None:
                    self.driver.stop()
            finally:
                if self.profile_lock is not None:
                    self.profile_lock.close()


def connect_browser(profile, engine, url, channel="chrome"):
    browser = PlaywrightBrowser({"profile_dir": str(profile), "browser": engine, "channel": channel,
                                 "login_navigation": True})
    try:
        browser.open(url)
        print(json.dumps({"browser": engine, "url": url,
                          "instruction": "Complete login in the dedicated browser, then CLOSE that browser window. The profile will be reused."}), flush=True)
        while browser.context.pages:
            try:
                browser.context.pages[0].wait_for_timeout(500)
            except Exception:
                break
        print(json.dumps({"connection_closed": True, "profile_ready_for_jobs": str(profile)}), flush=True)
    finally:
        browser.close()


def default_profile(engine):
    return Path.home() / ".local/share/web-task-relay/profiles" / engine


def session_files(endpoint):
    folder = Path.home() / '.local/share/web-task-relay/sessions'
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = hashlib.sha256(endpoint.encode()).hexdigest()[:12]
    return {kind: folder / (key + suffix) for kind, suffix in
            [('socket', '.sock'), ('state', '.json'), ('lock', '.lock'), ('log', '.log'), ('stop', '.stop')]}


def session_rpc(endpoint, method, args=None, job_id=None, auth=None):
    files = session_files(endpoint)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(75)
        client.connect(str(files['socket']))
        request = {'method': method, 'args': args or [], 'job_id': job_id, 'auth': auth}
        client.sendall((json.dumps(request) + '\n').encode())
        with client.makefile('rb') as stream:
            raw = stream.readline(4 * 1024 * 1024)
    if not raw:
        raise BrowserError('Browser session disconnected; submission outcome is not inferred')
    response = json.loads(raw)
    if not response.get('ok'):
        error = Attention if response.get('kind') == 'Attention' else BrowserError
        raise error(response.get('error', 'Browser session operation failed'))
    return response['result']


def start_session(endpoint):
    files = session_files(endpoint)
    try:
        info = session_rpc(endpoint, 'ping')
        if info['version'] != SESSION_VERSION:
            raise Attention('Browser session uses an older runtime; session-stop then session-start to reload it')
        return info
    except (OSError, ValueError, BrowserError):
        pass
    atomic(files['state'], json.dumps({'connected': False, 'starting': True, 'version': SESSION_VERSION}))
    with files['log'].open('a') as log:
        child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '_session',
                                  '--cdp-endpoint', endpoint], stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=log, start_new_session=True)
    return {'session_starting_pid': child.pid, 'endpoint': endpoint,
            'instruction': 'Allow the Chrome connection once; this local session is reused until stopped.'}


def connection_alive(connection):
    try:
        ready, _, _ = select.select([connection], [], [], 0)
        return not ready or connection.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT) != b''
    except (OSError, ValueError):
        return False


def validate_session_job(request, connection):
    auth = request.get('auth') or {}
    if not isinstance(auth.get('job_dir'), str) or not auth.get('owner_token'):
        raise Attention('Missing job execution authority')
    job = Path(auth['job_dir'])
    state = read(job)
    if (state['job_id'] != request['job_id'] or state.get('owner_token') != auth['owner_token']
            or state['deadline'] != auth.get('deadline') or time.time() >= state['deadline']
            or state['status'] in TERMINAL or (job / 'cancel').exists()):
        raise Attention('Job expired, cancelled, completed or lost ownership; browser action refused')
    if not connection_alive(connection):
        raise Attention('Requesting worker disconnected; browser action refused')
    if request['method'] == 'send' and state['status'] != 'submitting':
        raise Attention('No persisted submission intent; send refused')
    return state


def serve_session(endpoint):
    files = session_files(endpoint)
    lock = own_lock(files['lock'])
    if lock is None:
        return 0
    root = None
    server = None
    done = threading.Event()
    active = {'deadline': time.monotonic() + 45, 'request': None, 'connection': None}
    stop_seen = [None]

    def watchdog():
        while not done.wait(0.1):
            now = time.monotonic()
            reason = None
            snapshot = active.copy()
            if files['stop'].exists():
                if stop_seen[0] is None:
                    stop_seen[0] = now
                if server is not None:
                    server.stop_requested = True
                if now - stop_seen[0] >= 1:
                    reason = 'Browser session stopped by user'
            if (snapshot['deadline'] is not None and now >= snapshot['deadline']
                    and active['deadline'] == snapshot['deadline']):
                reason = 'Browser session operation stalled; restart the session to reconnect'
            request, connection = snapshot['request'], snapshot['connection']
            if request and request['method'] in {'open', 'fill', 'send', 'refresh'}:
                try:
                    validate_session_job(request, connection)
                except (Attention, OSError, ValueError, KeyError):
                    if active['request'] is request:
                        reason = 'Browser operation cancelled or lost its worker; session disconnected'
            if reason:
                atomic(files['state'], json.dumps({'connected': False, 'error': reason}))
                os._exit(1)  # The connector is ours; Chrome is borrowed and not killed.

    with lock:
        files['stop'].unlink(missing_ok=True)
        threading.Thread(target=watchdog, daemon=True).start()
        try:
            root = PlaywrightBrowser({'browser': 'existing', 'cdp_endpoint': endpoint})
            root.open('about:blank')
            root.page.close()  # Only the connector's own empty page.
            pages = {}

            class Handler(socketserver.StreamRequestHandler):
                def handle(self):
                    try:
                        self.request.settimeout(10)
                        request = json.loads(self.rfile.readline(4 * 1024 * 1024))
                        method, args, key = request['method'], request.get('args', []), request.get('job_id')
                        if method == 'ping':
                            result = {'connected': True, 'pid': os.getpid(), 'version': SESSION_VERSION}
                        elif method == 'shutdown':
                            self.server.stop_requested = True
                            result = {'stopping': True}
                        elif method in {'open', 'probe', 'fill', 'send', 'refresh'} and isinstance(key, str):
                            state = validate_session_job(request, self.request)
                            active.update(deadline=time.monotonic() + min(40, max(0, state['deadline'] - time.time())),
                                          request=request, connection=self.request)
                            if key not in pages:
                                if method != 'open':
                                    raise BrowserError('Session has no page for this job; recover the recorded conversation')
                                page = PlaywrightBrowser({'browser': 'existing', 'cdp_endpoint': endpoint})
                                page.driver, page.context = root.driver, root.context
                                pages[key] = page
                            pages[key].action_guard = lambda: validate_session_job(request, self.request)
                            try:
                                result = getattr(pages[key], method)(*args)
                            finally:
                                pages[key].action_guard = None
                        else:
                            raise BrowserError('Unsupported browser-session operation')
                        response = {'ok': True, 'result': result}
                    except (Attention, BrowserError, OSError, ValueError, KeyError) as exc:
                        response = {'ok': False, 'kind': type(exc).__name__, 'error': str(exc)}
                    finally:
                        active.update(deadline=None, request=None, connection=None)
                    try:
                        self.wfile.write((json.dumps(response) + '\n').encode())
                    except (BrokenPipeError, ConnectionResetError):
                        pass

            files['socket'].unlink(missing_ok=True)
            server = socketserver.UnixStreamServer(str(files['socket']), Handler)
            os.chmod(files['socket'], 0o600)
            server.timeout, server.stop_requested = 0.5, False
            atomic(files['state'], json.dumps({'connected': True, 'pid': os.getpid(), 'version': SESSION_VERSION}))
            active['deadline'] = None
            while not server.stop_requested:
                server.handle_request()
        except (Attention, BrowserError, OSError) as exc:
            atomic(files['state'], json.dumps({'connected': False, 'error': str(exc)}))
            return 1
        finally:
            active.update(deadline=time.monotonic() + 3, request=None, connection=None)
            if server is not None:
                server.server_close()
                files['socket'].unlink(missing_ok=True)
            if root is not None:
                root.close()  # Disconnect; never close the borrowed browser.
            done.set()
    return 0


class SessionBrowser:
    """Workers share one authorized browser connection over a private local socket."""
    def __init__(self, state, job):
        self.endpoint, self.key = state.get('cdp_endpoint', 'chrome'), state['job_id']
        self.state, self.job = state, job

    def _call(self, method, *args):
        until = time.monotonic() + 35
        while True:
            try:
                return session_rpc(self.endpoint, method, list(args), self.key,
                                   {'job_dir': str(self.job), 'owner_token': self.state.get('owner_token'),
                                    'deadline': self.state['deadline'], 'request_id': uuid.uuid4().hex})
            except (FileNotFoundError, ConnectionRefusedError):
                if time.monotonic() >= until:
                    files = session_files(self.endpoint)
                    if files['state'].exists():
                        info = json.loads(files['state'].read_text())
                        if info.get('error'):
                            raise Attention(info['error'])
                    raise Attention('Browser session unavailable; start it and allow the Chrome connection')
                time.sleep(0.2)

    def open(self, url):
        return self._call('open', url)

    def probe(self, tab, origin):
        return self._call('probe', tab, origin)

    def fill(self, tab, prompt, origin):
        return self._call('fill', tab, prompt, origin)

    def send(self, tab, origin):
        return self._call('send', tab, origin)

    def refresh(self, tab):
        return self._call('refresh', tab)

    def close(self):
        pass  # Session lifetime is explicitly controlled, independent of a worker.


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
    elif isinstance(browser, PlaywrightBrowser):
        # A restarted browser cannot reuse an old in-memory page handle.
        recovery_url = state.get("conversation_url", state["url"])
        if state["status"] in SENT and "/c/" not in urlsplit(recovery_url).path:
            raise Attention("Original conversation URL was not recorded; no automatic resend")
        state["tab"] = browser.open(recovery_url)
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
            if isinstance(browser, SessionBrowser) and state['status'] not in SENT and not recovered:
                state['tab'] = browser.open(state['url'])
                recovered = True
                save(job, state)
                continue
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
        if info.get("errors"):
            stale_account = any(('你的账户已切换' in e and '重新发送' in e)
                                or ('Your account has changed' in e and 'send' in e.lower())
                                for e in info['errors'])
            known_rejection = state['status'] in SENT and not info['answers'] and not info['busy']
            if (stale_account and (state['status'] not in SENT or known_rejection) and not state.get('refresh_used')
                    and state['attempts'] < state['max_attempts']):
                state['attempts'] += 1
                state['refresh_used'] = True
                state['last_rejection'] = '; '.join(info['errors'])[:400]
                # The provider explicitly rejected this read-only inference request.
                # This is different from a lost receipt or an unknown send outcome.
                state['status'] = 'queued'
                save(job, state)
                try:
                    browser.refresh(state['tab'])
                except BrowserError as exc:
                    raise Attention('Page refresh failed before submission; inspect the original tab') from exc
                continue
            raise Attention("Website reported an error; original conversation retained: " + "; ".join(info["errors"])[:400])
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
                raise Attention("Could not fill the task-owned composer; no submission and no automatic reconnect")
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
            # Loading/generation can be slow. A missing user marker never authorizes
            # a resend; keep observing within the original deadline instead of
            # inventing a shorter confirmation timeout.
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
        browser = None
        try:
            mode = state.get('browser', 'apple-events')
            browser = EgoBrowser(state, job) if mode == 'egolite' else (Chrome() if mode == 'apple-events' else (SessionBrowser(state, job) if mode == 'existing'
                                                               else PlaywrightBrowser(state)))
            observe(job, state, browser, owner_pid)
        except Attention as exc:
            if os.getppid() == owner_pid:
                finish(job, state, "needs_attention", str(exc))
        except BrowserError as exc:
            if os.getppid() == owner_pid:
                state["last_error"] = str(exc)
                save(job, state)
            return 1
        finally:
            if browser is not None:
                browser.close()
    return 0


def stop_child(child):
    try:
        # Clean up this owned group even when the worker died before its browser.
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    until = time.monotonic() + 3
    while time.monotonic() < until:
        child.poll()  # Reap the leader; descendants may still own this group.
        try:
            os.killpg(child.pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
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
                    if time.time() - state["updated_at"] > (90 if state['status'] == 'queued' else 45):
                        stop_child(child)
                        state = read(job)
                        state["last_error"] = "Worker heartbeat stalled"
                        save(job, state)
                        break
                    time.sleep(0.5)
                child.wait()
                stop_child(child)
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
    start.add_argument("--browser", choices=("egolite", "existing", "firefox", "chromium", "apple-events"), default="egolite")
    start.add_argument("--ego-space", help="Existing agent-owned TaskSpace numeric id; otherwise one durable space per job")
    start.add_argument("--cdp-endpoint", default="chrome")
    start.add_argument("--channel", choices=("chrome", "chromium"), default="chrome")
    start.add_argument("--profile-dir", type=Path)
    start.add_argument("--headless", action="store_true")
    connect = sub.add_parser("connect", help="Log in once in a dedicated persistent browser")
    connect.add_argument("--browser", choices=("firefox", "chromium"), default="chromium")
    connect.add_argument("--channel", choices=("chrome", "chromium"), default="chrome")
    connect.add_argument("--profile-dir", type=Path)
    connect.add_argument("--url", default="https://chatgpt.com/")
    for name in ('session-start', 'session-stop', '_session'):
        sub.add_parser(name).add_argument('--cdp-endpoint', default='chrome')
    for name in ("status", "collect", "cancel", "resume", "_supervise"):
        sub.add_parser(name).add_argument("job_dir", type=Path)
    internal_worker = sub.add_parser("_worker")
    internal_worker.add_argument("job_dir", type=Path)
    internal_worker.add_argument("--owner-pid", type=int, required=True)
    args = parser.parse_args(argv)
    if args.command == 'session-start':
        print(json.dumps(start_session(args.cdp_endpoint)))
        return 0
    if args.command == 'session-stop':
        session_files(args.cdp_endpoint)['stop'].touch(exist_ok=True)
        print(json.dumps({'stop_requested': True}))
        return 0
    if args.command == '_session':
        return serve_session(args.cdp_endpoint)
    if args.command == "connect":
        if not allowed_url(args.url):
            parser.error("Unsupported login URL")
        profile = (args.profile_dir or default_profile(args.browser)).resolve()
        connect_browser(profile, args.browser, args.url, args.channel)
        return 0
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
                   "poll_seconds": args.poll, "url": args.url, "browser": args.browser,
                   "channel": args.channel,
                   "ego_space": int(args.ego_space) if args.ego_space and args.ego_space.isdigit() else args.ego_space,
                   "cdp_endpoint": args.cdp_endpoint,
                   "profile_dir": str((args.profile_dir or default_profile(args.browser)).resolve()),
                   "headless": args.headless})
        if args.browser == 'existing':
            start_session(args.cdp_endpoint)
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
    except (OSError, ValueError, BrowserError, Attention) as exc:
        print("Relay error: " + str(exc), file=sys.stderr)
        sys.exit(2)
