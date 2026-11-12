#!/usr/bin/env python3
"""ノイズ入り擬似回線で serial_xfer を検証する (実機不要)。 python3 test_serial_xfer.py"""
import os
import queue
import random
import shutil
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import serial_xfer as sx


class End:
    def __init__(self, rx, tx, corrupt, drop, junk, rng):
        self.rx, self.tx = rx, tx
        self.corrupt, self.drop, self.junk, self.rng = corrupt, drop, junk, rng

    def write(self, data):
        r = self.rng.random()
        if r < self.drop:
            return
        data = bytearray(data)
        if r < self.drop + self.corrupt:
            data[self.rng.randrange(len(data))] ^= 1 << self.rng.randrange(8)
        if self.rng.random() < self.junk:
            data = bytearray(os.urandom(self.rng.randrange(1, 8))) + data
        self.tx.put(bytes(data))

    def read(self, timeout):
        try:
            return self.rx.get(timeout=timeout)
        except queue.Empty:
            return b""


def make_pair(corrupt=0.0, drop=0.0, junk=0.0, seed=1):
    a, b = queue.Queue(), queue.Queue()
    rng = random.Random(seed)
    return End(a, b, corrupt, drop, junk, rng), End(b, a, corrupt, drop, junk, rng)


def same(a, b):
    return open(a, "rb").read() == open(b, "rb").read()


def run(label, corrupt, drop, junk):
    tmp = tempfile.mkdtemp()
    try:
        src = os.path.join(tmp, "src"); root = os.path.join(tmp, "root"); out = os.path.join(tmp, "out")
        os.makedirs(os.path.join(src, "sub")); os.makedirs(root); os.makedirs(out)
        files = {"a.bin": os.urandom(100000), "sub/b.txt": b"hello\n" * 1000, "empty.dat": b"", "c.bin": os.urandom(2048)}
        for n, d in files.items():
            open(os.path.join(src, n), "wb").write(d)

        ea, eb = make_pair(corrupt, drop, junk)
        server = sx.Receiver(sx.Link(eb), root, chunk=1024, log=lambda *a: None)
        stop = threading.Event()

        def loop():
            while not stop.is_set():
                f = server.link.recv(0.1)
                if f:
                    server.handle(*f)
        th = threading.Thread(target=loop, daemon=True); th.start()

        client = sx.Link(ea)
        snd = sx.Sender(client, chunk=1024, timeout=0.3, tries=60, log=lambda *a: None)
        t0 = time.time()
        sys_stdout, sys.stdout = sys.stdout, open(os.devnull, "w")
        try:
            snd.send_files(sx.collect_files(src, "in"))
        finally:
            sys.stdout = sys_stdout
        for n in files:
            assert same(os.path.join(src, n), os.path.join(root, "in", "src", n)), "PUT mismatch " + n

        # ls
        st, cnt, extra = snd.request(sx.T_LS, b"in/src")
        assert st == 0 and b"a.bin" in extra, extra

        # get (ディレクトリ)
        st, n, _ = snd.request(sx.T_GET, b"in/src", implicit_ack=(sx.T_START,))
        assert st == 0, st
        sys.stdout = open(os.devnull, "w")
        try:
            sx.Receiver(client, out, allow_serve=False, log=lambda *a: None).run(until_bye=True, idle_timeout=20)
        finally:
            sys.stdout = sys_stdout
        for n in files:
            assert same(os.path.join(src, n), os.path.join(out, "src", n)), "GET mismatch " + n

        # 存在しない / パストラバーサル
        assert snd.request(sx.T_GET, b"nope.bin")[0] == sx.ST_NOTFOUND
        assert snd.request(sx.T_GET, b"../../etc/passwd")[0] == sx.ST_NOTFOUND
        st, _, _ = snd.request(sx.T_START, __import__("struct").pack(">Q32s", 1, b"x" * 32) + b"../evil")
        assert st == sx.ST_BADNAME

        stop.set(); th.join()
        print("PASS  %-28s (%.1fs)" % (label, time.time() - t0))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_resume():
    tmp = tempfile.mkdtemp()
    try:
        root = os.path.join(tmp, "root"); os.makedirs(root)
        data = os.urandom(50000)
        src = os.path.join(tmp, "f.bin"); open(src, "wb").write(data)
        # 20000バイト受信済みの .part を用意
        open(os.path.join(root, "f.bin.part"), "wb").write(data[:20000])
        ea, eb = make_pair()
        logs = []
        server = sx.Receiver(sx.Link(eb), root, log=logs.append)
        stop = threading.Event()
        th = threading.Thread(target=lambda: [server.handle(*f) for f in iter(lambda: server.link.recv(0.1) if not stop.is_set() else False, False) if f], daemon=True)
        th.start()
        snd = sx.Sender(sx.Link(ea), chunk=1024, timeout=0.5, log=lambda *a: None)
        sys_stdout, sys.stdout = sys.stdout, open(os.devnull, "w")
        try:
            snd.send_file(src, "f.bin")
        finally:
            sys.stdout = sys_stdout
        stop.set()
        assert same(src, os.path.join(root, "f.bin"))
        assert any("再開" in l for l in logs), logs
        print("PASS  %-28s" % "resume from .part")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    run("clean line", 0, 0, 0)
    run("5% corrupt, 5% drop, junk", 0.05, 0.05, 0.05)
    run("15% corrupt, 10% drop, junk", 0.15, 0.10, 0.10)
    test_resume()
    print("ALL PASSED")
