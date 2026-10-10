#!/usr/bin/env python3
"""Hands' commands: the desktop's windows, any app's elements as text, and actions by element number.

GTK apps go through AT-SPI (the Linux accessibility bus); Chromium pages and Electron apps through hands_cdp (their
DevTools protocol). handsd runs these commands in a long-lived process (open connections, events, stable numbers);
when it cannot, `hands` runs this file directly with the same commands: python3 hands_a11y.py <command> [args].
DESK_AGENT names the agent (its browser profile and its own numbering); DESK_SCREENS is where screenshots go.
"""
import collections, glob, json, os, re, subprocess, sys, threading, time

import hands_cdp as C

VERSION = "0.3.0"
HOME = os.path.expanduser("~/.desk")
LIMIT = 150  # elements per state, like hands_cdp
os.environ["DISPLAY"] = ":1"

USAGE = """usage: hands windows                     the windows, numbered
       hands state W [--changes] [--shot]  window W's elements, numbered (W: number or app name); --changes: only
                                         what changed since your last state of W; --shot: a screenshot of W too
       hands press N | set N TEXT | text   act on element N of the last state (or ui); text: the window's text
       hands close|focus|max|min W
       hands ui [--changes]              your browser's page, elements numbered; hands upload N FILE
       hands read [P]                    the page as text (markdown), part P of a long one
       hands tabs | tab new [URL] | tab close [N] | tab N
       hands wait load|idle [S] | wait text "…" [S]   (S: timeout in seconds, 10 by default)
       hands fill '{"Email": "a@b.c", "7": "x", "I agree": true}'   a form in one call (labels or numbers)
       hands dialog ok|cancel [TEXT]     answer the page's alert, confirm or prompt
       hands eval "JS"                   run JavaScript in the page (powerful: the page's data and actions)
       hands downloads | network | pdf FILE
       hands open URL|terminal|files|app NAME   a page in your own browser, a terminal, the file manager, an app
       hands screenshot [file] | click X Y [button] | double X Y | move X Y | type TEXT | key ctrl+l | scroll X Y N
       --as <profile> after the command names the agent (else DESK_AGENT); open --as <profile> <#rrggbb> [url]"""


def run_(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True).stdout


# --- an agent's session: its last window or page, and stable element numbers ---

class Ids:
    """Stable element numbers in one window or page: an element keeps its number while it lives, and a new element
    that looks like one that just went away (same role and name: a re-rendered button) takes its number."""

    def __init__(self):
        self.num, self.key, self.sig, self.next = {}, {}, {}, 0   # key -> n; n -> key; n -> sig
        self.lines, self.refs, self.gen = None, {}, None          # the last view of it, for --changes

    def assign(self, elems):
        """[(key, sig)] in order -> their numbers."""
        live = {k for k, _ in elems}
        free = collections.defaultdict(list)   # sig -> numbers of elements gone
        for n, k in self.key.items():
            if k not in live:
                free[self.sig[n]].append(n)
        out = []
        for k, sig in elems:
            n = self.num.get(k)
            if n is None:
                if free[sig]:
                    n = free[sig].pop(0)
                    self.num.pop(self.key[n], None)
                else:
                    self.next += 1
                    n = self.next
                self.num[k], self.key[n], self.sig[n] = n, k, sig
            out.append(n)
        return out


class Session:
    """What an agent did last: its last window or page (what `press N` acts on) and the numbering of each window or
    page it read. The daemon keeps one per agent; the direct path keeps `last` and `windows` in ~/.desk."""

    def __init__(self, agent, named=True):
        self.agent, self.named = agent, named
        self.last, self.windows, self.scopes = None, None, {}
        self.screens = os.path.join(HOME, "screens")
        self.cwd = os.getcwd()

    def ids(self, scope):
        if scope not in self.scopes:
            if len(self.scopes) >= 40:   # ponytail: forgets the oldest window's numbers; fine for an agent's few windows
                self.scopes.pop(next(iter(self.scopes)))
            self.scopes[scope] = Ids()
        return self.scopes[scope]

    def _file(self):
        return os.path.join(HOME, f"{self.agent}-session.json")

    def load(self):
        try:
            d = json.load(open(self._file()))
            self.last, self.windows = d.get("last"), d.get("windows")
        except (OSError, ValueError):
            pass

    def save(self):
        os.makedirs(HOME, exist_ok=True)
        json.dump({"last": self.last, "windows": self.windows}, open(self._file(), "w"))


def numbered(ids, items):
    """Items ({line} or {key, sig, line, ref}) as text lines, numbering the elements; and number -> ref."""
    ns = iter(ids.assign([(i["key"], i["sig"]) for i in items if "key" in i]))
    lines, refs = [], {}
    for i in items:
        if "key" in i:
            n = next(ns)
            lines.append(f"[{n}] {i['line']}")
            refs[str(n)] = i.get("ref", i["key"])
        else:
            lines.append(i["line"])
    return lines, refs


def changed(old, new):
    """What changed between two views: elements by number (+ new, ~ changed, - gone), other lines as they come and go."""
    def by(lines):
        return {(m.group(1) if (m := re.match(r"\[(\d+)\] ", x)) else x): x for x in lines}
    a, b = by(old), by(new)
    return ([("~ " if k in a else "+ ") + x for k, x in b.items() if a.get(k) != x] +
            ["- " + x for k, x in a.items() if k not in b])


def view(s, scope, head, items, changes):
    """The text of a window or page: all of it, or with --changes only what changed since this agent's last view."""
    ids = s.ids(scope)
    lines, ids.refs = numbered(ids, items)
    old, ids.lines = ids.lines, lines
    if changes and old is not None:
        return "\n".join([head, *(changed(old, lines) or ["(no changes)"])])
    return "\n".join([head, *lines])


# --- windows ---

def parse_windows(wmctrl, active):
    """wmctrl -lpGx lines -> windows: id, pid, x, y, w, h, app, title, focused."""
    out = []
    for line in wmctrl.splitlines():
        f = line.split(None, 9)
        if len(f) < 9:
            continue
        wid, _, pid, x, y, w, h, cls = f[:8]
        # cls is "instance.Class" (thunar.Thunar), and either may have dots (com.microsoft.vscode.com.microsoft.VSCode)
        half = len(cls) // 2
        inst = cls[:half] if cls[:half].lower() == cls[half + 1:].lower() else cls.split(".")[0]
        out.append({"id": int(wid, 16), "pid": int(pid), "x": int(x), "y": int(y), "w": int(w), "h": int(h),
                    "app": inst.rsplit(".", 1)[-1], "title": f[9] if len(f) > 9 else "", "focused": int(wid, 16) == active})
    return out


def windows(s=None):
    active = run_("xdotool", "getactivewindow").strip()
    ws = parse_windows(run_("wmctrl", "-lpGx"), int(active) if active.isdigit() else 0)
    if s:
        s.windows = ws
    return ws


def find_window(arg, ws=None, s=None):
    """A number from the last `hands windows`, or a word of an app or title."""
    if arg.isdigit():
        ws = ws or (s and s.windows) or windows(s)
        if 1 <= int(arg) <= len(ws):
            return ws[int(arg) - 1]
    else:
        arg = {"files": "thunar", "browser": "chromium"}.get(arg.lower(), arg)  # the names `hands open` uses
        for w in ws or windows():
            if arg.lower() in w["app"].lower() or arg.lower() in w["title"].lower():
                return w
    raise SystemExit(f"no window {arg!r}: run hands windows")


def window_by_id(wid):
    w = next((w for w in windows() if w["id"] == wid), None)
    if not w:
        raise SystemExit("that window is closed: run hands windows")
    return w


def show(w, n):
    return f'[{n}] {w["app"]} "{w["title"]}" {w["w"]}x{w["h"]}' + (" (focused)" if w["focused"] else "")


WAIT = 0.4  # seconds: a viewer's cursor glides to the pointer; the action comes once it is there


def point(s, x, y):  # the real pointer goes exactly where the agent acts, and the action waits for viewers to see it
    run_("xdotool", "mousemove", str(round(x)), str(round(y)))
    if s.named:
        time.sleep(WAIT)


def title_button(w, what, top):
    """Where a window's close/max/min button (or its title bar, for focus) is, in screen pixels. Openbox draws the
    title bar (top = its _NET_FRAME_EXTENTS top; wmctrl reports y one title bar too low there): buttons from the
    right edge, close, maximize, iconify, 21 px apart. Chromium draws its own (no frame extents): 52 px apart."""
    right, step, y, first = w["x"] + w["w"], 21, w["y"] - 1.5 * top, 16
    if not top:
        step, y, first = 32, w["y"] + 20, 21
    if what == "focus":
        return w["x"] + w["w"] / 2, y
    return right - first - step * {"close": 0, "max": 1, "min": 2}[what], y


def screenshot(s, w):
    """A screenshot of just this window (brought to the front: X has no pixels for its covered parts)."""
    os.makedirs(s.screens, exist_ok=True)
    shot = os.path.join(s.screens, f"{s.agent}-{time.strftime('%Y%m%d-%H%M%S')}-{w['app']}.png")
    run_("wmctrl", "-ia", hex(w["id"]))
    time.sleep(0.3)
    run_("scrot", "-o", "-w", str(w["id"]), shot)
    for old in sorted(glob.glob(os.path.join(s.screens, "*.png")), key=os.path.getmtime)[:-20]:   # the last 20
        os.remove(old)
    return shot


# --- the accessibility tree (AT-SPI) ---

ATSPI = threading.RLock()   # AT-SPI is used by one thread at a time (the daemon serves agents in parallel)
GEN = None                  # app bus name -> how many AT-SPI events it sent (the daemon counts; None: nobody counts)


def watch():
    """Counts each app's AT-SPI events, so `state --changes` can answer without reading an app that did not change.
    The daemon calls it; events come in when pump() runs (here every 0.2 s, and before each answer)."""
    global GEN
    import pyatspi
    counts = collections.Counter()

    def on(e):
        try:
            counts[e.source.app.bus_name] += 1
        except Exception:
            pass
    with ATSPI:
        pyatspi.Registry.registerEventListener(on, "object:children-changed", "object:state-changed", "window",
                                               "object:property-change:accessible-name", "object:text-changed")
    GEN = counts

    def loop():
        while True:
            pump()
            time.sleep(0.2)
    threading.Thread(target=loop, daemon=True).start()


def pump():
    from gi.repository import GLib
    with ATSPI:
        ctx = GLib.MainContext.default()
        while ctx.iteration(False):
            pass


def clean(t):
    return re.sub(r"\s+", " ", t or "").strip()[:80]


def number(root, facts):
    """The elements one can act on (or type into), in tree order: {key, sig, line, ref: [path, key]}, where path is
    the child indexes from the window. Headings and status bars come as {line: "# text"}. Repeats in a row (GTK tree
    cells) once."""
    items, last, count = [], None, 0

    def walk(o, path, depth):
        nonlocal last, count
        f = facts(o)
        if not f["showing"] or depth > 40:
            return
        name = clean(f["name"])
        if f["role"] in ("heading", "status bar") and name:
            items.append({"line": "# " + name})
        elif (f["actions"] and name) or f["editable"] or f["role"] == "terminal":
            sig = f'{f["role"]} "{name}"'
            line = sig + (f' = "{clean(f["value"])}"' if f["value"] else "")
            if line != last and count < LIMIT:
                key = f.get("key") or "/".join(map(str, path))
                items.append({"key": key, "sig": sig, "line": line, "ref": [path, key]})
                count += 1
            last = line
        for i, c in enumerate(f["children"]):
            walk(c, path + [i], depth + 1)

    walk(root, [], 0)
    if count == LIMIT:
        items.append({"line": f"(only the first {LIMIT} elements)"})
    return items


def facts(o):
    import pyatspi
    try:
        ifs = pyatspi.listInterfaces(o)
        acts = o.queryAction() if "Action" in ifs else None
        return {"role": o.getRoleName(), "name": o.name, "showing": o.getState().contains(pyatspi.STATE_SHOWING),
                "actions": [acts.getName(i) for i in range(acts.nActions)] if acts else [],
                "editable": "EditableText" in ifs, "children": list(o), "key": o.path,
                "value": o.queryText().getText(0, -1) if "EditableText" in ifs else ""}
    except Exception:  # an element that went away while we read
        return {"role": "", "name": "", "showing": False, "actions": [], "editable": False, "children": [], "value": ""}


def frame_of(w):
    """The window's AT-SPI frame."""
    import pyatspi
    for app in pyatspi.Registry.getDesktop(0):
        if app is None or app.get_process_id() != w["pid"]:
            continue
        frames = [f for f in app if f is not None]
        return next((f for f in frames if f.name == w["title"]), None) or frames[0] if frames else None
    raise SystemExit(f'{w["app"]} does not expose its window to accessibility: use hands screenshot and hands click')


def element(s, n):
    st = s.last or {}
    ref = (st.get("refs") or {}).get(str(n))
    if not ref:
        raise SystemExit(f"no element {n} in your last state: run hands state (or ui) again")
    if st["kind"] == "cdp":
        return st, ref
    path, key = ref
    with ATSPI:
        o = frame_of(window_by_id(st["window"]))
        try:
            for i in path:
                o = o[i]
        except Exception:
            o = None
        if o is None or facts(o).get("key", key) != key:
            raise SystemExit(f"element {n} moved or is gone: the window changed, run hands state again")
    return st, o


def center(o):
    import pyatspi
    e = o.queryComponent().getExtents(pyatspi.DESKTOP_COORDS)
    return e.x + e.width / 2, e.y + e.height / 2


def click(st, o):  # no accessible action: bring the window up and click the element's center (the pointer is there)
    run_("wmctrl", "-ia", hex(st["window"]))
    time.sleep(0.2)
    run_("xdotool", "click", "1")


# --- pages (Chromium and Electron, through hands_cdp) ---

def page(s, front=True):
    """The browser and page the agent works on: its last state's if that was a page (with front, the tab in front of
    its own browser, which may have opened since), else its own browser's tab in front."""
    st = s.last or {}
    if st.get("kind") == "cdp":
        b = C.browser(st["port"])
        ids = [p["id"] for p in b.pages()]
        if st.get("own") and front and ids:
            return b, ids[0]
        if st["tid"] in ids:
            return b, st["tid"]
        if not st.get("own"):
            raise SystemExit("that page is closed: run hands state again")
    return C.own_page(s.agent)


def page_view(s, b, tid, head, changes, own, window=None):
    snap = C.snapshot(b, tid)
    out = view(s, ("cdp", b.port, tid, snap["doc"]), (head + "\n" if head else "") + snap["head"], snap["items"], changes)
    s.last = {"kind": "cdp", "port": b.port, "tid": tid, "doc": snap["doc"], "own": own, "window": window,
              "refs": s.ids(("cdp", b.port, tid, snap["doc"])).refs}
    return out


def page_element(s, n):
    st, key = element(s, n)
    b = C.browser(st["port"])
    x, y, tag = C.find(b, st["tid"], st["doc"], key)
    if s.named:   # viewers draw the agent's cursor where the pointer is: move it to the element first
        sx, sy = C.screen_origin(b, st["tid"])
        point(s, sx + x, sy + y)
    return b, st, key, x, y, tag


# --- commands ---

def state(s, arg, changes=False, shot=False):
    w = find_window(arg, s=s)
    head = f'{w["app"]} "{w["title"]}" {w["w"]}x{w["h"]} at {w["x"]},{w["y"]}'
    if shot:
        head += f"; screenshot of this window: {screenshot(s, w)}"
    port = C.app_port(w["pid"])
    if port:
        b = C.browser(port)
        return page_view(s, b, C.window_page(b, w["title"]), head, changes, own=False, window=w["id"])
    with ATSPI:
        fr = frame_of(w)
        if fr is None:
            raise SystemExit(f'{w["app"]} has no accessible window: use hands state {arg} --shot, hands click and hands type')
        bus = fr.app.bus_name
        scope = ("atspi", w["id"])
        ids = s.ids(scope)
        if changes and ids.lines is not None and GEN is not None:
            try:   # a call to the app: the events it sent before answering come in first
                fr.queryComponent().getExtents(0)
            except Exception:
                pass
            pump()
            if GEN[bus] == ids.gen:
                s.last = {"kind": "atspi", "window": w["id"], "refs": ids.refs}
                return head + "\n(no changes)"
        items = number(fr, facts)
        if GEN is not None:   # counted after reading: some apps (Thunar) send events when they are read
            pump()            # ponytail: a change whose events come in during the read counts as read
            ids.gen = GEN[bus]
    out = view(s, scope, head, items, changes)
    s.last = {"kind": "atspi", "window": w["id"], "refs": ids.refs}
    if not ids.refs and not shot:   # a canvas, or an app that shows nothing to accessibility: pixels are all there is
        out += f"\n(no elements: screenshot of this window: {screenshot(s, w)}; use hands click and hands type)"
    return out


def press(s, n):
    if (s.last or {}).get("kind") == "cdp":
        b, st, _, x, y, _ = page_element(s, n)
        C.click(b, st["tid"], x, y)
        d = b.dialogs.get(st["tid"])
        return "ok" + (f"; {C.dialog_text(d)}" if d else "")
    st, o = element(s, n)
    with ATSPI:
        c = center(o)
    point(s, *c)   # other agents may use AT-SPI while this one's pointer travels
    with ATSPI:
        f = facts(o)
        # A row's accessible actions edit or expand it: a click selects it, as for a person (then Return opens it).
        act = o.queryAction() if f["actions"] and f["role"] not in ("table cell", "tree item", "list item") else None
        for want in ("click", "press", "activate", "toggle", "jump", None):
            if act and (want in f["actions"] or want is None):
                act.doAction(f["actions"].index(want) if want else 0)
                break
        else:
            click(st, o)
    return "ok"


def set_text(s, n, text):
    if (s.last or {}).get("kind") == "cdp":
        b, st, key, x, y, tag = page_element(s, n)
        if tag != "SELECT":
            C.click(b, st["tid"], x, y)
        C.set_value(b, st["tid"], st["doc"], key, tag, text)
        return "ok"
    st, o = element(s, n)
    with ATSPI:
        c = center(o)
    point(s, *c)
    with ATSPI:
        if facts(o)["editable"]:
            o.queryEditableText().setTextContents(text)
            return "ok"
    click(st, o)   # a terminal, or anything else that takes keys
    run_("xdotool", "type", "--delay", "12", "--", text)
    return "ok"


def text(s):
    st = s.last or {}
    if st.get("kind") != "atspi":
        b, tid = page(s)
        t = b.js(tid, C.TEXT)
        return t[:20000] + ("\n(… cut: scroll the page or use hands read 2)" if len(t) > 20000 else "")
    import pyatspi
    out = []

    def walk(o, depth):
        if o is None or depth > 40:
            return
        ifs = pyatspi.listInterfaces(o)
        if "Text" in ifs:
            out.append(o.queryText().getText(0, -1))
        elif o.name:
            out.append(o.name)
        for c in o:
            walk(c, depth + 1)

    with ATSPI:
        walk(frame_of(window_by_id(st["window"])), 0)
    t = re.sub(r"\n\s*\n+", "\n", "\n".join(x for x in out if x and x.strip())).strip()
    return t[-20000:]  # a terminal's latest output is at the end


def read(s, part):
    b, tid = page(s)
    t, size = b.js(tid, C.READ), 20000
    head, _, body = t.partition("\n")
    chunk = body[(part - 1) * size: part * size]
    more = len(body) > part * size
    return head + (f" (part {part})" if part > 1 else "") + "\n" + chunk + (f"\n(… more: hands read {part + 1})" if more else "")


def window_action(s, what, arg):
    w = find_window(arg, s=s)
    ext = run_("xprop", "-id", str(w["id"]), "_NET_FRAME_EXTENTS")   # "_NET_FRAME_EXTENTS(CARDINAL) = 0, 0, 26, 0"
    top = int(ext.split("=")[1].split(",")[2]) if "=" in ext else 0
    if what != "focus" and not w["focused"]:   # as a person would: bring it up, so its button can be seen
        run_("wmctrl", "-ia", hex(w["id"]))
        time.sleep(0.2)
    point(s, *title_button(w, what, top))
    if what == "close":
        run_("wmctrl", "-ic", hex(w["id"]))
    elif what == "focus":
        run_("wmctrl", "-ia", hex(w["id"]))
    elif what == "max":
        run_("wmctrl", "-ir", hex(w["id"]), "-b", "add,maximized_vert,maximized_horz")
    else:
        run_("xdotool", "windowminimize", str(w["id"]))
    return "ok"


def tab(s, rest):
    b, tid = page(s)
    op = rest[0] if rest else ""
    if op == "new":
        tid = b.c.call("Target.createTarget", url=rest[1] if len(rest) > 1 else "about:blank")["targetId"]
    elif op == "close":
        b.c.call("Target.closeTarget", targetId=C.pick_tab(b, rest[1]) if len(rest) > 1 else tid)
        return "ok"
    elif op == "switch" and len(rest) == 2 or op.isdigit() and len(rest) == 1:
        tid = C.pick_tab(b, rest[-1])
        b.c.call("Target.activateTarget", targetId=tid)
    else:
        raise SystemExit(USAGE)
    st = s.last or {}
    s.last = {"kind": "cdp", "port": b.port, "tid": tid, "doc": None, "own": st.get("own", True), "refs": {}}
    return "ok"


def timeout(rest, i):
    try:
        return float(rest[i]) if len(rest) > i else 10.0
    except ValueError:
        raise SystemExit("the timeout is in seconds: hands wait load 20")


def run(s, a):
    """Runs one command for an agent's session: its output, or SystemExit with what went wrong."""
    cmd, rest = (a[0], a[1:]) if a else ("", [])
    flags = set()
    if cmd in ("state", "ui"):
        flags = {x for x in rest if x.startswith("--")}
        rest = [x for x in rest if not x.startswith("--")]
        if flags - {"--changes", "--shot"}:
            raise SystemExit(USAGE)
    if cmd == "version":
        return f"hands {VERSION}"
    if cmd == "windows" and not rest:
        return "\n".join(show(w, i + 1) for i, w in enumerate(windows(s))) or "(no windows)"
    if cmd == "state" and len(rest) == 1:
        return state(s, rest[0], "--changes" in flags, "--shot" in flags)
    if cmd == "ui" and not rest:
        b, tid = C.own_page(s.agent)
        return page_view(s, b, tid, "", "--changes" in flags, own=True)
    if cmd == "press" and len(rest) == 1:
        return press(s, rest[0])
    if cmd == "set" and len(rest) >= 2:
        return set_text(s, rest[0], " ".join(rest[1:]))
    if cmd == "text" and not rest:
        return text(s)
    if cmd == "upload" and len(rest) == 2:
        st = s.last if (s.last or {}).get("kind") == "cdp" else {}
        b, tid = page(s, front=False)
        path = os.path.join(s.cwd, os.path.expanduser(rest[1]))
        if not os.path.isfile(path):
            raise SystemExit(f"file not found: {path}")
        key = (st.get("refs") or {}).get(rest[0]) if st.get("tid") == tid else None
        C.upload(b, tid, st.get("doc"), key, path)
        return "ok"
    if cmd in ("close", "focus", "max", "min") and len(rest) == 1:
        return window_action(s, cmd, rest[0])
    if cmd == "read" and len(rest) <= 1:
        return read(s, int(rest[0]) if rest and rest[0].isdigit() else 1)
    if cmd == "tabs" and not rest:
        b, _ = page(s)
        return "\n".join(f"[{i + 1}] {t}" for i, (_, t) in enumerate(C.tab_list(b))) or "(no tabs)"
    if cmd == "tab":
        return tab(s, rest)
    if cmd == "wait" and rest and rest[0] in ("load", "idle") and len(rest) <= 2:
        b, tid = page(s)
        return C.wait(b, tid, rest[0], None, timeout(rest, 1))
    if cmd == "wait" and rest and rest[0] == "text" and 2 <= len(rest) <= 3:
        b, tid = page(s)
        return C.wait(b, tid, "text", rest[1], timeout(rest, 2))
    if cmd == "fill" and len(rest) == 1:
        try:
            fields = json.loads(rest[0])
        except ValueError:
            raise SystemExit("""fill takes a JSON object: hands fill '{"Email": "a@b.c", "7": "x"}'""")
        st = s.last if (s.last or {}).get("kind") == "cdp" else {}
        b, tid = page(s, front=False)
        refs = (st.get("refs") or {}) if st.get("tid") == tid else {}
        if any(k.isdigit() and k not in refs for k in fields):
            raise SystemExit("numbers are from your last ui or state of this page: run hands ui first")
        fields = {("#hk:" + refs[k] if k.isdigit() else k): v for k, v in fields.items()}
        out = C.fill(b, tid, st.get("doc") if any(k.startswith("#hk:") for k in fields) else None, fields)
        return re.sub(r"#hk:(\d+)", lambda m: next(n for n, r in refs.items() if r == m.group(1)), out)
    if cmd == "eval" and rest:
        b, tid = page(s)
        return C.evaluate(b, tid, " ".join(rest))
    if cmd == "dialog" and rest and rest[0] in ("ok", "cancel"):
        b, tid = page(s)
        if (b.dialog(tid) or {}).get("stuck"):   # the protocol cannot answer it: the keyboard can, in its window
            w = next((w for w in windows() if C.app_port(w["pid"]) == b.port), None)
            if not w:
                raise SystemExit("no window of this browser on the screen")
            run_("wmctrl", "-ia", hex(w["id"]))
            time.sleep(0.2)
            if len(rest) > 1:
                run_("xdotool", "type", "--delay", "12", "--", " ".join(rest[1:]))
            run_("xdotool", "key", "Return" if rest[0] == "ok" else "Escape")
            b.dialogs.pop(tid, None)
            return "ok"
        return C.answer_dialog(b, tid, rest[0] == "ok", " ".join(rest[1:]) if len(rest) > 1 else None)
    if cmd == "downloads" and not rest:
        b, _ = page(s)
        return "\n".join(C.downloads(b)) or "(no downloads since the browser connected)"
    if cmd == "network" and not rest:
        b, tid = page(s)
        return "\n".join(C.network(b, tid)) or "(no requests seen on this tab yet)"
    if cmd == "pdf" and len(rest) == 1:
        b, tid = page(s)
        return C.pdf(b, tid, os.path.join(s.cwd, os.path.expanduser(rest[0])))
    raise SystemExit(USAGE)


def main():
    agent = os.environ.get("DESK_AGENT")
    s = Session(agent or "desk", named=bool(agent))
    s.screens = os.environ.get("DESK_SCREENS") or s.screens
    s.load()
    try:
        print(run(s, sys.argv[1:]))
    finally:
        s.save()


if __name__ == "__main__":
    main()
