#!/usr/bin/env python3
"""handsd: Hands' daemon, and the thin client `hands` runs.

  handsd hold CMD [args]    (what the xdotool shim runs for an agent) runs CMD in the agent's turn on the real
                            pointer and keyboard (see Turn in hands_a11y.py)
  handsd <command> [args]   (what `hands` runs) sends the command to the daemon over a Unix socket and prints the
                            answer; starts the daemon if it is not running; runs the command directly
                            (hands_a11y.py) if the daemon cannot start.
  handsd serve              the daemon: one per user and desktop, started by the first command. It keeps the
                            connections to the browsers (CDP) and to AT-SPI open, listens to their events, and keeps
                            each agent's session (its last window or page, and stable element numbers).

The protocol: one JSON line per connection, {"argv", "agent", "screens", "cwd"}; the answer, {"out", "err", "code"}.
The client imports nothing heavy, so it starts in a few milliseconds.
"""
import json, os, socket, sys

HERE = os.path.dirname(os.path.realpath(__file__))


def sock_dir():
    """A directory only this user can use (the socket gives the desktop to whoever connects)."""
    d = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/hands-{os.getuid()}"
    os.makedirs(d, mode=0o700, exist_ok=True)
    st = os.stat(d)
    if st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise OSError(f"{d} is not this user's own")
    return d


def connect():
    s = socket.socket(socket.AF_UNIX)
    s.connect(os.path.join(sock_dir(), "hands.sock"))
    return s


def request(argv):
    return json.dumps({"argv": argv, "agent": os.environ.get("DESK_AGENT") or "",
                       "screens": os.environ.get("DESK_SCREENS") or "", "cwd": os.getcwd()}).encode() + b"\n"


def hold(argv):
    """Runs argv (xdotool) once the daemon gives this agent its turn; the turn ends when this connection closes. With
    no daemon, runs it at once."""
    import subprocess
    try:
        s = connect()
    except OSError:
        s = start()
    if s is None:
        os.execv(argv[0], argv)
    s.sendall(request(["hold"]))
    r = json.loads(s.makefile("rb").readline() or b'{"err": "hands: the daemon stopped", "code": 1}')
    if r.get("code"):
        sys.exit(r.get("err") or 1)
    code = subprocess.call(argv)
    s.close()
    sys.exit(code)


def client(argv):
    req = request(argv)
    for _ in range(3):
        try:
            s = connect()
        except OSError:
            s = start()
        if s is None:   # no daemon: the same command, run directly
            os.execv(sys.executable, [sys.executable, os.path.join(HERE, "hands_a11y.py"), *argv])
        s.sendall(req)
        data = b""
        while chunk := s.recv(65536):
            data += chunk
        try:
            r = json.loads(data)
        except ValueError:
            sys.exit("hands: the daemon stopped while running this command; run it again")
        if not r.get("restart"):   # an old daemon, of a Hands since updated, ran nothing: the next one will
            break
    if r["out"]:
        print(r["out"])
    if r["err"]:
        print(r["err"], file=sys.stderr)
    sys.exit(r["code"])


def start():
    """Starts the daemon in the background and waits (up to 5 s) for its socket."""
    import subprocess, time
    try:
        sock_dir()
    except OSError:
        return None
    try:
        log = open(os.path.join(os.path.expanduser("~/.desk"), "handsd.log"), "a")
    except OSError:
        log = subprocess.DEVNULL
    subprocess.Popen([sys.executable, os.path.realpath(__file__), "serve"], start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=log, stderr=log)
    end = time.time() + 5
    while time.time() < end:
        try:
            return connect()
        except OSError:
            time.sleep(0.02)
    return None


def serve():
    import fcntl, socketserver, threading, traceback
    sys.path.insert(0, HERE)
    import hands_a11y as H

    os.environ.pop("DESK_AGENT", None)   # the agent of each command is in its request; `hands` marked ~/.desk/last
    d = sock_dir()
    lock = open(os.path.join(d, "hands.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)   # one daemon: a second one started at the same time leaves
    except OSError:
        return
    path = os.path.join(d, "hands.sock")
    if os.path.exists(path):
        os.unlink(path)
    try:
        H.watch()
    except Exception as e:   # no AT-SPI here: GTK apps are read without events
        print("handsd: no AT-SPI events:", e, file=sys.stderr, flush=True)
    files = [os.path.join(HERE, f) for f in ("handsd.py", "hands_a11y.py", "hands_cdp.py")]
    mtimes = [os.path.getmtime(f) for f in files]
    sessions, guard = {}, threading.Lock()

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            req = json.loads(self.rfile.readline())
            if [os.path.getmtime(f) for f in files] != mtimes:   # Hands was updated: the client starts the new one
                self.wfile.write(b'{"restart": true}')
                self.wfile.flush()
                srv.socket.close()
                os.unlink(path)
                os._exit(0)
            with guard:
                if req["agent"] not in sessions:
                    sessions[req["agent"]] = (H.Session(req["agent"] or "desk", named=bool(req["agent"])), threading.Lock())
                s, mine = sessions[req["agent"]]
            if req["argv"][:1] == ["hold"]:
                return self.hold(s)
            out, err, code = "", "", 0
            with mine:   # one command at a time per agent; agents in parallel
                s.screens = req["screens"] or os.path.join(H.HOME, "screens")
                s.cwd = req["cwd"]
                try:
                    out = H.run(s, req["argv"])
                    if req["argv"][:1] == ["version"]:
                        out += " (daemon)"
                except SystemExit as e:
                    err, code = (e.code, 1) if isinstance(e.code, str) else ("", e.code or 0)
                except Exception:
                    traceback.print_exc()
                    err, code = "hands: " + traceback.format_exc().strip().splitlines()[-1], 1
            self.wfile.write(json.dumps({"out": out, "err": err, "code": code}).encode())
            self.wfile.flush()

        def hold(self, s):
            """The agent's turn, while its xdotool runs (up to 30 s); then its cursor goes where the pointer went."""
            where = lambda: H.run_("xdotool", "getmouselocation").split()[:2]
            try:
                with H.turn(s):
                    before = where()
                    H.mark(s, acting=True)
                    self.wfile.write(b'{"code": 0}\n')
                    self.wfile.flush()
                    self.connection.settimeout(30)
                    try:
                        self.rfile.read()   # until the client closes
                    except OSError:
                        pass
                    after = where()
                    if after != before and len(after) == 2:
                        H.mark(s, *(int(v.split(":")[1]) for v in after))
                    H.mark(s, acting=False)
            except SystemExit as e:
                self.wfile.write(json.dumps({"err": f"hands: {e.code}", "code": 1}).encode() + b"\n")

    class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        daemon_threads = True

    with Server(path, Handler) as srv:
        print(f"handsd {H.VERSION} on {path}", file=sys.stderr, flush=True)
        srv.serve_forever()


if __name__ == "__main__":
    if sys.argv[1:] == ["serve"]:
        serve()
    elif sys.argv[1:2] == ["hold"] and len(sys.argv) > 2:
        hold(sys.argv[2:])
    else:
        client(sys.argv[1:])
