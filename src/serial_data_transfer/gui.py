"""serial-xfer-gui: a simple transfer window (tkinter), mainly for the Windows side.

- Pick files or folders in the Explorer dialog and send them to Jetson
- Browse the Jetson folder list and receive files
- Files passed on the command line (Explorer's "Send to" or dropping on the icon) are sent at startup

  serial-xfer-gui                    open the window
  serial-xfer-gui FILE...            open and send FILE
  serial-xfer-gui --create-shortcuts create Desktop and "Send to" shortcuts (Windows)
"""
import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import types

from . import serial_xfer as sx

APP_TITLE = "Jetson ファイル転送"
SENDTO_NAME = "Jetson へ送る"
BAUDS = (3000000, 2000000, 1000000, 921600, 460800, 115200)
DEFAULTS = {
    "port": "",
    "baud": 3000000,
    "rtscts": True,
    "chunk": 59000,
    "timeout": 2.0,
    "tries": 10,
    "save_dir": "",
}
FTDI_VID = 0x0403


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------
def config_path():
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "serial-xfer", "gui.json")


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(config_path(), encoding="utf-8") as f:
            cfg.update({k: v for k, v in json.load(f).items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    path = config_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def default_save_dir():
    d = os.path.join(os.path.expanduser("~"), "Downloads")
    return d if os.path.isdir(d) else os.path.expanduser("~")


def list_ports():
    """[(device, description, is_ftdi)] with FTDI devices first."""
    try:
        from serial.tools import list_ports as lp
    except ImportError:
        return []
    out = [(p.device, p.description or "", p.vid == FTDI_VID) for p in lp.comports()]
    out.sort(key=lambda x: (not x[2], x[0]))
    return out


def parse_ls(text):
    """Parse an ls reply into [(name, is_dir, size)]."""
    entries = []
    for line in text.splitlines():
        parts = line.split(None, 2)
        if len(parts) != 3:
            continue
        kind, size, name = parts
        if kind == "d":
            entries.append((name.rstrip("/"), True, None))
        elif kind == "f":
            try:
                entries.append((name, False, int(size)))
            except ValueError:
                pass
    return entries


def join_remote(folder, name):
    return (folder.strip("/") + "/" + name).strip("/")


def parent_remote(folder):
    folder = folder.strip("/")
    return folder.rsplit("/", 1)[0] if "/" in folder else ""


def friendly_error(e):
    if isinstance(e, Cancelled):
        return "中止しました"
    if isinstance(e, sx.LinkTimeout):
        return ("Jetson から応答がありません。\n"
                "ケーブル、ポート、速度（Jetson 側と同じか）、Jetson 側のサービスが動いているかを確認してください。")
    name = type(e).__name__
    if name == "SerialException":
        return "ポートを開けません（他のソフトが使用中かもしれません）:\n%s" % e
    return "%s: %s" % (name, e)


# --------------------------------------------------------------------------
# Transfer logic (no tk; testable on its own)
# --------------------------------------------------------------------------
class Cancelled(Exception):
    pass


def serial_opener(cfg):
    """Returns a function that opens the serial port per operation."""
    def opener():
        args = types.SimpleNamespace(port=cfg["port"], baud=int(cfg["baud"]), rtscts=bool(cfg["rtscts"]))
        link, ser = sx.open_link(args)
        return link, ser.close
    return opener


class Client:
    """Talks to the Jetson-side serve. Each operation opens and closes the port."""

    def __init__(self, opener, chunk=59000, timeout=2.0, tries=10, log=None, progress=None,
                 cancel=None):
        self.opener = opener
        self.chunk, self.timeout, self.tries = chunk, timeout, tries
        self.log = log or (lambda s: None)
        self.on_progress = progress or (lambda name, pos, size, rate: None)
        self.cancel = cancel or threading.Event()
        self._t = {}

    def _progress(self, name, pos, size):
        if self.cancel.is_set():
            raise Cancelled()
        now = time.monotonic()
        t0, p0 = self._t.setdefault(name, (now, pos))
        rate = (pos - p0) / (now - t0) if now > t0 else 0.0
        self.on_progress(name, pos, size, rate)

    def _sender(self, link):
        return sx.Sender(link, self.chunk, self.timeout, self.tries, log=self.log,
                         progress=self._progress)

    def _with_link(self, fn):
        self._t = {}
        link, close = self.opener()
        try:
            return fn(link)
        finally:
            close()

    def ls(self, remote=""):
        def op(link):
            status, _, extra = self._sender(link).request(sx.T_LS, remote.encode("utf-8"))
            if status != sx.ST_OK:
                raise sx.TransferError("%s: %s" % (remote or "/", sx.ST_TEXT.get(status, status)))
            return parse_ls(extra.decode("utf-8", "replace"))
        return self._with_link(op)

    def put(self, paths, dest=""):
        files = []
        for p in paths:
            files += sx.collect_files(p, dest)

        def op(link):
            self.log("%d 個のファイルを送信します" % len(files))
            self._sender(link).send_files(files)
            return len(files)
        return self._with_link(op)

    def get(self, remotes, outdir):
        def op(link):
            snd = self._sender(link)
            for name in remotes:
                if self.cancel.is_set():
                    raise Cancelled()
                status, n, _ = snd.request(sx.T_GET, name.encode("utf-8"), implicit_ack=(sx.T_START,))
                if status != sx.ST_OK:
                    raise sx.TransferError("%s: %s" % (name, sx.ST_TEXT.get(status, status)))
                self.log("受信: %s" % name)
                sx.Receiver(link, outdir, allow_serve=False, log=self.log,
                            progress=self._progress).run(until_bye=True, idle_timeout=30)
            return len(remotes)
        return self._with_link(op)


# --------------------------------------------------------------------------
# Windows shortcuts
# --------------------------------------------------------------------------
_PS_SHORTCUTS = r"""
$w = New-Object -ComObject WScript.Shell
$items = @(
  @([Environment]::GetFolderPath('Desktop'), $env:SX_DESKTOP_NAME),
  @([Environment]::GetFolderPath('SendTo'),  $env:SX_SENDTO_NAME)
)
foreach ($it in $items) {
  $s = $w.CreateShortcut((Join-Path $it[0] ($it[1] + '.lnk')))
  $s.TargetPath = $env:SX_EXE
  $s.WorkingDirectory = $env:USERPROFILE
  $s.Description = $env:SX_DESKTOP_NAME
  $s.Save()
}
"""


def gui_exe_path():
    a0 = os.path.abspath(sys.argv[0])
    if a0.lower().endswith(".exe") and os.path.isfile(a0):
        return a0
    return shutil.which("serial-xfer-gui")


def create_shortcuts():
    """Create shortcuts on the Desktop and in "Send to". Returns the exe path."""
    if os.name != "nt":
        raise RuntimeError("ショートカット作成は Windows のみ対応しています")
    exe = gui_exe_path()
    if not exe:
        raise RuntimeError("serial-xfer-gui.exe が見つかりません（uv tool install 済みか確認してください）")
    env = dict(os.environ, SX_EXE=exe, SX_DESKTOP_NAME=APP_TITLE, SX_SENDTO_NAME=SENDTO_NAME)
    r = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", _PS_SHORTCUTS],
        env=env, capture_output=True, text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0:
        raise RuntimeError("ショートカットの作成に失敗しました:\n%s" % (r.stderr or r.stdout))
    return exe


def open_folder(path):
    if os.name == "nt":
        os.startfile(path)  # noqa: S606 (opens a folder the user picked)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


# --------------------------------------------------------------------------
# Screen
# --------------------------------------------------------------------------
class App:
    def __init__(self, root, initial_files=()):
        import tkinter as tk
        from tkinter import ttk

        self.tk, self.ttk = tk, ttk
        self.root = root
        self.cfg = load_config()
        if not self.cfg["save_dir"] or not os.path.isdir(self.cfg["save_dir"]):
            self.cfg["save_dir"] = default_save_dir()
        self.q = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False
        self.remote_dir = ""
        self.entries = []
        self.initial_files = list(initial_files)

        root.title(APP_TITLE)
        root.geometry("720x560")
        root.minsize(560, 420)
        self._build()
        self.refresh_ports()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(50, self._poll)
        root.after(300, self._startup)

    # ---- layout
    def _build(self):
        tk, ttk = self.tk, self.ttk
        pad = {"padx": 6, "pady": 4}

        top = ttk.LabelFrame(self.root, text="接続")
        top.pack(fill="x", **pad)
        ttk.Label(top, text="ポート").grid(row=0, column=0, sticky="w", **pad)
        self.port_var = tk.StringVar()
        self.port_box = ttk.Combobox(top, textvariable=self.port_var, width=34, state="readonly")
        self.port_box.grid(row=0, column=1, sticky="we", **pad)
        ttk.Button(top, text="再検索", command=self.refresh_ports).grid(row=0, column=2, **pad)
        ttk.Label(top, text="速度").grid(row=0, column=3, sticky="w", **pad)
        self.baud_var = tk.StringVar(value=str(self.cfg["baud"]))
        ttk.Combobox(top, textvariable=self.baud_var, values=[str(b) for b in BAUDS], width=9
                     ).grid(row=0, column=4, **pad)
        self.rtscts_var = tk.BooleanVar(value=bool(self.cfg["rtscts"]))
        ttk.Checkbutton(top, text="RTS/CTS", variable=self.rtscts_var).grid(row=0, column=5, **pad)
        top.columnconfigure(1, weight=1)

        mid = ttk.LabelFrame(self.root, text="Jetson 側のファイル")
        mid.pack(fill="both", expand=True, **pad)
        bar = ttk.Frame(mid)
        bar.pack(fill="x")
        ttk.Button(bar, text="↑ 上へ", command=self.go_up).pack(side="left", **pad)
        ttk.Button(bar, text="更新", command=self.refresh_remote).pack(side="left", **pad)
        self.path_var = tk.StringVar(value="/")
        ttk.Label(bar, textvariable=self.path_var).pack(side="left", **pad)

        tf = ttk.Frame(mid)
        tf.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        self.tree = ttk.Treeview(tf, columns=("size",), selectmode="extended")
        self.tree.heading("#0", text="名前")
        self.tree.heading("size", text="サイズ")
        self.tree.column("#0", width=440)
        self.tree.column("size", width=120, anchor="e")
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self.on_double)

        acts = ttk.Frame(self.root)
        acts.pack(fill="x", **pad)
        self.btns = [
            ttk.Button(acts, text="ファイルを送信…", command=self.send_files),
            ttk.Button(acts, text="フォルダを送信…", command=self.send_folder),
            ttk.Button(acts, text="選択したものを受信…", command=self.receive_selected),
        ]
        for b in self.btns:
            b.pack(side="left", **pad)
        self.cancel_btn = ttk.Button(acts, text="中止", command=self.cancel.set, state="disabled")
        self.cancel_btn.pack(side="right", **pad)

        prog = ttk.Frame(self.root)
        prog.pack(fill="x", **pad)
        self.pbar = ttk.Progressbar(prog, maximum=1000)
        self.pbar.pack(fill="x", padx=6)
        self.status_var = tk.StringVar(value="")
        ttk.Label(prog, textvariable=self.status_var).pack(anchor="w", padx=6)

        lf = ttk.Frame(self.root)
        lf.pack(fill="both", **pad)
        self.logbox = tk.Text(lf, height=7, state="disabled", wrap="none")
        self.logbox.pack(fill="both", padx=6, pady=(0, 6))

        menu = tk.Menu(self.root)
        m = tk.Menu(menu, tearoff=False)
        m.add_command(label="受信フォルダを開く", command=lambda: open_folder(self.cfg["save_dir"]))
        m.add_command(label="デスクトップと「送る」にショートカットを作成", command=self.make_shortcuts)
        menu.add_cascade(label="メニュー", menu=m)
        self.root.config(menu=menu)

    # ---- helpers
    def log(self, text):
        self.logbox.configure(state="normal")
        self.logbox.insert("end", time.strftime("%H:%M:%S ") + text.strip() + "\n")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")

    def refresh_ports(self):
        ports = list_ports()
        labels = ["%s  %s" % (d, desc) for d, desc, _ in ports]
        self.port_devices = [d for d, _, _ in ports]
        self.port_box["values"] = labels
        want = self.cfg["port"]
        idx = self.port_devices.index(want) if want in self.port_devices else (0 if ports else -1)
        if idx >= 0:
            self.port_box.current(idx)
        else:
            self.port_var.set("")
            self.log("シリアルポートが見つかりません。USB を挿してから「再検索」を押してください。")

    def current_cfg(self):
        i = self.port_box.current()
        if i < 0:
            raise RuntimeError("ポートを選んでください")
        try:
            baud = int(self.baud_var.get())
        except ValueError:
            raise RuntimeError("速度は数字で入力してください")
        self.cfg.update(port=self.port_devices[i], baud=baud, rtscts=bool(self.rtscts_var.get()))
        save_config(self.cfg)
        return self.cfg

    def make_client(self):
        cfg = self.current_cfg()
        self.cancel.clear()
        return Client(serial_opener(cfg), chunk=int(cfg["chunk"]), timeout=float(cfg["timeout"]),
                      tries=int(cfg["tries"]), log=lambda s: self.q.put(("log", s)),
                      progress=lambda *a: self.q.put(("progress", a)), cancel=self.cancel)

    def run_task(self, label, fn, on_done=None):
        """Run fn(client) in a worker thread. on_done(result) is called on the screen side."""
        if self.busy:
            return
        try:
            client = self.make_client()
        except RuntimeError as e:
            self.show_error(str(e))
            return
        self.set_busy(True, label)

        def work():
            try:
                self.q.put(("done", (on_done, fn(client), None)))
            except BaseException as e:  # noqa: BLE001 (shown to the user)
                self.q.put(("done", (on_done, None, e)))
        threading.Thread(target=work, daemon=True).start()

    def set_busy(self, busy, label=""):
        self.busy = busy
        for b in self.btns:
            b.configure(state="disabled" if busy else "normal")
        self.cancel_btn.configure(state="normal" if busy else "disabled")
        self.root.configure(cursor="watch" if busy else "")
        if busy:
            self.pbar["value"] = 0
            self.status_var.set(label)

    def show_error(self, text):
        from tkinter import messagebox
        self.log("エラー: " + text.replace("\n", " "))
        messagebox.showerror(APP_TITLE, text, parent=self.root)

    def _poll(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "log":
                    self.log(val)
                elif kind == "progress":
                    name, pos, size, rate = val
                    self.pbar["value"] = 1000.0 * pos / size if size else 1000
                    self.status_var.set("%s   %s / %s   %.0f KB/s" % (
                        name, sx.fmt_size(pos), sx.fmt_size(size), rate / 1024.0))
                elif kind == "done":
                    on_done, result, err = val
                    self.set_busy(False)
                    if err is not None:
                        self.status_var.set(friendly_error(err).splitlines()[0])
                        if not isinstance(err, Cancelled):
                            self.show_error(friendly_error(err))
                        else:
                            self.log("中止しました")
                    elif on_done:
                        on_done(result)
        except queue.Empty:
            pass
        self.root.after(50, self._poll)

    # ---- actions
    def _startup(self):
        if self.initial_files:
            self.send_paths(self.initial_files, ask=True)
        elif self.port_devices:
            self.refresh_remote()

    def refresh_remote(self):
        folder = self.remote_dir

        def done(entries):
            self.entries = entries
            self.tree.delete(*self.tree.get_children())
            for name, is_dir, size in entries:
                # Tk 8.6 is weak with emoji (outside the BMP), so folders get a trailing "/"
                self.tree.insert("", "end", iid=name, text=name + ("/" if is_dir else ""),
                                 values=("" if is_dir else sx.fmt_size(size),))
            self.path_var.set("/" + folder)
            self.status_var.set("接続OK（%d 項目）" % len(entries))
        self.run_task("一覧を取得中…", lambda c: c.ls(folder), done)

    def on_double(self, _event):
        sel = self.tree.focus()
        for name, is_dir, _ in self.entries:
            if name == sel and is_dir:
                self.remote_dir = join_remote(self.remote_dir, name)
                self.refresh_remote()
                return

    def go_up(self):
        if self.remote_dir:
            self.remote_dir = parent_remote(self.remote_dir)
            self.refresh_remote()

    def send_paths(self, paths, ask=False):
        from tkinter import messagebox
        paths = [p for p in paths if os.path.exists(p)]
        if not paths:
            return
        dest = self.remote_dir
        if ask:
            names = "\n".join(os.path.basename(p.rstrip("\\/")) for p in paths[:10])
            more = "\n…ほか %d 件" % (len(paths) - 10) if len(paths) > 10 else ""
            if not messagebox.askyesno(APP_TITLE, "Jetson の /%s に送信しますか？\n\n%s%s" % (
                    dest, names, more), parent=self.root):
                return

        def done(n):
            self.log("送信完了（%d ファイル）" % n)
            self.status_var.set("送信完了（%d ファイル）" % n)
            self.pbar["value"] = 1000
            self.refresh_remote()
        self.run_task("送信中…", lambda c: c.put(paths, dest), done)

    def send_files(self):
        from tkinter import filedialog
        paths = filedialog.askopenfilenames(parent=self.root, title="Jetson に送るファイルを選択")
        if paths:
            self.send_paths(list(paths))

    def send_folder(self):
        from tkinter import filedialog
        path = filedialog.askdirectory(parent=self.root, title="Jetson に送るフォルダを選択")
        if path:
            self.send_paths([path])

    def receive_selected(self):
        from tkinter import filedialog, messagebox
        names = list(self.tree.selection())
        if not names:
            messagebox.showinfo(APP_TITLE, "受信するファイルを一覧から選んでください", parent=self.root)
            return
        outdir = filedialog.askdirectory(parent=self.root, title="保存先フォルダを選択",
                                         initialdir=self.cfg["save_dir"])
        if not outdir:
            return
        self.cfg["save_dir"] = outdir
        save_config(self.cfg)
        remotes = [join_remote(self.remote_dir, n) for n in names]

        def done(n):
            self.log("受信完了（%d 件） → %s" % (n, outdir))
            self.status_var.set("受信完了（%d 件）" % n)
            self.pbar["value"] = 1000
            if messagebox.askyesno(APP_TITLE, "受信が完了しました。保存先フォルダを開きますか？\n%s" % outdir,
                                   parent=self.root):
                open_folder(outdir)
        self.run_task("受信中…", lambda c: c.get(remotes, outdir), done)

    def make_shortcuts(self):
        from tkinter import messagebox
        try:
            create_shortcuts()
        except RuntimeError as e:
            return self.show_error(str(e))
        messagebox.showinfo(APP_TITLE, "デスクトップに「%s」、右クリックの「送る」に「%s」を作成しました。"
                            % (APP_TITLE, SENDTO_NAME), parent=self.root)

    def on_close(self):
        if self.busy:
            from tkinter import messagebox
            if not messagebox.askyesno(APP_TITLE, "転送中です。中止して閉じますか？", parent=self.root):
                return
            self.cancel.set()
        self.root.destroy()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="serial-xfer-gui", description=APP_TITLE)
    ap.add_argument("files", nargs="*", help="起動時に送信するファイル/フォルダ")
    ap.add_argument("--create-shortcuts", action="store_true",
                    help="デスクトップと「送る」にショートカットを作って終了 (Windows)")
    args = ap.parse_args(argv)

    import tkinter as tk
    if args.create_shortcuts:
        try:
            create_shortcuts()
        except RuntimeError as e:
            from tkinter import messagebox
            r = tk.Tk()
            r.withdraw()
            messagebox.showerror(APP_TITLE, str(e))
            return 1
        return 0

    root = tk.Tk()
    App(root, initial_files=args.files)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
