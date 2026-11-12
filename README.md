# Windows ⇔ Jetson シリアルファイル転送 (AE-TTL-232R)

## 1. 配線（最重要）

AE-TTL-232R の 6 ピン端子（FTDI ケーブル互換）と Jetson 40 ピンヘッダ(J41)を **クロス接続** します。

| AE-TTL-232R | 方向 | Jetson 40pin ヘッダ | 備考 |
|---|---|---|---|
| 1 GND | ― | Pin 6 (GND) | 必須 |
| 4 TXD | → | **Pin 10 (UART1_RXD)** | TX↔RX はクロス |
| 5 RXD | ← | **Pin 8 (UART1_TXD)** | |
| 6 RTS | → | **Pin 36 (UART1_CTS)** | RTS↔CTS もクロス |
| 2 CTS | ← | **Pin 11 (UART1_RTS)** | |
| 3 +5V | ✕ | **接続しない** | 下記参照 |

デバイス名は Jetson 側 `/dev/ttyTHS1`（Nano / Xavier NX / Orin Nano・NX の 40pin UART）。

### ⚠ 必ず確認すること
1. **+5V(3番)は Jetson の 5V ピンに繋がない。** 3番は USB の 5V が常時出ており(最大350mA)、Jetson 自身の電源と並列接続になって危険です。Jetson は自分の電源で動かし、通信に要るのは GND/TXD/RXD(/RTS/CTS)だけです。
2. **基板の DIP スイッチ SW2 を「3.3V」側（出荷時 OFF）にする。** Jetson の GPIO は 3.3V ロジックで、5V を入れると壊れます。
3. 配線は **両機の電源を切った状態** で行う。

## 2. Jetson 側の準備（初回のみ）

```bash
sudo apt install -y python3-serial            # または pip3 install pyserial
sudo usermod -aG dialout $USER                # 再ログインが必要
ls -l /dev/ttyTHS1                            # 存在確認
# シリアルコンソールが /dev/ttyTHS1 を掴んでいる場合 (Nano / Xavier 系)
sudo systemctl stop nvgetty && sudo systemctl disable nvgetty
```
- RTS/CTS (Pin 11/36) は機種・JetPack によっては UART に割り当てられていません。その場合 `sudo /opt/nvidia/jetson-io/jetson-io.py` で UART1 を有効化してください。**まずは RTS/CTS なしで動作確認**するのがおすすめです（本ツールは CRC+ACK+再送で信頼性を確保するため、フロー制御なしでも動作します）。

## 3. Windows 側の準備（初回のみ）
- AE-TTL-232R を USB で接続 → FTDI ドライバが自動導入され、デバイスマネージャーの「ポート(COM と LPT)」に `USB Serial Port (COMx)` が出ます。
- Python 3 と `pip install pyserial`。
- 高速化したい場合: そのポートのプロパティ → ポートの設定 → 詳細設定 → **Latency Timer を 1 ms** に変更（既定 16ms で ACK 待ちが遅くなります）。

## 4. 使い方

`serial_xfer.py` は両側で同じファイルです。**片側を `serve` で待ち受けにしておけば**、もう片側から送受信すべて行えます。

```bash
# Jetson（待ち受け。受信先 = ~/share。常駐させてOK）
python3 serial_xfer.py -p /dev/ttyTHS1 -b 115200 serve --root ~/share
```
```powershell
# Windows
python serial_xfer.py -p COM5 -b 115200 put  C:\data\model.bin C:\data\images   # Windows → Jetson（フォルダ可）
python serial_xfer.py -p COM5 -b 115200 put  report.pdf --dest incoming          # 相手 root 内の incoming/ へ
python serial_xfer.py -p COM5 -b 115200 ls                                       # Jetson 側一覧
python serial_xfer.py -p COM5 -b 115200 get  results\log.csv --outdir C:\recv    # Jetson → Windows
```
逆向きにしたい場合（Windows を `serve`、Jetson から `put` / `get`）も同じコマンドで可能です。

主なオプション: `-b` ボーレート / `--rtscts` ハードウェアフロー制御 / `--chunk` 1フレームのサイズ(既定2048) / `--timeout` / `--tries`

## 5. 動作確認の順番
1. 配線を確認し、まず `-b 115200`、RTS/CTS なしで `ls` が返るか確認。
2. 小さいファイルを `put` → Jetson 側で内容を確認。
3. `get` で戻して一致を確認。
4. 速度を上げる: `-b 460800` → `921600`（両側同じ値）。高速時は `--chunk 8192` と Latency Timer 1ms が効きます。
5. 最後に必要なら `--rtscts`（両側に付ける）。

### 困ったとき
| 症状 | 確認 |
|---|---|
| 「相手から応答がありません」 | TX/RX がクロスか、GND、ボーレート一致、Jetson 側で `serve` 起動中か、nvgetty 停止、ポート名 |
| 一部だけ失敗・遅い | ボーレートを下げる / 配線を短く / GND をしっかり / `--chunk 1024` |
| `--rtscts` で止まる | Pin 11/36 が UART 割り当てか確認（jetson-io）。外すだけでも動く |
| Permission denied | `dialout` グループ追加 → 再ログイン |
| 簡易ループバック試験 | Jetson の Pin8 と Pin10 を短絡し、`python3 -c "import serial;s=serial.Serial('/dev/ttyTHS1',115200,timeout=1);s.write(b'hi');print(s.read(2))"` |

## 6. 仕様メモ
- フレーム: `A5 5A | type | len | payload | CRC32`。壊れたフレームは破棄し、ACK タイムアウトで再送。
- 完了時に **SHA-256** で全体検証し、一致したときだけ最終ファイル名に確定（途中は `*.part`）。
- 中断しても再実行すれば `*.part` から再開。同一ファイルが既にあればスキップ。
- 受信先 root 外へのパス（`..` など）は拒否。同名の別内容ファイルは上書きされます。
- 検証状況: ビット化け・フレーム欠落・ゴミバイトを注入した擬似回線で `test_serial_xfer.py` が全て合格。**実機（FT232R/Jetson UART）での動作は未検証**です。
