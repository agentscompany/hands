#!/usr/bin/env python3
"""pc-ui ui | press N | set N "text" | text | upload N file — an agent's Chromium page as text, and actions by number.

Talks to the Chromium of the agent's profile (~/.config/chromium-bots/<profile>, opened by `pc open --as`) through
its debugging protocol (CDP), on the port it writes to <profile>/DevToolsActivePort (Chromium must run with
--remote-debugging-port=0). Standard library only.

  pc-ui ui --as <profile>                the visible interactive elements, numbered: [12] button "Buy"
  pc-ui press --as <profile> N           click element N (real mouse events, after scrolling to it)
  pc-ui set --as <profile> N "text"      write into field N (replacing what was there); in a <select>, pick the option
  pc-ui text --as <profile>              the page's text (report figures, a call's captions, articles)
  pc-ui upload --as <profile> N file     send a file through upload field N (or the page's, if N is not one)
"""
import base64, json, os, socket, struct, subprocess, sys, time, urllib.request

LIMIT = 120  # elements per read: the rest comes after scrolling

SNAPSHOT = r"""
(() => {
  for (const e of document.querySelectorAll('[data-pc-id]')) e.removeAttribute('data-pc-id');
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
              '[role=menuitem], [role=checkbox], [role=option], [onclick], [contenteditable=true], summary';
  let n = 0, more = 0;
  for (const e of document.querySelectorAll(sel + ', h1, h2, h3')) {
    if (!visible(e) || e.disabled) continue;
    if (/^H[123]$/.test(e.tagName)) { const t = clean(e.innerText); if (t) out.push('# ' + t); continue; }
    if (n >= __LIMIT__) { more++; continue; }
    e.setAttribute('data-pc-id', ++n);
    let line = '[' + n + '] ' + kind(e) + ' "' + label(e) + '"';
    if (e.tagName === 'SELECT') line += ' = "' + clean(e.options[e.selectedIndex]?.text) + '"';
    else if ((e.tagName === 'INPUT' || e.tagName === 'TEXTAREA') && !['checkbox', 'radio', 'submit', 'button'].includes(e.type) && e.value)
      line += ' = "' + (e.type === 'password' ? '••••' : clean(e.value)) + '"';
    out.push(line);
  }
  const below = document.documentElement.scrollHeight - scrollY - H;
  return [document.title + ' — ' + location.href, ...out,
          more ? '(+' + more + ' more visible elements past the limit)' : '',
          below > 40 ? '(the page goes on below: scroll to see more, then ui again)' : ''].filter(Boolean).join('\n');
})()
""".replace("__LIMIT__", str(LIMIT))


# The text a person reads on the page, without runs of blank lines; capped to fit in a turn.
TEXT = r"""
(() => {
  const t = (document.body?.innerText || '').replace(/[ \t]+/g, ' ').replace(/\n\s*\n+/g, '\n').trim();
  const max = 20000;
  return document.title + ' — ' + location.href + '\n' + (t.length > max ? t.slice(0, max) + '\n(… cut: scroll the page or ask for a part)' : t);
})()
"""


class CDP:
    """A minimal WebSocket client (RFC 6455) for a Chromium tab."""

    def __init__(self, ws_url):
        host, _, path = ws_url[len("ws://"):].partition("/")
        h, _, p = host.partition(":")
        self.s = socket.create_connection((h, int(p)), timeout=15)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall((f"GET /{path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            head += self.s.recv(1)
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise SystemExit("Chromium refused the debugging connection")
        self.n = 0

    def _send(self, text):
        data = text.encode()
        mask = os.urandom(4)
        n = len(data)
        head = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n) if n < 65536
                                else bytes([0x80 | 127]) + struct.pack(">Q", n))
        self.s.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.s.recv(n - len(buf))
            if not chunk:
                raise SystemExit("the connection to Chromium dropped")
            buf += chunk
        return buf

    def _recv(self):
        msg = b""
        while True:
            b0, b1 = self._exact(2)
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._exact(8))[0]
            msg += self._exact(n)
            if b0 & 0x80:
                return msg.decode()

    def call(self, method, **params):
        self.n += 1
        self._send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            m = json.loads(self._recv())
            if m.get("id") == self.n:
                if "error" in m:
                    raise SystemExit(m["error"].get("message", "Chromium error"))
                return m.get("result", {})

    def js(self, expr):
        r = self.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in r:
            raise SystemExit("error in the page: " + r["exceptionDetails"].get("text", ""))
        return r.get("result", {}).get("value")


def tab(profile):
    d = os.path.expanduser(f"~/.config/chromium-bots/{profile}")
    try:
        port = open(os.path.join(d, "DevToolsActivePort")).readline().strip()
        pages = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5))
    except (OSError, ValueError):
        raise SystemExit("this agent's browser is not open (or opened before it supported this): open it with pc open --as")
    pages = [p for p in pages if p.get("type") == "page" and not p.get("url", "").startswith("chrome-extension://")]
    if not pages:
        raise SystemExit("no tab open: open one with pc open --as")
    return CDP(pages[0]["webSocketDebuggerUrl"])  # the active tab comes first


def center(c, n):
    r = c.js(f"""(() => {{ const e = document.querySelector('[data-pc-id="{int(n)}"]');
      if (!e) return null; e.scrollIntoView({{block: 'nearest', inline: 'nearest'}});
      const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2, e.tagName]; }})()""")
    if not r:
        raise SystemExit(f"no element {n}: did the page change? run ui again")
    if os.environ.get("DESK_AGENT"):  # viewers draw the agent's cursor where the pointer is: move it to the element
        sx, sy = c.js("[screenX, screenY + outerHeight - innerHeight]")
        subprocess.run(["xdotool", "mousemove", str(int(sx + r[0])), str(int(sy + r[1]))], env={**os.environ, "DISPLAY": ":1"})
    return r


def press(c, n):
    x, y, tag = center(c, n)
    for t in ("mouseMoved", "mousePressed", "mouseReleased"):
        c.call("Input.dispatchMouseEvent", type=t, x=x, y=y, button="left", clickCount=1)
    return tag


def main():
    a = sys.argv[1:]
    if len(a) < 3 or a[1] != "--as":
        raise SystemExit(__doc__)
    action, profile, rest = a[0], a[2], a[3:]
    if action in ("ui", "text"):
        # Right after pc open the browser is still starting, or the tab is on about:blank or loading: wait up to 10 s.
        for _ in range(20):
            try:
                c = tab(profile)
                if c.js("location.href !== 'about:blank' && document.readyState === 'complete'"):
                    break
            except SystemExit:
                pass
            time.sleep(0.5)
        print(tab(profile).js(SNAPSHOT if action == "ui" else TEXT))
        return
    c = tab(profile)
    if action == "press" and rest:
        press(c, rest[0])
        print("ok")
    elif action == "set" and len(rest) >= 2:
        n, text = rest[0], " ".join(rest[1:])
        x, y, tag = center(c, n)
        if tag == "SELECT":
            ok = c.js(f"""(() => {{ const e = document.querySelector('[data-pc-id="{int(n)}"]'), t = {json.dumps(text)}.toLowerCase();
              const o = [...e.options].find(o => o.text.trim().toLowerCase() === t || o.value.toLowerCase() === t)
                     || [...e.options].find(o => o.text.toLowerCase().includes(t));
              if (!o) return false; e.value = o.value;
              e.dispatchEvent(new Event('input', {{bubbles: true}})); e.dispatchEvent(new Event('change', {{bubbles: true}})); return true; }})()""")
            if not ok:
                raise SystemExit(f'no option "{text}" in menu {n}')
        else:
            press(c, n)
            c.js(f"""(() => {{ const e = document.querySelector('[data-pc-id="{int(n)}"]');
              if (e.select) e.select(); else document.execCommand('selectAll'); }})()""")
            c.call("Input.insertText", text=text)
        print("ok")
    elif action == "upload" and len(rest) >= 2:
        n, path = rest[0], os.path.abspath(rest[1])
        if not os.path.isfile(path):
            raise SystemExit(f"file not found: {path}")
        # Field N if it is a file field; else the page's file field (sites often hide it behind a button).
        r = c.call("Runtime.evaluate", expression=f"""(() => {{ const e = document.querySelector('[data-pc-id="{int(n)}"]');
          if (e && e.type === 'file') return e;
          return (e && e.closest('form, label, div')?.querySelector('input[type=file]')) || document.querySelector('input[type=file]'); }})()""")
        oid = r.get("result", {}).get("objectId")
        if not oid:
            raise SystemExit("no upload field on this page: press the upload button and try again")
        c.call("DOM.setFileInputFiles", files=[path], objectId=oid)
        print("ok")
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
