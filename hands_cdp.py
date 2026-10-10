"""Chromium pages (and Electron apps) through the DevTools protocol (CDP): one connection per browser, its pages as flat
sessions on it, and what its events tell (dialogs, downloads, network, loading). hands_a11y numbers what this reads.
Standard library only.

A browser is found by its DevTools port: an agent's Chromium (~/.config/chromium-bots/<profile>, run with
--remote-debugging-port=0) writes it to <profile>/DevToolsActivePort; an Electron app opened by `hands open app` runs
with --remote-debugging-port=N.
"""
import base64, json, os, socket, struct, threading, time, urllib.request

LIMIT = 120  # elements per read: the rest comes after scrolling

# The visible elements one can act on, and the headings, in page order. Each element keeps a key (data-hk) for the
# document's life, so the same element keeps its number between reads.
SNAPSHOT = r"""
(() => {
  const doc = window.__hkDoc || (window.__hkDoc = Math.random().toString(36).slice(2));
  const W = innerWidth, H = innerHeight, out = [];
  const visible = e => {
    const r = e.getBoundingClientRect();
    if (r.width < 2 || r.height < 2 || r.bottom < 0 || r.right < 0 || r.top > H || r.left > W) return false;
    const s = getComputedStyle(e);
    return s.visibility !== 'hidden' && s.display !== 'none' && +s.opacity > 0.05;
  };
  const clean = t => (t || '').replace(/\s+/g, ' ').trim().slice(0, 80);
  // The name a person sees: aria-label, the field's <label>, the text, the placeholder… the name attribute last.
  const label = e => clean(e.getAttribute('aria-label') || (e.labels && e.labels[0] && e.labels[0].innerText) ||
                           e.innerText || e.placeholder || e.title || e.alt || (e.type === 'submit' ? e.value : '') ||
                           e.getAttribute('name'));
  const kind = e => {
    const t = e.tagName.toLowerCase(), role = e.getAttribute('role'), type = (e.type || '').toLowerCase();
    if (t === 'a') return 'link';
    if (t === 'select') return 'menu';
    if (t === 'textarea' || e.isContentEditable) return 'field';
    if (t === 'input') {
      if (type === 'checkbox') return e.checked ? 'checkbox [x]' : 'checkbox [ ]';
      if (type === 'radio') return e.checked ? 'option (•)' : 'option ( )';
      if (['submit', 'button', 'reset'].includes(type)) return 'button';
      return 'field' + (type && type !== 'text' ? ' ' + type : '');
    }
    if (t === 'button' || role === 'button') return 'button';
    return role || 'clickable';
  };
  const sel = 'a[href], button, input:not([type=hidden]), select, textarea, [role=button], [role=link], [role=tab], ' +
              '[role=menuitem], [role=menuitemcheckbox], [role=menuitemradio], [role=checkbox], [role=radio], [role=switch], ' +
              '[role=option], [role=treeitem], [role=combobox], [role=textbox], [role=searchbox], [role=slider], ' +
              '[onclick], [contenteditable=true], summary';
  let n = 0, more = 0;
  for (const e of document.querySelectorAll(sel + ', h1, h2, h3')) {
    if (!visible(e) || e.disabled) continue;
    if (/^H[123]$/.test(e.tagName)) { const t = clean(e.innerText); if (t) out.push({line: '# ' + t}); continue; }
    if (n >= __LIMIT__) { more++; continue; }
    n++;
    if (!e.dataset.hk) e.dataset.hk = window.__hk = (window.__hk || 0) + 1;
    const k = kind(e), name = label(e);
    let line = k + ' "' + name + '"';
    if (e.tagName === 'SELECT') line += ' = "' + clean(e.options[e.selectedIndex]?.text) + '"';
    else if ((e.tagName === 'INPUT' || e.tagName === 'TEXTAREA') && !['checkbox', 'radio', 'submit', 'button'].includes(e.type) && e.value)
      line += ' = "' + (e.type === 'password' ? '••••' : clean(e.value)) + '"';
    const r = e.getBoundingClientRect(), p = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    if (p && !e.contains(p) && !p.contains(e) && !(e.labels && [...e.labels].some(l => l.contains(p)))) line += ' (covered)';
    out.push({key: e.dataset.hk, sig: k.split(' ')[0] + ' ' + name, line});
  }
  if (more) out.push({line: '(+' + more + ' more visible elements past the limit)'});
  if (document.documentElement.scrollHeight - scrollY - H > 40) out.push({line: '(the page goes on below: scroll to see more, then ui again)'});
  return {head: document.title + ' — ' + location.href, doc, items: out};
})()
""".replace("__LIMIT__", str(LIMIT))

# The text a person reads on the page, without runs of blank lines.
TEXT = r"""
(() => document.title + ' — ' + location.href + '\n' +
        (document.body?.innerText || '').replace(/[ \t\u00a0]+/g, ' ').replace(/\n\s*\n+/g, '\n').trim())()
"""

# The page as light markdown: headings, paragraphs, lists, tables, code and links; the main content when the page
# marks one (<main>, <article>).
READ = r"""
(() => {
  const root = document.querySelector('main, article, [role=main]') || document.body, out = [];
  const skip = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'SVG', 'svg', 'CANVAS', 'IFRAME', 'BUTTON', 'SELECT', 'INPUT', 'TEXTAREA']);
  const one = t => (t || '').replace(/\s+/g, ' ').trim();
  const emit = t => { t = one(t); if (t) out.push(t); };
  const link = a => { const t = one(a.innerText); return t && /^https?:/.test(a.href) ? '[' + t + '](' + a.href + ')' : t; };
  const block = c => {
    const t = c.tagName;
    if (/^H[1-6]$/.test(t)) return emit('#'.repeat(+t[1]) + ' ' + c.innerText);
    if (t === 'PRE') return out.push('```\n' + c.innerText.trimEnd() + '\n```');
    if (t === 'TR') return emit([...c.cells].map(x => one(x.innerText)).join(' | '));
    if (t === 'LI' && !c.querySelector('ul, ol, p, div')) return emit('- ' + walk(c, true));
    if (t === 'IMG') return c.alt && emit('[image: ' + c.alt + ']');
    walk(c);
  };
  // Inline content gathers into one line; a block child ends the line and goes on its own.
  function walk(e, inline) {
    let buf = [];
    for (const c of e.childNodes) {
      if (c.nodeType === 3) { buf.push(c.textContent); continue; }
      if (c.nodeType !== 1 || skip.has(c.tagName)) continue;
      const s = getComputedStyle(c);
      if (s.display === 'none' || s.visibility === 'hidden') continue;
      if (inline || s.display.startsWith('inline')) buf.push(c.tagName === 'A' ? link(c) : walk(c, true));
      else { emit(buf.join('')); buf = []; block(c); }
    }
    if (inline) return buf.join('');
    emit(buf.join(''));
  }
  walk(root);
  return document.title + ' — ' + location.href + (root !== document.body ? ' (main content; hands text has the whole page)' : '') +
         '\n' + out.join('\n');
})()
"""

# Fills a form in one go: keys are element numbers (resolved to keys by the caller), or a field's label, placeholder,
# name or id; values are text, true/false for checkboxes, an option's text for menus.
FILL = r"""
((fields, doc) => {
  if (doc && window.__hkDoc !== doc) throw new Error('the page changed: run ui again');
  const all = [...document.querySelectorAll('input:not([type=hidden]), select, textarea, [contenteditable=true]')];
  const names = e => [e.getAttribute('aria-label'), e.labels && e.labels[0] && e.labels[0].innerText, e.placeholder,
                      e.name, e.id].filter(Boolean).map(t => t.replace(/\s+/g, ' ').trim().toLowerCase());
  const find = k => {
    if (k.startsWith('#hk:')) return document.querySelector('[data-hk="' + k.slice(4) + '"]');
    k = k.toLowerCase();
    return all.find(e => names(e).includes(k)) || all.find(e => names(e).some(t => t.includes(k)));
  };
  const done = [], missing = [];
  for (const [k, v] of Object.entries(fields)) {
    const e = find(k);
    if (!e) { missing.push(k); continue; }
    if (e.type === 'checkbox' || e.type === 'radio') { if (e.checked !== !!v) e.click(); }
    else if (e.tagName === 'SELECT') {
      const t = String(v).toLowerCase();
      const o = [...e.options].find(o => o.text.trim().toLowerCase() === t || o.value.toLowerCase() === t)
             || [...e.options].find(o => o.text.toLowerCase().includes(t));
      if (!o) { missing.push(k + ' (no option "' + v + '")'); continue; }
      e.value = o.value;
    } else if (e.isContentEditable) {
      e.focus(); document.execCommand('selectAll'); document.execCommand('insertText', false, String(v));
    } else {   // the value setter of the element's own class, so frameworks (React…) see the change
      e.focus(); Object.getOwnPropertyDescriptor(Object.getPrototypeOf(e), 'value').set.call(e, String(v));
    }
    e.dispatchEvent(new Event('input', {bubbles: true})); e.dispatchEvent(new Event('change', {bubbles: true}));
    done.push(k);
  }
  return {done, missing};
})
"""


class CDP:
    """A minimal WebSocket client (RFC 6455) to a browser's DevTools endpoint. A reader thread hands each reply to its
    caller and each event to on_event."""

    def __init__(self, ws_url, on_event):
        host, _, path = ws_url[len("ws://"):].partition("/")
        h, _, p = host.partition(":")
        self.s = socket.create_connection((h, int(p)), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall((f"GET /{path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            head += self.s.recv(1)
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise SystemExit("the browser refused the debugging connection")
        self.s.settimeout(None)
        self.n, self.lock, self.waiting, self.alive, self.on_event = 0, threading.Lock(), {}, True, on_event
        threading.Thread(target=self._read, daemon=True).start()

    def _send(self, text):
        data, mask = text.encode(), os.urandom(4)
        n = len(data)
        head = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n) if n < 65536
                                else bytes([0x80 | 127]) + struct.pack(">Q", n))
        masked = (int.from_bytes(data, "big") ^ int.from_bytes((mask * (n // 4 + 1))[:n], "big")).to_bytes(n, "big")
        self.s.sendall(head + mask + masked)

    def _exact(self, n):
        buf = bytearray()
        while len(buf) < n:
            chunk = self.s.recv(min(n - len(buf), 1 << 20))
            if not chunk:
                raise OSError("closed")
            buf += chunk
        return bytes(buf)

    def _recv(self):
        msg = bytearray()
        while True:
            b0, b1 = self._exact(2)
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._exact(8))[0]
            data = self._exact(n)
            if b0 & 0x0F == 8:
                raise OSError("closed")
            if b0 & 0x0F in (9, 10):   # ping, pong: Chromium sends none
                continue
            msg += data
            if b0 & 0x80:
                return msg.decode()

    def _read(self):
        try:
            while True:
                m = json.loads(self._recv())
                if "id" in m:
                    w = self.waiting.pop(m["id"], None)
                    if w:
                        w[1].update(m)
                        w[0].set()
                else:
                    try:
                        self.on_event(m)
                    except Exception:
                        pass
        except (OSError, ValueError):
            pass
        self.alive = False
        for ev, _ in list(self.waiting.values()):
            ev.set()

    def call(self, method, session=None, timeout=15, wait=True, **params):
        """Sends a command and waits for its reply (wait=False: does not, for a click that may open a dialog)."""
        if not self.alive:
            raise SystemExit("the connection to the browser dropped: try again")
        ev, box = threading.Event(), {}
        msg = {"method": method, "params": params}
        if session:
            msg["sessionId"] = session
        with self.lock:
            self.n += 1
            msg["id"] = self.n
            if wait:
                self.waiting[self.n] = (ev, box)
            self._send(json.dumps(msg))
        if not wait:
            return None
        if not ev.wait(timeout):
            self.waiting.pop(msg["id"], None)
            raise SystemExit(f"the page did not answer in {timeout} s (a dialog open? hands ui shows it)")
        if not box:
            raise SystemExit("the connection to the browser dropped: try again")
        if "error" in box:
            raise SystemExit(box["error"].get("message", "browser error"))
        return box.get("result", {})


def get(port, path):
    return json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5))


class Browser:
    """One browser (an agent's Chromium or an Electron app): its connection, its pages' sessions, and what its events
    told: open dialogs, downloads, recent requests, pages loading."""

    def __init__(self, port):
        self.port = port
        self.sessions, self.target_of = {}, {}     # targetId -> sessionId, and back
        self.dialogs, self.loading, self.quiet = {}, {}, {}   # targetId -> dialog / loading / time of last request
        self.downloads, self.requests = {}, {}     # guid -> download; requestId -> request (the last 200)
        self.c = CDP(get(port, "/json/version")["webSocketDebuggerUrl"], self.event)
        self.probed = set()
        try:   # Chromium saves where it always does, and tells us
            self.c.call("Browser.setDownloadBehavior", behavior="default", eventsEnabled=True)
        except SystemExit:
            pass   # some Electron versions do not take it
        # Every page, now and as it opens, gets a session at once: a dialog it opens later is told (one that opened
        # before nobody can tell, see stuck).
        self.c.call("Target.setAutoAttach", autoAttach=True, waitForDebuggerOnStart=False, flatten=True)

    def event(self, m):
        method, p = m["method"], m.get("params", {})
        t = self.target_of.get(m.get("sessionId"))
        if method == "Page.javascriptDialogOpening":
            self.dialogs[t] = p
        elif method == "Page.javascriptDialogClosed":
            self.dialogs.pop(t, None)
        elif method == "Page.frameStartedLoading" and p.get("frameId") == t:
            self.loading[t] = True
        elif method in ("Page.frameStoppedLoading", "Page.loadEventFired") and p.get("frameId", t) == t:
            self.loading[t] = False
        elif method == "Target.attachedToTarget" and p["targetInfo"]["type"] == "page":
            self.enable(p["targetInfo"]["targetId"], p["sessionId"])
        elif method == "Target.detachedFromTarget":
            self.target_of.pop(p.get("sessionId"), None)
            self.sessions.pop(p.get("targetId"), None)
        elif method == "Browser.downloadWillBegin":
            self.downloads[p["guid"]] = {"url": p["url"], "name": p["suggestedFilename"], "state": "inProgress", "path": ""}
        elif method == "Browser.downloadProgress" and p["guid"] in self.downloads:
            d = self.downloads[p["guid"]]
            d.update(state=p["state"], got=p.get("receivedBytes", 0), path=p.get("filePath") or d["path"])
        elif method.startswith("Network."):
            self.quiet[t] = time.time()
            r = self.requests.get(p.get("requestId"))
            if method == "Network.requestWillBeSent":
                if len(self.requests) >= 200:
                    self.requests.pop(next(iter(self.requests)))
                self.requests[p["requestId"]] = {"t": t, "method": p["request"]["method"], "url": p["request"]["url"],
                                                 "type": p.get("type", ""), "status": "…"}
            elif r and method == "Network.responseReceived":
                r["status"] = p["response"]["status"]
            elif r and method == "Network.loadingFailed":
                r["status"] = "failed" if not p.get("canceled") else "canceled"

    def pages(self):
        """The tabs (page targets), the one in front first (Chromium lists the most recently active first)."""
        return [p for p in get(self.port, "/json/list")
                if p.get("type") == "page" and not p.get("url", "").startswith(("chrome-extension://", "devtools://"))]

    def tabs(self):
        """The tabs in a steady order (the browser's target order), for numbering."""
        return [t for t in self.c.call("Target.getTargets")["targetInfos"]
                if t["type"] == "page" and not t["url"].startswith(("chrome-extension://", "devtools://"))]

    def enable(self, tid, s):
        if tid not in self.sessions:
            self.sessions[tid], self.target_of[s] = s, tid
            # Dialogs and loading. Page.enable answers only once an open dialog closes, and does not tell about it.
            self.c.call("Page.enable", session=s, wait=False)
            self.c.call("Network.enable", session=s, wait=False)

    def session(self, tid):
        if tid not in self.sessions:
            self.enable(tid, self.c.call("Target.attachToTarget", targetId=tid, flatten=True)["sessionId"])
        if tid not in self.probed:
            self.probed.add(tid)
            self.stuck(tid)
        return self.sessions[tid]

    def stuck(self, tid):
        """Whether the page is stuck on a dialog that opened before we connected (no event told about it): it does not
        run JavaScript. Noted as an open dialog, with no message."""
        try:
            self.c.call("Runtime.evaluate", session=self.sessions[tid], timeout=1, expression="1")
            if self.dialogs.get(tid, {}).get("stuck"):
                self.dialogs.pop(tid, None)
            return False
        except SystemExit:
            self.dialogs.setdefault(tid, {"type": "dialog", "message": "(it opened before Hands connected)", "stuck": True})
            return True

    def call(self, tid, method, **params):
        return self.c.call(method, session=self.session(tid), **params)

    def dialog(self, tid):
        self.session(tid)
        return self.dialogs.get(tid)

    def js(self, tid, expr, timeout=10):
        d = self.dialog(tid)
        if d and (not d.get("stuck") or self.stuck(tid)):
            raise SystemExit(dialog_text(d))
        r = self.c.call("Runtime.evaluate", session=self.session(tid), timeout=timeout, expression=expr,
                        returnByValue=True, awaitPromise=True, userGesture=True)
        if "exceptionDetails" in r:
            e = r["exceptionDetails"]
            raise SystemExit("error in the page: " + (e.get("exception", {}).get("description") or e.get("text", "")).split("\n")[0])
        return r.get("result", {}).get("value")


def dialog_text(d):
    return (f'a dialog is open: {d.get("type", "alert")} "{d.get("message", "")}": answer it with hands dialog ok|cancel'
            + (" [text]" if d.get("type") == "prompt" else ""))


BROWSERS = {}   # port -> Browser, while its connection lives (for the whole daemon's life)
_lock = threading.Lock()


def browser(port):
    with _lock:
        b = BROWSERS.get(port)
        if not b or not b.c.alive:
            try:
                b = BROWSERS[port] = Browser(port)
            except (OSError, ValueError, KeyError):
                raise SystemExit("the browser is not answering on its debugging port: open it again")
        return b


def profile_port(profile):
    """The DevTools port of an agent's Chromium (its profile), or None if it is not running."""
    try:
        return int(open(os.path.expanduser(f"~/.config/chromium-bots/{profile}/DevToolsActivePort")).readline())
    except (OSError, ValueError):
        return None


def app_port(pid):
    """The DevTools port of the app a window belongs to (Chromium, or an Electron app run with
    --remote-debugging-port), from its command line; None if it has none."""
    try:
        argv = open(f"/proc/{pid}/cmdline").read().split("\0")
    except OSError:
        return None
    port = next((a.split("=", 1)[1] for a in argv if a.startswith("--remote-debugging-port=")), None)
    if port and port != "0":
        return int(port)
    if port == "0":   # Chromium picked a port and wrote it into its profile
        d = next((a.split("=", 1)[1] for a in argv if a.startswith("--user-data-dir=")), os.path.expanduser("~/.config/chromium"))
        try:
            return int(open(os.path.join(d, "DevToolsActivePort")).readline())
        except (OSError, ValueError):
            return None
    return None


def own_page(profile, wait=10.0):
    """The agent's own browser and the tab in front. Right after hands open the browser is still starting, or the tab is
    on about:blank or loading: wait for it (up to `wait` seconds)."""
    end = time.time() + wait
    while True:
        port = profile_port(profile)
        try:
            if port:
                b = browser(port)
                ps = b.pages()
                if ps and (ps[0]["url"] != "about:blank" or time.time() > end):
                    tid = ps[0]["id"]
                    if b.dialog(tid) or b.js(tid, "document.readyState === 'complete'") or time.time() > end:
                        return b, tid
        except (SystemExit, OSError, ValueError):
            if time.time() > end:
                raise
        if time.time() > end:
            raise SystemExit("this agent's browser is not open: open it with hands open URL")
        time.sleep(0.25)


def window_page(b, title):
    """The page shown in a window: the tab whose title is in the window's title, else the one in front."""
    ps = b.pages()
    if not ps:
        raise SystemExit("no page open in this app")
    return next((p["id"] for p in ps if p.get("title") and p["title"] in title), ps[0]["id"])


def snapshot(b, tid):
    """The page's head line, document id and items ({key, sig, line}; headings only {line}); a dialog if one is open."""
    d = b.dialog(tid)
    if d:
        url = next((p["url"] for p in b.pages() if p["id"] == tid), "")
        return {"head": url, "doc": None, "items": [{"line": dialog_text(d)}]}
    return b.js(tid, SNAPSHOT)


def find(b, tid, doc, key):
    """Scrolls element `key` into view: its center in the page, and its tag."""
    r = b.js(tid, f"""(() => {{ if (window.__hkDoc !== {json.dumps(doc)}) return 'doc';
      const e = document.querySelector('[data-hk="{int(key)}"]'); if (!e) return null;
      e.scrollIntoView({{block: 'nearest', inline: 'nearest'}});
      const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2, e.tagName]; }})()""")
    if r == "doc":
        raise SystemExit("the page changed: run ui (or state) again")
    if not r:
        raise SystemExit("that element is gone: run ui (or state) again")
    return r


def screen_origin(b, tid):
    """Where the page's top left corner is on the screen: the window's, past its side border and the toolbars."""
    return b.js(tid, "[screenX + (outerWidth - innerWidth) / 2, screenY + outerHeight - innerHeight - (outerWidth - innerWidth) / 2]")


def click(b, tid, x, y):
    s = b.session(tid)
    for t, held in (("mouseMoved", 0), ("mousePressed", 1), ("mouseReleased", 0)):   # the release may open a dialog:
        b.c.call("Input.dispatchMouseEvent", session=s, wait=t != "mouseReleased", type=t, x=x, y=y,   # do not wait
                 button="left" if t != "mouseMoved" else "none", buttons=held, clickCount=1)
    time.sleep(0.05)


def set_value(b, tid, doc, key, tag, text):
    """Writes into a field (replacing what was there, typed as text input), or picks a menu's option."""
    if tag == "SELECT":
        ok = b.js(tid, f"""(() => {{ const e = document.querySelector('[data-hk="{int(key)}"]'), t = {json.dumps(text)}.toLowerCase();
          const o = [...e.options].find(o => o.text.trim().toLowerCase() === t || o.value.toLowerCase() === t)
                 || [...e.options].find(o => o.text.toLowerCase().includes(t));
          if (!o) return false; e.value = o.value;
          e.dispatchEvent(new Event('input', {{bubbles: true}})); e.dispatchEvent(new Event('change', {{bubbles: true}})); return true; }})()""")
        if not ok:
            raise SystemExit(f'no option "{text}" in that menu')
        return
    b.js(tid, f"""(() => {{ const e = document.querySelector('[data-hk="{int(key)}"]'); e.focus();
      if (e.select) e.select(); else document.execCommand('selectAll'); }})()""")
    b.call(tid, "Input.insertText", text=text)


def upload(b, tid, doc, key, path):
    """Sends a file through upload field `key`, or the page's file field (sites often hide it behind a button)."""
    r = b.call(tid, "Runtime.evaluate", expression=f"""(() => {{ const e = {json.dumps(key)} && window.__hkDoc === {json.dumps(doc)} &&
        document.querySelector('[data-hk="{int(key or 0)}"]');
      if (e && e.type === 'file') return e;
      return (e && e.closest('form, label, div')?.querySelector('input[type=file]')) || document.querySelector('input[type=file]'); }})()""")
    oid = r.get("result", {}).get("objectId")
    if not oid:
        raise SystemExit("no upload field on this page: press the upload button and try again")
    b.call(tid, "DOM.setFileInputFiles", files=[path], objectId=oid)


def fill(b, tid, doc, fields):
    r = b.js(tid, f"({FILL})({json.dumps(fields)}, {json.dumps(doc)})")
    out = f"filled {len(r['done'])}: " + ", ".join(r["done"]) if r["done"] else "filled nothing"
    if r["missing"]:
        raise SystemExit(out + "; not found: " + ", ".join(r["missing"]) + " (hands ui shows the fields)")
    return out


def wait(b, tid, what, text, timeout):
    """Waits until the page has loaded, the network is quiet (and loaded) for half a second, or a text shows."""
    end = time.time() + timeout
    while True:
        try:
            if what == "text":
                ok = b.js(tid, f"(document.body?.innerText || '').replace(/\\u00a0/g, ' ').includes({json.dumps(text)})")
            else:
                ok = not b.loading.get(tid) and b.js(tid, "document.readyState === 'complete'") and (
                    what == "load" or time.time() - b.quiet.get(tid, 0) > 0.5)
        except SystemExit as e:
            if "dialog" in str(e):
                raise
            ok = False   # between two pages
        if ok:
            return "ok"
        if time.time() > end:
            raise SystemExit(f"waited {timeout:g} s and it did not happen" + (f': no "{text}" on the page' if text else ""))
        time.sleep(0.1)


def tab_list(b):
    front = (b.pages() or [{}])[0].get("id")
    return [(t["targetId"], f'"{t["title"]}" {t["url"]}' + (" (front)" if t["targetId"] == front else "")) for t in b.tabs()]


def pick_tab(b, n):
    ts = b.tabs()
    if not n.isdigit() or not 1 <= int(n) <= len(ts):
        raise SystemExit(f"no tab {n}: hands tabs lists them")
    return ts[int(n) - 1]["targetId"]


def downloads(b):
    return [f'{d["state"]}: {d["path"] or d["name"]} ({d["url"][:100]})' for d in b.downloads.values()]


def network(b, tid, n=20):
    rs = [r for r in b.requests.values() if r["t"] == tid][-n:]
    return [f'{r["status"]} {r["method"]} {r["type"].lower()} {r["url"][:120]}' for r in rs]


def pdf(b, tid, path):
    data = b.c.call("Page.printToPDF", session=b.session(tid), timeout=60, printBackground=True)["data"]
    with open(path, "wb") as f:
        f.write(base64.b64decode(data))
    return path


def answer_dialog(b, tid, ok, text=None):
    if not b.dialog(tid):
        raise SystemExit("no dialog open on this page")
    params = {"accept": ok}
    if text is not None:
        params["promptText"] = text
    b.call(tid, "Page.handleJavaScriptDialog", **params)
    b.dialogs.pop(tid, None)
    return "ok"


def evaluate(b, tid, expr):
    v = b.js(tid, expr, timeout=30)
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
