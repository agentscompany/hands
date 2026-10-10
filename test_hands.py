# python3 test_hands.py: the window list, the numbering of a tree, stable numbers, change lists, and the daemon's
# protocol (no desktop needed).
import os, subprocess, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hands_a11y as d

ws = d.parse_windows("0x00800003  0 7572   250  199  772  477  xfce4-terminal.Xfce4-terminal  desk Terminal - ac@desk: ~\n"
                     "0x00a00007  0 7574   190  159  900  560  thunar.Thunar         desk ac - Thunar\n"
                     "0x00c00001  0 81     0    0    10   10   x.X  desk\n"
                     "0x01c00004  0 2857   0    0    1279 799  com.microsoft.vscode.com.microsoft.VSCode  desk Welcome - Visual Studio Code\n", 0xa00007)
assert [(w["app"], w["title"], w["focused"]) for w in ws] == [
    ("xfce4-terminal", "Terminal - ac@desk: ~", False), ("thunar", "ac - Thunar", True), ("x", "", False),
    ("vscode", "Welcome - Visual Studio Code", False)], ws
assert d.show(ws[1], 2) == '[2] thunar "ac - Thunar" 900x560 (focused)'
assert d.find_window("files", ws)["id"] == 0xa00007 and d.find_window("thunar", ws)["id"] == 0xa00007 and d.find_window("2", ws)["id"] == 0xa00007


def n(role, name="", children=(), showing=True, actions=(), editable=False, value=""):
    return {"role": role, "name": name, "showing": showing, "actions": list(actions), "editable": editable,
            "value": value, "children": list(children)}


tree = n("frame", "ac - Thunar", [
    n("tool bar", "", [n("panel", "", [n("button", "Back", actions=["click"])]),
                       n("button", "Hidden", showing=False, actions=["click"]),
                       n("text", "", editable=True, value="/home/ac", actions=["activate"])]),
    n("table", "", [n("table cell", "", actions=["activate"]), n("table cell", "Places", actions=["activate"]),
                    n("table cell", "", actions=["activate"]), n("table cell", "Places", actions=["activate"]),
                    n("table cell", "Devices", actions=["activate"])]),
    n("status bar", "9 folders"),
    n("terminal", "Terminal", actions=["menu"]),
])
s = d.Session("test")
items = d.number(tree, lambda o: o)
lines, refs = d.numbered(s.ids("w"), items)
assert lines == ['[1] button "Back"', '[2] text "" = "/home/ac"', '[3] table cell "Places"', '[4] table cell "Devices"',
                 "# 9 folders", '[5] terminal "Terminal"'], lines
assert [refs[str(i)][0] for i in range(1, 6)] == [[0, 0, 0], [0, 2], [1, 1], [1, 4], [3]], refs

d.LIMIT = 2
items = d.number(tree, lambda o: o)
assert len([i for i in items if "key" in i]) == 2 and items[-1]["line"] == "(only the first 2 elements)", items
d.LIMIT = 150
print("tree: ok")

# Stable numbers: an element keeps its number; a new one gets a new number; a re-rendered one (new key, same role and
# name) takes the number of the one that went away.
ids = d.Ids()
assert ids.assign([("a", "button Save"), ("b", "link Home"), ("c", "field Email")]) == [1, 2, 3]
assert ids.assign([("x", "button New"), ("a", "button Save"), ("c", "field Email")]) == [4, 1, 3]
assert ids.assign([("b2", "link Home"), ("a", "button Save")]) == [2, 1]
print("stable numbers: ok")

# Changes since the last view: + new, ~ changed, - gone; nothing changed says so.
s = d.Session("test")
page = lambda *ls: [{"key": k, "sig": k, "line": l, "ref": k} for k, l in ls]
full = d.view(s, "p", "Title — url", page(("a", 'button "Save"'), ("b", 'field "Email"')), True)
assert full == 'Title — url\n[1] button "Save"\n[2] field "Email"', full
same = d.view(s, "p", "Title — url", page(("a", 'button "Save"'), ("b", 'field "Email"')), True)
assert same == "Title — url\n(no changes)", same
diff = d.view(s, "p", "Title — url", page(("b", 'field "Email" = "x"'), ("c", 'link "Next"')), True)
assert diff == 'Title — url\n~ [2] field "Email" = "x"\n+ [3] link "Next"\n- [1] button "Save"', diff
print("changes: ok")

# The title bar's buttons: openbox (frame extents top 26; wmctrl's y is one title bar low) and Chromium's own.
term = {"x": 250, "y": 199, "w": 772, "h": 477}
assert d.title_button(term, "close", 26) == (1006, 160) and d.title_button(term, "min", 26) == (964, 160), d.title_button(term, "close", 26)
chrome = {"x": 52, "y": 36, "w": 1177, "h": 648}
assert d.title_button(chrome, "close", 0) == (1208, 56) and d.title_button(chrome, "max", 0) == (1176, 56)
print("title buttons: ok")

# Keys for pages: xdotool names as CDP key events.
import hands_cdp as c
assert c.key_event("Return") == {"key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "modifiers": 0, "text": "\r", "unmodifiedText": "\r"}
e = c.key_event("ctrl+a")
assert (e["key"], e["code"], e["windowsVirtualKeyCode"], e["modifiers"], e["text"]) == ("a", "KeyA", 65, 2, ""), e
assert c.key_event("shift+Tab")["modifiers"] == 8 and c.key_event("F5")["windowsVirtualKeyCode"] == 116
for bad in ("hyper+a", "Nope", "ctrl+"):
    try:
        c.key_event(bad)
        raise AssertionError(bad)
    except SystemExit:
        pass
print("keys: ok")

# Turns on the real pointer and keyboard: in order of arrival; bounded; none while the person watching has control.
# Virtual cursors: one per agent in ~/.desk/cursors.json.
import threading
with tempfile.TemporaryDirectory() as tmp:
    d.HOME, d.CURSORS, d.TURN_WAIT, d.WAIT = tmp, os.path.join(tmp, "cursors.json"), 1, 0.01
    t, order = d.Turn(), []
    def act(name, hold):
        with t(d.Session(name)):
            order.append(name)
            time.sleep(hold)
    ths = [threading.Thread(target=act, args=(n, 0.1)) for n in ("a", "b", "c")]
    for th in ths:
        th.start()
        time.sleep(0.02)
    for th in ths:
        th.join()
    assert order == ["a", "b", "c"], order
    th = threading.Thread(target=act, args=("long", 1.5))
    th.start()
    time.sleep(0.05)
    t0 = time.time()
    try:
        act("late", 0)
        raise AssertionError("no timeout")
    except SystemExit as e:
        assert "busy for 1 s" in str(e) and 0.9 < time.time() - t0 < 1.5, e
    th.join()
    open(os.path.join(tmp, "control"), "w").close()
    try:
        act("x", 0)
        raise AssertionError("ran during take over")
    except SystemExit as e:
        assert "taken over" in str(e), e
    os.remove(os.path.join(tmp, "control"))
    s1, s2 = d.Session("alfred"), d.Session("nina")
    with d.aim(s1, 10.4, 20):
        d.mark(s2, 300, 400, True)
        import json
        cur = json.load(open(d.CURSORS))
        assert cur["alfred"]["acting"] and (cur["alfred"]["x"], cur["alfred"]["y"]) == (10, 20) and cur["nina"]["x"] == 300, cur
    assert not json.load(open(d.CURSORS))["alfred"]["acting"]
    d.mark(d.Session("desk", named=False), 1, 1)   # an unnamed agent has no cursor
    assert set(json.load(open(d.CURSORS))) == {"alfred", "nina"}
    d.HOME = d.CURSORS = None
print("turns and cursors: ok")

# The daemon: the first command starts it, the next ones go to it; it answers errors with their message and code; a
# second daemon leaves the first alone. A private runtime dir keeps this test off a real desktop's daemon.
with tempfile.TemporaryDirectory() as tmp:
    env = {**os.environ, "XDG_RUNTIME_DIR": tmp, "HOME": tmp}
    os.makedirs(os.path.join(tmp, ".desk"))
    hd = os.path.join(os.path.dirname(os.path.abspath(__file__)), "handsd.py")
    hands = lambda *a: subprocess.run([sys.executable, "-S", hd, *a], env=env, capture_output=True, text=True, timeout=20)
    r = hands("version")
    assert r.returncode == 0 and r.stdout.strip() == f"hands {d.VERSION} (daemon)", r
    assert os.path.exists(os.path.join(tmp, "hands.sock"))
    t = time.time()
    r = hands("press")
    assert r.returncode == 1 and r.stderr.startswith("usage: hands"), r
    r = subprocess.run([sys.executable, hd, "serve"], env=env, capture_output=True, timeout=10)   # leaves at once
    assert r.returncode == 0, r
    r = hands("version")
    assert r.stdout.strip().endswith("(daemon)"), r
    subprocess.run(["pkill", "-f", f"{hd} serve"])   # this test's daemon (its socket goes with tmp)
print("daemon: ok")
