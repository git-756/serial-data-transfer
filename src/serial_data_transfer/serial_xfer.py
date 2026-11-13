#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
serial_xfer.py  -  UART(シリアル)経由の双方向ファイル転送ツール
Windows <-> Jetson (AE-TTL-232R / FT232R など) 用。同じファイルを両側で使います。

uv でインストールすると serial-xfer コマンドになります。
このファイル単体でも動きます (必要: Python 3.7+ と pyserial)。

使い方 (例):
  Jetson : serial-xfer serve -p /dev/ttyTHS1 -b 115200 --root ~/share
  Windows: serial-xfer put -p COM5 -b 115200 data.bin photos\\        # 送る
           serial-xfer get -p COM5 -b 115200 result.csv --outdir .    # 受け取る
           serial-xfer ls  -p COM5 -b 115200                          # 一覧

プロトコル概要:
  フレーム = A5 5A | type(1) | len(2) | payload | CRC32(4)   (CRC32はtype+len+payloadに対して)
  ストップ&ウェイト方式 (ACK再送)、SHA-256で最終検証、.part ファイルで中断再開に対応。
"""
import argparse
import hashlib
import os
import random
import struct
import sys
import time
import zlib

MAGIC = b"\xA5\x5A"
MAX_PAYLOAD = 60000

T_START, T_DATA, T_END, T_ACK, T_BYE, T_GET, T_LS = 1, 2, 3, 4, 5, 6, 7

ST_OK, ST_HASH, ST_BADNAME, ST_NOSESSION, ST_NOTFOUND, ST_INCOMPLETE = 0, 1, 2, 3, 4, 5
ST_TEXT = {
    ST_OK: "OK",
    ST_HASH: "SHA-256不一致 (破棄しました。再実行してください)",
    ST_BADNAME: "不正なファイル名/パス",
    ST_NOSESSION: "受信側にセッションがありません",
    ST_NOTFOUND: "見つかりません",
    ST_INCOMPLETE: "データが不足しています",
}


class LinkTimeout(Exception):
    pass


class TransferError(Exception):
    pass


# --------------------------------------------------------------------------
# 低レベル: フレームの送受信
# --------------------------------------------------------------------------
def encode_frame(ftype, payload=b""):
    body = struct.pack(">BH", ftype, len(payload)) + payload
    return MAGIC + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


class SerialTransport:
    """pyserial をラップ。read(timeout) は届いている分をまとめて返す。"""

    def __init__(self, ser):
        self.ser = ser

    def write(self, data):
        self.ser.write(data)
        self.ser.flush()

    def read(self, timeout):
        # ser.timeout は 0.05 秒固定。呼び出し側(Link.recv)がループする。
        b = self.ser.read(1)
        if not b:
            return b""
        n = self.ser.in_waiting
        if n:
            b += self.ser.read(n)
        return b


class Link:
    def __init__(self, transport):
        self.t = transport
        self.buf = bytearray()
        self.last_rx = time.monotonic()
        self.pending = []  # unget() されたフレーム

    def unget(self, frame):
        self.pending.insert(0, frame)

    def send(self, ftype, payload=b""):
        self.t.write(encode_frame(ftype, payload))

    def _parse(self):
        b = self.buf
        while True:
            i = b.find(MAGIC)
            if i < 0:
                if len(b) > 1:
                    del b[:-1]  # 末尾1バイトだけ残す(MAGICの前半かもしれない)
                return None
            if i > 0:
                del b[:i]
            if len(b) < 5:
                return None
            ftype, n = struct.unpack(">BH", bytes(b[2:5]))
            if n > MAX_PAYLOAD + 16:
                del b[:1]
                continue
            total = 5 + n + 4
            if len(b) < total:
                return None
            body = bytes(b[2:5 + n])
            (crc,) = struct.unpack(">I", bytes(b[5 + n:total]))
            if zlib.crc32(body) & 0xFFFFFFFF == crc:
                payload = bytes(b[5:5 + n])
                del b[:total]
                return ftype, payload
            del b[:1]  # CRC不一致: 1バイト進めて再同期

    def recv(self, timeout):
        """フレームを1つ返す。timeout秒で来なければ None。"""
        deadline = time.monotonic() + timeout
        while True:
            if self.pending:
                return self.pending.pop(0)
            f = self._parse()
            if f:
                return f
            left = deadline - time.monotonic()
            if left <= 0:
                # 途中まで来て止まった壊れフレームは捨てる
                if self.buf and time.monotonic() - self.last_rx > 0.5:
                    self.buf.clear()
                return None
            data = self.t.read(min(left, 0.05))
            if data:
                self.buf += data
                self.last_rx = time.monotonic()


# --------------------------------------------------------------------------
# 共通ユーティリティ
# --------------------------------------------------------------------------
def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b)
    return h.digest()


def _console():
    """進捗(\r 上書き)を出してよいコンソールなら stdout を返す。
    pythonw (Windows GUI) では stdout が None、systemd ではパイプなので出さない。"""
    out = sys.stdout
    try:
        return out if out is not None and out.isatty() else None
    except Exception:
        return None


def fmt_size(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return "%d%s" % (n, u) if u == "B" else "%.1f%s" % (n, u)
        n /= 1024.0


def collect_files(path, dest=""):
    """(実パス, 相対名) のリスト。ディレクトリは再帰。"""
    path = os.path.abspath(path)
    out = []
    if os.path.isdir(path):
        base = os.path.basename(path.rstrip("/\\")) or "root"
        for d, _, files in os.walk(path):
            for fn in sorted(files):
                full = os.path.join(d, fn)
                rel = os.path.join(base, os.path.relpath(full, path))
                out.append((full, _join_rel(dest, rel)))
    elif os.path.isfile(path):
        out.append((path, _join_rel(dest, os.path.basename(path))))
    else:
        raise TransferError("ファイルが見つかりません: %s" % path)
    return out


def _join_rel(dest, rel):
    rel = rel.replace("\\", "/")
    dest = dest.replace("\\", "/").strip("/")
    return (dest + "/" + rel) if dest else rel


# --------------------------------------------------------------------------
# 送信側
# --------------------------------------------------------------------------
class Sender:
    def __init__(self, link, chunk=2048, timeout=2.0, tries=10, log=print, progress=None):
        """progress(name, pos, size): 進捗コールバック。None ならコンソールに表示。"""
        self.link = link
        self.progress = progress
        self.chunk = max(64, min(chunk, MAX_PAYLOAD - 16))
        self.timeout = timeout
        self.tries = tries
        self.log = log
        self.seq = random.randrange(1, 1 << 31)

    def request(self, ftype, body, tries=None, timeout=None, implicit_ack=()):
        """要求を送り、同じseqのACKを待つ。戻り値 (status, offset, extra)
        implicit_ack: このtypeのフレームが来たら、ACKが落ちただけで相手は処理済みと見なす。"""
        self.seq = (self.seq + 1) & 0xFFFFFFFF
        seq = self.seq
        payload = struct.pack(">I", seq) + body
        tries = tries or self.tries
        timeout = timeout or self.timeout
        for _ in range(tries):
            self.link.send(ftype, payload)
            deadline = time.monotonic() + timeout
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                f = self.link.recv(left)
                if f is None:
                    break
                t, p = f
                if t == T_ACK and len(p) >= 13:
                    s, status, off = struct.unpack(">IBQ", p[:13])
                    if s == seq:
                        return status, off, p[13:]
                elif t in implicit_ack:
                    self.link.unget(f)
                    return ST_OK, 0, b""
        raise LinkTimeout("相手から応答がありません (配線/ボーレート/相手側のserve起動を確認)")

    def send_file(self, path, relname):
        size = os.path.getsize(path)
        sha = sha256_file(path)
        status, pos, _ = self.request(
            T_START, struct.pack(">Q32s", size, sha) + relname.encode("utf-8"))
        if status != ST_OK:
            raise TransferError("%s: %s" % (relname, ST_TEXT.get(status, status)))
        if pos > size:
            pos = 0
        t0 = time.monotonic()
        start_pos = pos
        stalls = 0
        with open(path, "rb") as f:
            while pos < size:
                f.seek(pos)
                data = f.read(self.chunk)
                status, new_pos, _ = self.request(T_DATA, struct.pack(">Q", pos) + data)
                if status != ST_OK:
                    raise TransferError("%s: %s" % (relname, ST_TEXT.get(status, status)))
                if new_pos == pos:
                    stalls += 1
                    if stalls >= 5:
                        raise TransferError("%s: 受信側が進みません" % relname)
                else:
                    stalls = 0
                pos = min(new_pos, size)
                self._progress(relname, pos, size, (pos - start_pos), t0)
        status, _, _ = self.request(T_END, sha)
        con = _console() if self.progress is None else None
        if con:
            con.write("\r")
        if status != ST_OK:
            raise TransferError("%s: %s" % (relname, ST_TEXT.get(status, status)))
        dt = max(time.monotonic() - t0, 1e-6)
        self.log("送信完了: %s (%s, %.1f KB/s)%s" % (
            relname, fmt_size(size), (size - start_pos) / 1024.0 / dt, " " * 20 if con else ""))

    def _progress(self, name, pos, size, sent, t0):
        if self.progress is not None:
            return self.progress(name, pos, size)
        con = _console()
        if con is None:
            return
        dt = max(time.monotonic() - t0, 1e-6)
        pct = 100.0 * pos / size if size else 100.0
        con.write("\r  %s  %5.1f%%  %s/%s  %.1f KB/s   " % (
            name[-40:], pct, fmt_size(pos), fmt_size(size), sent / 1024.0 / dt))
        con.flush()

    def send_files(self, files):
        for full, rel in files:
            self.send_file(full, rel)
        try:
            self.request(T_BYE, b"", tries=3, timeout=1.0)
        except LinkTimeout:
            pass


# --------------------------------------------------------------------------
# 受信側 (serve / get の受け取り)
# --------------------------------------------------------------------------
class _Cur:
    pass


class Receiver:
    def __init__(self, link, root, chunk=2048, once=False, allow_serve=True, log=print,
                 progress=None):
        """progress(name, received, size): 受信の進捗コールバック (任意)。"""
        self.link = link
        self.progress = progress
        self.root = os.path.abspath(root)
        self.chunk = chunk
        self.once = once
        self.allow_serve = allow_serve  # GET/LS に応じるか
        self.log = log
        self.cur = None
        self.last_done = None
        self.last_get_seq = None
        os.makedirs(self.root, exist_ok=True)

    # ---- ヘルパ
    def safe_path(self, name):
        name = name.replace("\\", "/").strip()
        parts = [p for p in name.split("/") if p not in ("", ".")]
        if any(p == ".." or ":" in p or "\x00" in p for p in parts):
            return None
        path = os.path.abspath(os.path.join(self.root, *parts))
        if path != self.root and not path.startswith(self.root + os.sep):
            return None
        return path

    def reply(self, seq, status, offset=0, extra=b""):
        self.link.send(T_ACK, struct.pack(">IBQ", seq, status, offset) + extra)

    def close_cur(self):
        if self.cur is not None:
            try:
                self.cur.fh.close()
            except Exception:
                pass
            self.cur = None

    # ---- メインループ
    def run(self, until_bye=False, idle_timeout=None):
        """until_bye=True: BYEを受けたら戻る(getの受信用)。"""
        last = time.monotonic()
        while True:
            f = self.link.recv(0.5)
            if f is None:
                if idle_timeout and time.monotonic() - last > idle_timeout:
                    raise LinkTimeout("相手から応答がありません")
                continue
            last = time.monotonic()
            if self.handle(*f) and (until_bye or self.once):
                return

    def handle(self, t, p):
        """BYE を処理したら True。"""
        if t not in (T_START, T_DATA, T_END, T_BYE, T_GET, T_LS) or len(p) < 4:
            return False
        (seq,) = struct.unpack(">I", p[:4])
        body = p[4:]
        if t == T_START:
            self._start(seq, body)
        elif t == T_DATA:
            self._data(seq, body)
        elif t == T_END:
            self._end(seq, body)
        elif t == T_BYE:
            self.close_cur()
            self.reply(seq, ST_OK)
            return True
        elif t == T_GET and self.allow_serve:
            self._get(seq, body)
        elif t == T_LS and self.allow_serve:
            self._ls(seq, body)
        return False

    def _start(self, seq, body):
        if len(body) < 41:
            return self.reply(seq, ST_BADNAME)
        size, sha = struct.unpack(">Q32s", body[:40])
        name = body[40:].decode("utf-8", "replace")
        path = self.safe_path(name)
        if path is None or path == self.root:
            self.log("拒否: 不正なパス %r" % name)
            return self.reply(seq, ST_BADNAME)
        c = self.cur
        if c and c.sha == sha and c.path == path and c.size == size:
            return self.reply(seq, ST_OK, c.received)  # START再送
        self.close_cur()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.isfile(path) and os.path.getsize(path) == size and sha256_file(path) == sha:
            self.last_done = sha
            self.log("スキップ(同一ファイルが既に存在): %s" % name)
            return self.reply(seq, ST_OK, size)
        part = path + ".part"
        received = 0
        if os.path.isfile(part):
            sz = os.path.getsize(part)
            if sz <= size:
                received = sz
            else:
                os.remove(part)
        c = _Cur()
        c.path, c.part, c.size, c.sha, c.received, c.name = path, part, size, sha, received, name
        c.fh = open(part, "ab" if received else "wb")
        self.cur = c
        self.log("受信開始: %s (%s)%s" % (
            name, fmt_size(size), "  ※%s から再開" % fmt_size(received) if received else ""))
        self.reply(seq, ST_OK, received)

    def _data(self, seq, body):
        c = self.cur
        if c is None or len(body) < 8:
            return self.reply(seq, ST_NOSESSION)
        (off,) = struct.unpack(">Q", body[:8])
        data = body[8:]
        if off == c.received and c.received + len(data) <= c.size:
            c.fh.write(data)
            c.fh.flush()
            c.received += len(data)
        self.reply(seq, ST_OK, c.received)
        if self.progress is not None:
            self.progress(c.name, c.received, c.size)

    def _end(self, seq, body):
        sha = body[:32]
        c = self.cur
        if c and c.sha == sha:
            if c.received != c.size:
                return self.reply(seq, ST_INCOMPLETE, c.received)
            c.fh.close()
            ok = sha256_file(c.part) == c.sha
            if ok:
                os.replace(c.part, c.path)
                self.last_done = sha
                self.log("受信完了: %s (%s)" % (c.name, fmt_size(c.size)))
            else:
                os.remove(c.part)
                self.log("SHA-256不一致: %s (破棄)" % c.name)
            self.cur = None
            return self.reply(seq, ST_OK if ok else ST_HASH)
        if self.last_done == sha:
            return self.reply(seq, ST_OK)  # END再送
        self.reply(seq, ST_NOSESSION)

    # ---- GET / LS (サーバ側)
    def _get(self, seq, body):
        name = body.decode("utf-8", "replace")
        path = self.safe_path(name)
        if path is None or not os.path.exists(path):
            return self.reply(seq, ST_NOTFOUND)
        files = collect_files(path)
        if seq == self.last_get_seq:  # GET再送(ACK喪失) → 二重送信しない
            return self.reply(seq, ST_OK, len(files))
        self.last_get_seq = seq
        self.reply(seq, ST_OK, len(files))
        self.log("送信要求(GET): %s (%d ファイル)" % (name, len(files)))
        s = Sender(self.link, chunk=self.chunk, log=self.log)
        try:
            s.send_files(files)
        except (LinkTimeout, TransferError) as e:
            self.log("GET送信中断: %s" % e)

    def _ls(self, seq, body):
        name = body.decode("utf-8", "replace")
        path = self.safe_path(name)
        if path is None or not os.path.isdir(path):
            return self.reply(seq, ST_NOTFOUND)
        lines = []
        for fn in sorted(os.listdir(path)):
            if fn.endswith(".part"):
                continue
            full = os.path.join(path, fn)
            if os.path.isdir(full):
                lines.append("d  %12s  %s/" % ("-", fn))
            else:
                lines.append("f  %12d  %s" % (os.path.getsize(full), fn))
        text = "\n".join(lines).encode("utf-8")[:MAX_PAYLOAD - 100]
        self.reply(seq, ST_OK, len(lines), text)


# --------------------------------------------------------------------------
# コマンドライン
# --------------------------------------------------------------------------
def open_link(args):
    import serial  # 遅延import (pip install pyserial)
    ser = serial.Serial(
        args.port, args.baud, bytesize=8, parity="N", stopbits=1,
        timeout=0.05, write_timeout=10, rtscts=args.rtscts, xonxoff=False)
    time.sleep(0.1)
    ser.reset_input_buffer()
    return Link(SerialTransport(ser)), ser


def main(argv=None):
    def common_parser(in_sub):
        # 共通オプションはサブコマンドの前でも後でも書けるようにする。
        # サブコマンド側は SUPPRESS にして、前に書いた値を既定値で上書きしない。
        def d(v):
            return argparse.SUPPRESS if in_sub else v
        c = argparse.ArgumentParser(add_help=False)
        c.add_argument("-p", "--port", default=d(None), help="COM5 / /dev/ttyTHS1 など")
        c.add_argument("-b", "--baud", type=int, default=d(115200))
        c.add_argument("--rtscts", action="store_true", default=d(False),
                       help="RTS/CTSハードウェアフロー制御を有効化")
        c.add_argument("--chunk", type=int, default=d(2048), help="1フレームのデータ長 (既定2048)")
        c.add_argument("--timeout", type=float, default=d(2.0), help="ACK待ち秒 (既定2.0)")
        c.add_argument("--tries", type=int, default=d(10), help="再送回数 (既定10)")
        return c

    common = common_parser(in_sub=True)
    ap = argparse.ArgumentParser(description="UART file transfer", parents=[common_parser(in_sub=False)])
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", parents=[common], help="待ち受け(受信 + 相手からのget/ls に応答)")
    s.add_argument("--root", default=".", help="受信先/公開ルートディレクトリ")
    s.add_argument("--once", action="store_true", help="1回の転送(BYE)で終了")
    s.add_argument("--no-serve", action="store_true", help="get/ls に応答しない(受信専用)")

    s = sub.add_parser("put", parents=[common], help="ファイル/フォルダを相手へ送る")
    s.add_argument("paths", nargs="+")
    s.add_argument("--dest", default="", help="相手側root内の保存先サブフォルダ")

    s = sub.add_parser("get", parents=[common], help="相手のファイル/フォルダを取得")
    s.add_argument("remote")
    s.add_argument("--outdir", default=".")

    s = sub.add_parser("ls", parents=[common], help="相手側rootの一覧")
    s.add_argument("remote", nargs="?", default="")

    args = ap.parse_args(argv)
    if not args.port:
        ap.error("-p/--port を指定してください")
    link, ser = open_link(args)
    try:
        if args.cmd == "serve":
            print("待ち受け開始: %s @%d  root=%s  (Ctrl+Cで終了)" % (
                args.port, args.baud, os.path.abspath(args.root)))
            Receiver(link, args.root, chunk=args.chunk, once=args.once,
                     allow_serve=not args.no_serve).run()
        elif args.cmd == "put":
            files = []
            for p in args.paths:
                files += collect_files(p, args.dest)
            print("%d ファイルを送信します" % len(files))
            Sender(link, args.chunk, args.timeout, args.tries).send_files(files)
            print("完了")
        elif args.cmd == "get":
            snd = Sender(link, args.chunk, args.timeout, args.tries)
            status, n, _ = snd.request(T_GET, args.remote.encode("utf-8"), implicit_ack=(T_START,))
            if status != ST_OK:
                raise TransferError("%s: %s" % (args.remote, ST_TEXT.get(status, status)))
            print("受信を開始します")
            Receiver(link, args.outdir, allow_serve=False).run(until_bye=True, idle_timeout=30)
            print("完了")
        elif args.cmd == "ls":
            snd = Sender(link, args.chunk, args.timeout, args.tries)
            status, n, extra = snd.request(T_LS, args.remote.encode("utf-8"))
            if status != ST_OK:
                raise TransferError("%s: %s" % (args.remote or "/", ST_TEXT.get(status, status)))
            print(extra.decode("utf-8", "replace") or "(空)")
    except KeyboardInterrupt:
        print("\n中断しました")
        return 130
    except (LinkTimeout, TransferError) as e:
        print("\nエラー: %s" % e)
        return 1
    finally:
        try:
            ser.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
