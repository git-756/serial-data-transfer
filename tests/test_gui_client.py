"""Tests for the GUI's transfer logic (Client) and helpers on a simulated line (no hardware, no screen)."""
import os
import threading

import pytest

from serial_data_transfer import gui
from serial_data_transfer import serial_xfer as sx
from test_serial_xfer import make_pair


@pytest.fixture
def server(tmp_path):
    """Run serve (Receiver) in a thread. Returns (opener, server root)."""
    root = tmp_path / "jetson"
    (root / "docs").mkdir(parents=True)
    (root / "hello.txt").write_bytes(b"hello\n" * 100)
    (root / "docs" / "a.bin").write_bytes(os.urandom(30000))
    ea, eb = make_pair(corrupt=0.02, drop=0.02, junk=0.02, seed=7)
    rx = sx.Receiver(sx.Link(eb), str(root), chunk=4096, log=lambda s: None)
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            f = rx.link.recv(0.1)
            if f:
                rx.handle(*f)
    th = threading.Thread(target=loop, daemon=True)
    th.start()
    yield (lambda: (sx.Link(ea), lambda: None)), root
    stop.set()
    th.join(2)


def client(opener, **kw):
    return gui.Client(opener, chunk=4096, timeout=0.3, tries=30, **kw)


def test_ls(server):
    opener, _ = server
    assert client(opener).ls() == [("docs", True, None), ("hello.txt", False, 600)]
    assert client(opener).ls("docs") == [("a.bin", False, 30000)]


def test_put_files_and_folder_with_progress(server, tmp_path):
    opener, root = server
    src = tmp_path / "pc"
    (src / "photos").mkdir(parents=True)
    f1 = src / "model.bin"
    f1.write_bytes(os.urandom(50000))
    (src / "photos" / "p1.jpg").write_bytes(os.urandom(9000))
    seen = []
    n = client(opener, progress=lambda *a: seen.append(a)).put([str(f1), str(src / "photos")], dest="docs")
    assert n == 2
    assert (root / "docs" / "model.bin").read_bytes() == f1.read_bytes()
    assert (root / "docs" / "photos" / "p1.jpg").read_bytes() == (src / "photos" / "p1.jpg").read_bytes()
    last = {name: (pos, size) for name, pos, size, _ in seen}
    assert last["docs/model.bin"] == (50000, 50000)


def test_get_file_and_folder(server, tmp_path):
    opener, root = server
    out = tmp_path / "recv"
    seen = []
    n = client(opener, progress=lambda *a: seen.append(a)).get(["hello.txt", "docs"], str(out))
    assert n == 2
    assert (out / "hello.txt").read_bytes() == (root / "hello.txt").read_bytes()
    assert (out / "docs" / "a.bin").read_bytes() == (root / "docs" / "a.bin").read_bytes()
    assert any(name == "docs/a.bin" and pos == size == 30000 for name, pos, size, _ in seen)


def test_cancel_then_resume(server, tmp_path):
    opener, root = server
    f = tmp_path / "big.bin"
    f.write_bytes(os.urandom(80000))
    cancel = threading.Event()

    def prog(name, pos, size, rate):
        if pos >= 20000:
            cancel.set()
    with pytest.raises(gui.Cancelled):
        client(opener, progress=prog, cancel=cancel).put([str(f)])
    assert not (root / "big.bin").exists()
    assert (root / "big.bin.part").exists()
    # Sending again resumes from the .part and finishes
    assert client(opener).put([str(f)]) == 1
    assert (root / "big.bin").read_bytes() == f.read_bytes()


def test_get_missing_file(server, tmp_path):
    opener, _ = server
    with pytest.raises(sx.TransferError):
        client(opener).get(["nothing.bin"], str(tmp_path))


def test_helpers():
    assert gui.parse_ls("d             -  sub/\nf           12  a b.txt\njunk\n") == [
        ("sub", True, None), ("a b.txt", False, 12)]
    assert gui.join_remote("", "a") == "a"
    assert gui.join_remote("x/y", "a") == "x/y/a"
    assert gui.parent_remote("x/y") == "x"
    assert gui.parent_remote("x") == ""
    assert "応答がありません" in gui.friendly_error(sx.LinkTimeout())
    assert gui.friendly_error(gui.Cancelled()) == "中止しました"


def test_config_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    cfg = gui.load_config()
    assert cfg["baud"] == 3000000 and cfg["rtscts"] is True
    cfg.update(port="COM7", baud=921600, unknown="ignored")
    gui.save_config(cfg)
    again = gui.load_config()
    assert again["port"] == "COM7" and again["baud"] == 921600 and "unknown" not in again
