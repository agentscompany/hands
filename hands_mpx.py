"""Each agent's own pointer and keyboard on the X desktop (MPX: an XInput 2.2 master device per agent), so agents click
and type on the screen at the same time instead of taking turns on the one real pointer.

The master "hands-<agent>" comes with its own XTEST slaves: clicks and keys are faked on those, the pointer is warped
with XIWarpPointer, and keys go to the window set with XISetFocus (each master has its own keyboard focus). Nothing is
installed for this: libX11, libXi and libXtst come with Chromium. Tested with Xvnc (TigerVNC), GTK apps and Chromium.

hand(agent) is None when it cannot work (no XInput 2.2, HANDS_MPX=0, or no room for another master: the X server has
40 devices, 4 per master); callers then use the real pointer in the agent's turn, as before.
"""
import ctypes as C, os, threading, time

_X = _Xi = _Xt = None


def _libs():
    global _X, _Xi, _Xt
    if _X:
        return
    X, Xi, Xt = C.CDLL("libX11.so.6"), C.CDLL("libXi.so.6"), C.CDLL("libXtst.so.6")
    X.XOpenDisplay.restype = C.c_void_p
    X.XDefaultRootWindow.argtypes, X.XDefaultRootWindow.restype = [C.c_void_p], C.c_ulong
    X.XSync.argtypes = [C.c_void_p, C.c_int]
    X.XKeysymToKeycode.argtypes, X.XKeysymToKeycode.restype = [C.c_void_p, C.c_ulong], C.c_ubyte
    X.XStringToKeysym.argtypes, X.XStringToKeysym.restype = [C.c_char_p], C.c_ulong
    X.XkbKeycodeToKeysym.argtypes, X.XkbKeycodeToKeysym.restype = [C.c_void_p, C.c_ubyte, C.c_int, C.c_int], C.c_ulong
    X.XSetErrorHandler.argtypes = [C.c_void_p]
    Xi.XIQueryVersion.argtypes = [C.c_void_p, C.POINTER(C.c_int), C.POINTER(C.c_int)]
    Xi.XIChangeHierarchy.argtypes = [C.c_void_p, C.c_void_p, C.c_int]
    Xi.XIQueryDevice.argtypes, Xi.XIQueryDevice.restype = [C.c_void_p, C.c_int, C.POINTER(C.c_int)], C.POINTER(_Dev)
    Xi.XIFreeDeviceInfo.argtypes = [C.POINTER(_Dev)]
    Xi.XIWarpPointer.argtypes = [C.c_void_p, C.c_int, C.c_ulong, C.c_ulong, C.c_double, C.c_double, C.c_uint, C.c_uint,
                                 C.c_double, C.c_double]
    Xi.XISetFocus.argtypes = [C.c_void_p, C.c_int, C.c_ulong, C.c_ulong]
    Xi.XOpenDevice.argtypes, Xi.XOpenDevice.restype = [C.c_void_p, C.c_ulong], C.c_void_p
    for f in (Xt.XTestFakeDeviceButtonEvent, Xt.XTestFakeDeviceKeyEvent):
        f.argtypes = [C.c_void_p, C.c_void_p, C.c_uint, C.c_int, C.c_void_p, C.c_int, C.c_ulong]
    # A failed request (a window that just closed, no room for a master) must not kill handsd: Xlib's default exits.
    global _ignore
    _ignore = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(lambda d, e: 0)
    X.XSetErrorHandler(C.cast(_ignore, C.c_void_p))
    _X, _Xi, _Xt = X, Xi, Xt


class _Dev(C.Structure):
    _fields_ = [("deviceid", C.c_int), ("name", C.c_char_p), ("use", C.c_int), ("attachment", C.c_int),
                ("enabled", C.c_int), ("num_classes", C.c_int), ("classes", C.c_void_p)]


class _AddMaster(C.Structure):
    _fields_ = [("type", C.c_int), ("name", C.c_char_p), ("send_core", C.c_int), ("enable", C.c_int)]


class _RemoveMaster(C.Structure):
    _fields_ = [("type", C.c_int), ("deviceid", C.c_int), ("return_mode", C.c_int), ("return_pointer", C.c_int),
                ("return_keyboard", C.c_int)]


def _devices(d):
    n = C.c_int()
    p = _Xi.XIQueryDevice(d, 0, C.byref(n))   # XIAllDevices
    out = {p[i].name.decode(): p[i].deviceid for i in range(n.value)}
    _Xi.XIFreeDeviceInfo(p)
    return out


def _remove(d, pointer_id):
    r = _RemoveMaster(2, pointer_id, 1, 2, 3)   # XIRemoveMaster; slaves back to the core pointer (2) and keyboard (3)
    _Xi.XIChangeHierarchy(d, C.byref(r), 1)
    _X.XSync(d, 0)


MODS = {"ctrl": "Control_L", "control": "Control_L", "shift": "Shift_L", "alt": "Alt_L", "super": "Super_L",
        "meta": "Meta_L", "win": "Super_L"}
CHARS = {"\n": "Return", "\t": "Tab"}


class Hand:
    """One agent's pointer and keyboard, on its own connection to X (Xlib connections are not shared across threads;
    handsd runs one command at a time per agent)."""

    def __init__(self, agent):
        _libs()
        d = _X.XOpenDisplay(os.environ.get("DISPLAY", ":1").encode())
        if not d:
            raise OSError("no X display")
        major, minor = C.c_int(2), C.c_int(2)
        if _Xi.XIQueryVersion(d, C.byref(major), C.byref(minor)) != 0:
            raise OSError("no XInput 2.2")
        self.d, self.root, self.name = d, _X.XDefaultRootWindow(d), f"hands-{agent}"[:60]
        ds = _devices(d)
        if self.name + " pointer" in ds:   # left by an earlier handsd: a fresh one, with no key still held down
            _remove(d, ds[self.name + " pointer"])
        m = _AddMaster(1, self.name.encode(), 1, 1)   # XIAddMaster, sends core events, enabled
        _Xi.XIChangeHierarchy(d, C.byref(m), 1)
        _X.XSync(d, 0)
        ds = _devices(d)
        try:
            self.pointer, self.keyboard = ds[self.name + " pointer"], ds[self.name + " keyboard"]
            self.xp = _Xi.XOpenDevice(d, ds[self.name + " XTEST pointer"])
            self.xk = _Xi.XOpenDevice(d, ds[self.name + " XTEST keyboard"])
        except KeyError:
            raise OSError("no room for another pointer")

    def close(self):
        _remove(self.d, self.pointer)

    def move(self, x, y):
        _Xi.XIWarpPointer(self.d, self.pointer, 0, self.root, 0, 0, 0, 0, x, y)
        _X.XSync(self.d, 0)

    def click(self, button=1, times=1):
        for _ in range(times):
            _Xt.XTestFakeDeviceButtonEvent(self.d, self.xp, button, 1, None, 0, 0)
            _Xt.XTestFakeDeviceButtonEvent(self.d, self.xp, button, 0, None, 0, 0)
        _X.XSync(self.d, 0)

    def focus(self, window):
        """Where this agent's keys go: its own keyboard focus, apart from the screen's and the other agents'."""
        _Xi.XISetFocus(self.d, self.keyboard, window, 0)
        _X.XSync(self.d, 0)

    def _code(self, keysym):
        """(keycode, needs Shift) for a keysym on the current keymap, or None."""
        kc = _X.XKeysymToKeycode(self.d, keysym) if keysym else 0
        if not kc:
            return None
        if _X.XkbKeycodeToKeysym(self.d, kc, 0, 0) == keysym:
            return kc, False
        return (kc, True) if _X.XkbKeycodeToKeysym(self.d, kc, 0, 1) == keysym else None

    def _tap(self, codes):
        """Presses the keycodes in order and lets go in reverse (a combination), then flushes."""
        for kc in codes:
            _Xt.XTestFakeDeviceKeyEvent(self.d, self.xk, kc, 1, None, 0, 0)
        for kc in reversed(codes):
            _Xt.XTestFakeDeviceKeyEvent(self.d, self.xk, kc, 0, None, 0, 0)
        _X.XSync(self.d, 0)

    def keys(self, combos):
        """xdotool's key syntax ("ctrl+a", "Return"): False, with nothing pressed, if one of them is not on the keymap."""
        plan = []
        for combo in combos:
            codes = []
            for part in combo.split("+"):
                c = self._code(_X.XStringToKeysym(MODS.get(part.lower(), part).encode()))
                if not c:
                    return False
                codes += [self._code(_X.XStringToKeysym(b"Shift_L"))[0]] * c[1] + [c[0]]
            plan.append(codes)
        for codes in plan:
            self._tap(codes)
        return True

    def type(self, text):
        """False, with nothing typed, if a character is not on the keymap (xdotool remaps a spare key for those, which
        changes the keymap for everyone: the caller types it in the agent's turn instead)."""
        shift = self._code(_X.XStringToKeysym(b"Shift_L"))[0]
        plan = []
        for ch in text:
            o = ord(ch)
            ks = _X.XStringToKeysym(CHARS[ch].encode()) if ch in CHARS else o if o < 0x100 else 0x1000000 | o
            c = self._code(ks)
            if not c:
                return False
            plan.append([shift, c[0]] if c[1] else [c[0]])
        for codes in plan:
            self._tap(codes)
            time.sleep(0.012)   # as xdotool type --delay 12: apps drop keys that come all at once
        return True


_hands, _lock = {}, threading.Lock()


def hand(agent):
    """The agent's Hand, made on first use; None if MPX cannot work here (the caller takes the agent's turn)."""
    if os.environ.get("HANDS_MPX") == "0" or not agent:
        return None
    with _lock:
        if agent not in _hands:
            try:
                _hands[agent] = Hand(agent)
            except OSError:
                _hands[agent] = None   # ponytail: never retried in this handsd; a restart tries again
        return _hands[agent]
