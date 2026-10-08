# python3 test_desk_a11y.py: the window list and the numbering of the accessibility tree (no desktop needed).
import importlib.util, sys

spec = importlib.util.spec_from_file_location("desk_a11y", "desk-a11y.py")
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)

ws = d.parse_windows("0x00800003  0 7572   250  199  772  477  xfce4-terminal.Xfce4-terminal  desk Terminal - ac@desk: ~\n"
                     "0x00a00007  0 7574   190  159  900  560  thunar.Thunar         desk ac - Thunar\n"
                     "0x00c00001  0 81     0    0    10   10   x.X  desk\n", 0xa00007)
assert [(w["app"], w["title"], w["focused"]) for w in ws] == [
    ("xfce4-terminal", "Terminal - ac@desk: ~", False), ("thunar", "ac - Thunar", True), ("x", "", False)], ws
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
lines, paths = d.number(tree, lambda o: o)
assert lines == ['[1] button "Back"', '[2] text "" = "/home/ac"', '[3] table cell "Places"', '[4] table cell "Devices"',
                 "# 9 folders", '[5] terminal "Terminal"'], lines
assert paths == [[0, 0, 0], [0, 2], [1, 1], [1, 4], [3]], paths

d.LIMIT = 2
lines, paths = d.number(tree, lambda o: o)
assert len(paths) == 2 and lines[-1] == "(only the first 2 elements)", lines
print("desk-a11y: ok")
