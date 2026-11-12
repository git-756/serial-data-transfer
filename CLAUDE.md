# プロジェクト: Windows ⇔ Jetson シリアルファイル転送

## 目的
AE-TTL-232R（FT232RQ, USB-microB〜USB-A）でWindows PCとJetsonのGPIO UART(/dev/ttyTHS1)を接続し、
双方向にファイルをやり取りする。

## ファイル
- `serial_xfer.py` : 転送ツール本体（Windows/Jetson共通、依存は pyserial のみ）。サブコマンド serve / put / get / ls
- `test_serial_xfer.py` : ノイズ注入した擬似回線での自動テスト（実機不要）
- `README.md` : 配線・セットアップ・使い方

## 配線（確定事項）
AE-TTL-232R TXD→Jetson Pin10(RXD) / RXD→Pin8(TXD) / RTS→Pin36(CTS) / CTS→Pin11(RTS) / GND→Pin6。
**+5V端子(3番)はJetsonに接続しない**（USB 5Vが常時出力されるため電源が並列になる）。
**SW2は3.3V側**（Jetson GPIOは3.3Vロジック）。

## プロトコル要点
フレーム `A5 5A | type | len(2) | payload | CRC32`。ストップ&ウェイトでACK/再送。
START(サイズ+SHA-256+名前) → DATA(offset付き) → END で、SHA-256一致時のみ `.part` を本名にrename。
`.part` からの再開、同一ファイルのスキップ、root外パス(`..`)の拒否あり。
GET要求のACKが落ちた場合は START受信を暗黙ACKとして扱う（修正済み）。

## 現状
- 擬似回線テスト (`python3 test_serial_xfer.py`) は全て合格（フレーム約25%破損/欠落でも完走）。
- **実機（FT232R ⇔ Jetson UART）では未検証。**
- 実機は Jetson AGX Orin Dev Kit（R39.2.1）。`/dev/ttyTHS1` = 3100000.serial（UART1、header Pin8/10 = PR.02/PR.03）。
- 2026-11-10: dialout 追加済み、ポートは開ける。`loopback_test.py`（Pin8-Pin10 短絡）は 115200/460800/921600 とも **受信 0 バイト**。
  送信は完了扱いになるため、短絡位置か pinmux（GPIO のままで UART になっていない）が疑わしい。原因はまだ特定できていない。
- 2026-11-12: pinmux は正常と確認。jetson-io で Pin8/10 = UART1。pinconf は TX(pr2) が tristate=0 / enable-input=0、
  RX(pr3) が tristate=1 / enable-input=1 / pull-up で、UART として正しい設定。残る疑いは物理側（短絡位置・接触）。
- 2026-11-12: AE-TTL-232R を Jetson 自身の USB に挿し（`/dev/ttyUSB0`）、ttyTHS1 と TXD/RXD/GND で接続して実機検証。
  - 生データ双方向 16KiB: 115200/460800/921600 すべてエラー 0。→ 先の Pin8-10 ループバック失敗は短絡側の問題だった。
  - serve(ttyTHS1) ⇔ put/ls/get(ttyUSB0) の往復は全速度で SHA-256 一致。RTS/CTS なし。
    | baud | chunk | put | get |
    |---|---|---|---|
    | 115200 | 2048 | 10.1 KiB/s | 10.1 KiB/s |
    | 460800 | 2048 | 32.5 KiB/s | 32.2 KiB/s |
    | 921600 | 2048 | 51.0 KiB/s | 50.7 KiB/s |
    | 921600 | 8192 | 74.8 KiB/s | 74.8 KiB/s |
  - 1 フレームあたり約 17ms の待ちが乗っている。FTDI の latency_timer（Linux 既定 16ms）が主因と思われる（未検証、変更に root 必要）。
  - CLI 修正: `-p` などの共通オプションがサブコマンドの前に書けず必ずエラーになっていた（README の書き方が動かない）。前後どちらでも可にした。
  - Windows PC との接続、`--rtscts` はまだ試していない。

## 次にやること
1. 実機でループバック試験（Pin8-Pin10短絡）→ AE-TTL-232R接続で `ls` 疎通確認（115200, RTS/CTSなし）
2. put/get の往復でファイル一致確認 → ボーレートを 460800 / 921600 と上げて実測速度を記録
3. `--rtscts` の可否確認（Pin11/36のpinmux。必要なら jetson-io で UART1 を有効化）
4. 高速時のスループット改善が必要ならスライディングウィンドウ化を検討（現状はストップ&ウェイト）
5. Jetson側 serve を systemd で常駐化（任意）

## 注意
- Jetsonで /dev/ttyTHS1 を使う前に nvgetty の停止と dialout グループ追加が必要な場合あり（README参照）
- 実機の結果（速度・エラー・pinmux）が分かったらこのファイルの「現状」を更新すること
