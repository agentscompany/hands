#!/usr/bin/env python3
"""hands windows | state W | ui | press N | set N TEXT | text | upload N FILE | close|focus|max|min W

Any window of the desktop as text, for agents (`hands` runs this):
  hands windows            the windows, numbered: [2] thunar "ac - Thunar" 900x560 (focused)
  hands state W            one window's elements, numbered, and a screenshot of just that window (brought to the
                          front). W: a number from `hands windows`, or a word of its app or title (chromium, terminal…)
  hands press N            press element N of the last `state` (or `ui`); hands set N TEXT writes into it
  hands text               the text of the last window (a terminal's output, a page's text)
  hands close|focus|max|min W
Chromium pages go through pc-ui (its debugging protocol); every other app through AT-SPI, the Linux accessibility
bus. Actions use accessibility when the app offers one (the window need not be in front) and otherwise focus the
window and click the element's center; either way the pointer moves there, so a viewer sees where the agent acted.
DESK_AGENT names the agent (its browser profile and its own numbering); DESK_SCREENS is where screenshots go.
"""
import glob, json, os, re, subprocess, sys, time

HOME = os.path.expanduser("~/.desk")
AGENT = os.environ.get("DESK_AGENT") or "desk"
SCREENS = os.environ.get("DESK_SCREENS") or os.path.join(HOME, "screens")  # the last 20 are kept
LIMIT = 150  # elements per state, like pc-ui
os.environ["DISPLAY"] = ":1"


def run(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True).stdout


def save(name, v):  # per agent: agents share the home
    os.makedirs(HOME, exist_ok=True)
    json.dump(v, open(os.path.join(HOME, f"{AGENT}-{name}"), "w"))


def load(name):
    try:
        return json.load(open(os.path.join(HOME, f"{AGENT}-{name}")))
    except (OSError, ValueError):
        return None


# --- windows ---

def parse_windows(wmctrl, active):
    """wmctrl -lpGx lines -> windows: id, pid, x, y, w, h, app, title, focused."""
    out = []
    for line in wmctrl.splitlines():
        f = line.split(None, 9)
        if len(f) < 9:
            continue
        wid, _, pid, x, y, w, h, cls = f[:8]
        out.append({"id": int(wid, 16), "pid": int(pid), "x": int(x), "y": int(y), "w": int(w), "h": int(h),
                    "app": cls.split(".")[0], "title": f[9] if len(f) > 9 else "", "focused": int(wid, 16) == active})
    return out


def windows():
    active = run("xdotool", "getactivewindow").strip()
    ws = parse_windows(run("wmctrl", "-lpGx"), int(active) if active.isdigit() else 0)
    save("windows.json", ws)
    return ws


def find_window(arg, ws=None):
    """A number from the last `hands windows`, or a word of an app or title."""
    if arg.isdigit():
        ws = ws or load("windows.json") or windows()
        if 1 <= int(arg) <= len(ws):
            return ws[int(arg) - 1]
    else:
        arg = {"files": "thunar", "browser": "chromium"}.get(arg.lower(), arg)  # the names `hands open` uses
        for w in ws or windows():
            if arg.lower() in w["app"].lower() or arg.lower() in w["title"].lower():
                return w
    raise SystemExit(f"no window {arg!r}: run hands windows")


def show(w, n):
    return f'[{n}] {w["app"]} "{w["title"]}" {w["w"]}x{w["h"]}' + (" (focused)" if w["focused"] else "")


def point(x, y):  # the real pointer goes where the agent acts: viewers draw the agent's cursor there
    run("xdotool", "mousemove", str(int(x)), str(int(y)))


# --- the accessibility tree (AT-SPI) ---

def clean(t):
    return re.sub(r"\s+", " ", t or "").strip()[:80]


def number(root, facts):
    """The elements one can act on (or type into), in tree order: lines "[n] role "name" = "value"" and the path
    (child indexes) of each. Headings and status bars come as "# text". Repeats in a row (GTK tree cells) once."""
    lines, paths, last = [], [], None

    def walk(o, path, depth):
        f = facts(o)
        if not f["showing"] or depth > 40:
            return
        name = clean(f["name"])
        if f["role"] in ("heading", "status bar") and name:
            lines.append("# " + name)
        elif (f["actions"] and name) or f["editable"] or f["role"] == "terminal":
            nonlocal last
            line = f'{f["role"]} "{name}"' + (f' = "{clean(f["value"])}"' if f["value"] else "")
            if line != last and len(paths) < LIMIT:
                paths.append(path)
                lines.append(f"[{len(paths)}] {line}")
            last = line
        for i, c in enumerate(f["children"]):
            walk(c, path + [i], depth + 1)

    walk(root, [], 0)
    if len(paths) == LIMIT:
        lines.append(f"(only the first {LIMIT} elements)")
    return lines, paths


def facts(o):
    import pyatspi
    try:
        ifs = pyatspi.listInterfaces(o)
        acts = o.queryAction() if "Action" in ifs else None
        return {"role": o.getRoleName(), "name": o.name, "showing": o.getState().contains(pyatspi.STATE_SHOWING),
                "actions": [acts.getName(i) for i in range(acts.nActions)] if acts else [],
                "editable": "EditableText" in ifs, "children": list(o),
                "value": o.queryText().getText(0, -1) if "EditableText" in ifs else ""}
    except Exception:  # an element that went away while we read
        return {"role": "", "name": "", "showing": False, "actions": [], "editable": False, "children": [], "value": ""}


def frame_of(w):
    """The window's AT-SPI frame: [app index, frame index] and the frame."""
    import pyatspi
    desktop = pyatspi.Registry.getDesktop(0)
    for i, app in enumerate(desktop):
        if app is None or app.get_process_id() != w["pid"]:
            continue
        frames = list(app)
        for j, fr in enumerate(frames):
            if fr is not None and fr.name == w["title"]:
                return [i, j], fr
        if frames:
            return [i, 0], frames[0]
    raise SystemExit(f'{w["app"]} does not expose its window to accessibility: use hands screenshot and hands click')


def element(n):
    st = load("state.json") or {"kind": "chromium", "profile": AGENT}  # before any state: the agent's browser
    if st["kind"] == "chromium":
        return st, None
    import pyatspi
    if not 1 <= int(n) <= len(st["paths"]):
        raise SystemExit(f"no element {n} in the last state")
    o = pyatspi.Registry.getDesktop(0)
    try:
        for i in st["root"] + st["paths"][int(n) - 1]:
            o = o[i]
    except Exception:
        o = None
    if o is None:
        raise SystemExit(f"element {n} is gone: the window changed, run hands state again")
    return st, o


def center(o):
    import pyatspi
    e = o.queryComponent().getExtents(pyatspi.DESKTOP_COORDS)
    return e.x + e.width / 2, e.y + e.height / 2


def click(st, o):  # no accessible action: bring the window up and click the element's center
    x, y = center(o)
    run("wmctrl", "-ia", hex(st["window"]))
    time.sleep(0.2)
    run("xdotool", "mousemove", str(int(x)), str(int(y)), "click", "1")


# --- commands ---

def pcui(*args, profile=None):
    r = subprocess.run(["pc-ui", args[0], "--as", profile or AGENT, *args[1:]], env={**os.environ, "DESK_AGENT": AGENT})
    sys.exit(r.returncode)


def chromium_profile(w):  # the Chromium profile (pc open --as) a window belongs to
    try:
        cmd = open(f"/proc/{w['pid']}/cmdline").read().split("\0")
    except OSError:
        cmd = []
    for a in cmd:
        m = re.match(r"--user-data-dir=.*/chromium-bots/([^/]+)$", a)
        if m:
            return m.group(1)
    raise SystemExit("this Chromium window is not an agent's browser: open your own with hands open URL")


def state(arg):
    w = find_window(arg)
    os.makedirs(SCREENS, exist_ok=True)
    shot = os.path.join(SCREENS, f"{AGENT}-{time.strftime('%Y%m%d-%H%M%S')}-{w['app']}.png")
    run("wmctrl", "-ia", hex(w["id"]))  # in front: X has no pixels for the covered parts of a window
    time.sleep(0.3)
    run("scrot", "-o", "-w", str(w["id"]), shot)
    for old in sorted(glob.glob(os.path.join(SCREENS, "*.png")), key=os.path.getmtime)[:-20]:
        os.remove(old)
    print(f'{w["app"]} "{w["title"]}" {w["w"]}x{w["h"]} at {w["x"]},{w["y"]}; screenshot of this window: {shot}')
    sys.stdout.flush()
    if w["app"].lower() == "chromium":
        p = chromium_profile(w)
        save("state.json", {"kind": "chromium", "profile": p})
        pcui("ui", profile=p)
    root, fr = frame_of(w)
    lines, paths = number(fr, facts)
    save("state.json", {"kind": "atspi", "window": w["id"], "root": root, "paths": paths})
    print("\n".join(lines) or "(no elements: use the screenshot, hands click and hands type)")


def press(n):
    st, o = element(n)
    if st["kind"] == "chromium":
        pcui("press", n, profile=st["profile"])
    point(*center(o))
    f = facts(o)
    # A row's accessible actions edit or expand it: a click selects it, as for a person (then Return opens it).
    act = o.queryAction() if f["actions"] and f["role"] not in ("table cell", "tree item", "list item") else None
    for want in ("click", "press", "activate", "toggle", "jump", None):
        if act and (want in f["actions"] or want is None):
            act.doAction(f["actions"].index(want) if want else 0)
            break
    else:
        click(st, o)
    print("ok")


def set_text(n, text):
    st, o = element(n)
    if st["kind"] == "chromium":
        pcui("set", n, text, profile=st["profile"])
    point(*center(o))
    if facts(o)["editable"]:
        o.queryEditableText().setTextContents(text)
    else:  # a terminal, or anything else that takes keys
        click(st, o)
        run("xdotool", "type", "--delay", "12", "--", text)
    print("ok")


def text():
    st = load("state.json") or {"kind": "chromium", "profile": AGENT}
    if st["kind"] == "chromium":
        pcui("text", profile=st["profile"])
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

    w = next((w for w in windows() if w["id"] == st["window"]), None)
    if not w:
        raise SystemExit("that window is closed: run hands windows")
    _, fr = frame_of(w)
    walk(fr, 0)
    t = re.sub(r"\n\s*\n+", "\n", "\n".join(x for x in out if x and x.strip())).strip()
    print(t[-20000:])  # a terminal's latest output is at the end


def window_action(what, arg):
    w = find_window(arg)
    point(w["x"] + w["w"] / 2, w["y"] + 12)
    if what == "close":
        run("wmctrl", "-ic", hex(w["id"]))
    elif what == "focus":
        run("wmctrl", "-ia", hex(w["id"]))
    elif what == "max":
        run("wmctrl", "-ir", hex(w["id"]), "-b", "add,maximized_vert,maximized_horz")
    else:
        run("xdotool", "windowminimize", str(w["id"]))
    print("ok")


def main():
    a = sys.argv[1:]
    cmd, rest = (a[0], a[1:]) if a else ("", [])
    if cmd == "windows":
        print("\n".join(show(w, i + 1) for i, w in enumerate(windows())) or "(no windows)")
    elif cmd == "state" and len(rest) == 1:
        state(rest[0])
    elif cmd == "ui" and not rest:
        save("state.json", {"kind": "chromium", "profile": AGENT})
        pcui("ui")
    elif cmd == "press" and len(rest) == 1:
        press(rest[0])
    elif cmd == "set" and len(rest) >= 2:
        set_text(rest[0], " ".join(rest[1:]))
    elif cmd == "text" and not rest:
        text()
    elif cmd == "upload" and len(rest) == 2:
        st = load("state.json") or {}
        pcui("upload", *rest, profile=st.get("profile"))
    elif cmd in ("close", "focus", "max", "min") and len(rest) == 1:
        window_action(cmd, rest[0])
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
