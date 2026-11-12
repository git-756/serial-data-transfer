#!/usr/bin/env python3
"""Jetson UART loopback test (short Pin8 TXD to Pin10 RXD).

Example: python3 loopback_test.py -p /dev/ttyTHS1 -b 115200 460800 921600
Writes random data at each baud rate, reads it back, and reports matches and speed.
"""
import argparse
import os
import time

import serial


def run(port: str, baud: int, size: int, rtscts: bool) -> bool:
    data = os.urandom(size)
    with serial.Serial(port, baud, timeout=0.2, rtscts=rtscts) as s:
        s.reset_input_buffer()
        got = bytearray()
        t0 = time.monotonic()
        # Write and read in chunks so the input buffer can't overflow.
        step = 256
        for i in range(0, size, step):
            s.write(data[i:i + step])
            got += s.read(s.in_waiting or 0)
        deadline = time.monotonic() + max(2.0, size * 10 / baud * 2)
        while len(got) < size and time.monotonic() < deadline:
            got += s.read(size - len(got))
        dt = time.monotonic() - t0
    ok = bytes(got) == data
    bad = sum(a != b for a, b in zip(got, data))
    print(f"{baud:>8} bps  sent={size} recv={len(got)} mismatched_bytes={bad} "
          f"{'OK' if ok else 'NG'}  {len(got) / dt / 1024:.1f} KiB/s")
    return ok


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-p", "--port", default="/dev/ttyTHS1")
    ap.add_argument("-b", "--baud", type=int, nargs="+", default=[115200])
    ap.add_argument("-n", "--size", type=int, default=64 * 1024, help="test size in bytes")
    ap.add_argument("--rtscts", action="store_true",
                    help="enable RTS/CTS (also short Pin11 to Pin36)")
    a = ap.parse_args()
    results = [run(a.port, b, a.size, a.rtscts) for b in a.baud]
    raise SystemExit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
