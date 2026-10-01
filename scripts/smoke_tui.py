#!/usr/bin/env python3
"""Development-only PTY smoke tests; the application itself requires no Python."""
import errno
import fcntl
import os
from pathlib import Path
import pty
import select
import signal
import struct
import subprocess
import tempfile
import termios
import time


ROOT = Path(__file__).resolve().parent.parent
BINARY = ROOT / "target" / "debug" / "wifimap"
subprocess.run([str(ROOT / "scripts" / "cargo"), "build", "--locked"], cwd=ROOT, check=True)


class Session:
    def __init__(self, args):
        sync_read, sync_write = os.pipe()
        self.pid, self.master = pty.fork()
        if self.pid == 0:
            os.close(sync_write)
            os.read(sync_read, 1)
            os.close(sync_read)
            os.execve(str(BINARY), [str(BINARY), *args], {**os.environ, "TERM": "xterm-256color"})
        os.close(sync_read)
        self.returncode = None
        self.original = termios.tcgetattr(self.master)
        self.resize(120, 36)
        self.output = bytearray()
        os.write(sync_write, b"g")
        os.close(sync_write)
        self.read(1.0)

    def poll(self):
        if self.returncode is None:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid:
                self.returncode = os.waitstatus_to_exitcode(status)
        return self.returncode

    def __enter__(self):
        return self

    def __exit__(self, *_):
        if self.poll() is None:
            os.kill(self.pid, signal.SIGKILL)
        # Closing the PTY before reaping also unblocks a child's terminal drain.
        try:
            os.close(self.master)
        except OSError:
            pass
        deadline = time.monotonic() + 3
        while self.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert self.poll() is not None, "smoke child did not terminate"

    def resize(self, width, height):
        fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", height, width, 0, 0))
        if hasattr(self, "pid"):
            os.kill(self.pid, signal.SIGWINCH)

    def read(self, seconds=0.15):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self.master], [], [], max(0, deadline - time.monotonic()))
            if not ready:
                break
            try:
                chunk = os.read(self.master, 65536)
            except OSError as error:
                if error.errno == errno.EIO:
                    break
                raise
            if not chunk:
                break
            self.output.extend(chunk)
            # Ratatui queries the cursor while initializing its backend.
            if b"\x1b[6n" in chunk:
                os.write(self.master, b"\x1b[1;1R")

    def send(self, keys, seconds=0.15):
        os.write(self.master, keys.encode() if isinstance(keys, str) else keys)
        self.read(seconds)

    def expect(self, text):
        if text.encode() not in self.output:
            # A resize forces a complete redraw; ordinary frames update only
            # changed cells and can split a label across cursor escape codes.
            self.resize(119, 36)
            self.read(0.15)
            self.resize(120, 36)
            self.read(0.15)
        assert text.encode() in self.output, f"Missing {text!r} in PTY output: {self.output[-2500:]!r}"

    def close(self, keys="q"):
        if keys:
            self.send(keys)
        deadline = time.monotonic() + 3
        while self.poll() is None and time.monotonic() < deadline:
            self.read(0.1)
        assert self.poll() == 0, self.output[-2500:].decode(errors="replace")
        restored = termios.tcgetattr(self.master)
        mask = termios.ECHO | termios.ICANON | termios.ISIG
        assert restored[3] & mask == self.original[3] & mask, "terminal modes were not restored"
        self.expect("\x1b[?1049h")
        self.expect("\x1b[?1049l")
        os.close(self.master)


def walk(db):
    with Session(["--demo", "--db", str(db), "walk", "--interval", "0.1", "--name", "Smoke survey"]) as session:
        session.expect("WIFIMAP")
        session.expect("DEMO / SIMULATED")
        session.send("?")
        session.expect("Keyboard help")
        session.send("\x1b", 0.25)
        session.send("n")
        session.expect("New spot name")
        session.send("q?st room")  # Shortcut characters remain text while editing.
        assert session.poll() is None
        session.send("\r")
        session.send("s")
        session.send("t")
        session.send("b")
        session.send("b")
        session.expect("Replace saved ideal benchmark?")
        session.send("\r")  # Keep existing benchmark is selected by default.
        session.send("c")
        session.expect("Compare with prior walk")
        session.send("\r")
        session.send("r")
        session.send("\r")
        session.send("\r")
        session.resize(60, 15)
        session.read(0.2)
        session.expect("Resize terminal")
        session.resize(120, 36)
        session.read(0.2)
        session.close()
        assert not db.exists(), "demo touched the requested database"


def evaluation(db):
    with Session(["--demo", "--db", str(db), "eval"]) as session:
        session.expect("Ranked readings")
        session.send("\t")
        session.send("m")
        session.send("c")
        session.expect("Compare: before walk")
        session.send("\r")
        session.expect("Compare: after walk")
        session.send("\r")
        session.expect("Walk comparison")
        session.send("xf")
        session.send("\r")
        session.expect("Reading details")
        session.send("\t")
        session.send("\x1b[C")
        session.send("\x1b", 0.25)
        session.send("\x1b", 0.25)
        session.send("w")
        session.expect("Filter by SSID")
        session.send("\r")
        session.close("\x03")
        assert not db.exists(), "demo touched the requested database"


def interruption(db):
    for command, interrupt in (("walk", signal.SIGTERM), ("eval", signal.SIGINT)):
        with Session(["--demo", "--db", str(db), command]) as session:
            session.expect("WIFIMAP")
            os.kill(session.pid, interrupt)
            session.close(None)
            assert not db.exists(), "demo touched the requested database"


with tempfile.TemporaryDirectory(prefix="wifimap-smoke-") as directory:
    db = Path(directory) / "must-not-exist.sqlite3"
    walk(db)
    evaluation(db)
    interruption(db)
print("PTY smoke passed: walk/eval, editors, measurements, comparison, resize, signal cleanup, terminal restoration, demo isolation")
