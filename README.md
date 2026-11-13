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

パッケージ管理は [uv](https://docs.astral.sh/uv/) で行います（依存は pyserial のみ）。

```bash
sudo usermod -aG dialout $USER                # 再ログインが必要
ls -l /dev/ttyTHS1                            # 存在確認
# シリアルコンソールが /dev/ttyTHS1 を掴んでいる場合 (Nano / Xavier 系)
sudo systemctl stop nvgetty && sudo systemctl disable nvgetty

cd serial-data-transfer
uv sync                                       # .venv を作って依存を入れる
uv run serial-xfer -h                         # 動作確認
```
- RTS/CTS (Pin 11/36) は機種・JetPack によっては UART に割り当てられていません。その場合 `sudo /opt/nvidia/jetson-io/jetson-io.py` で UART1 を有効化してください。**まずは RTS/CTS なしで動作確認**するのがおすすめです（本ツールは CRC+ACK+再送で信頼性を確保するため、フロー制御なしでも動作します）。
- テスト（実機不要）: `uv run pytest`（約 4 分）、`uv run pytest -m "not slow"`（約 45 秒）

## 3. Windows 側の準備（初回のみ）
- AE-TTL-232R を USB で接続 → FTDI ドライバが自動導入され、デバイスマネージャーの「ポート(COM と LPT)」に `USB Serial Port (COMx)` が出ます。
- uv を入れる（PowerShell）: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
  （または `winget install --id=astral-sh.uv -e`）。Python は uv が自動で用意します。
- 高速化したい場合: そのポートのプロパティ → ポートの設定 → 詳細設定 → **Latency Timer を 1 ms** に変更（既定 16ms で ACK 待ちが遅くなります）。

### Windows へのインストール（どれか1つ）
Jetson 側で配布ファイルを作ります。`dist/` に wheel と zip ができます。
```bash
uv build                                                      # dist/serial_data_transfer-0.1.0-py3-none-any.whl
git archive --format=zip --prefix=serial-data-transfer/ -o dist/serial-data-transfer.zip HEAD
```

| 方法 | Windows でやること | 使い方 |
|---|---|---|
| **A. wheel（おすすめ）** | `.whl` をコピーして `uv tool install .\serial_data_transfer-0.1.0-py3-none-any.whl` | どこからでも `serial-xfer ...` |
| B. zip を展開 | zip をコピー → 展開 → `cd serial-data-transfer` → `uv sync` | フォルダ内で `uv run serial-xfer ...` |
| C. git | `uv tool install git+https://github.com/git-756/serial-data-transfer`（private なので下記の認証が先に必要） | どこからでも `serial-xfer ...` |

更新したときは、A なら新しい wheel で `uv tool install --force ...`、B なら展開し直して `uv sync`、C なら `uv tool install --force git+https://...`（または `uv tool upgrade serial-data-transfer`）。

C の認証（Windows で初回のみ）: リポジトリは private なので、[Git for Windows](https://git-scm.com/download/win) を入れて GitHub にログインしておきます。
どちらか一方で構いません。
- `git clone https://github.com/git-756/serial-data-transfer` を一度実行し、ブラウザで出るログインを済ませる（Git Credential Manager が認証を保存します）
- `winget install GitHub.cli` → `gh auth login` → `gh auth setup-git`

SSH 鍵を GitHub に登録済みなら `git+ssh://git@github.com/git-756/serial-data-transfer.git` でも入ります。
uv が使えない環境では、`src/serial_data_transfer/serial_xfer.py` 1 ファイルと `pip install pyserial` だけでも `python serial_xfer.py ...` で動きます。

## 4. 使い方

両側で同じツールを使います。**片側を `serve` で待ち受けにしておけば**、もう片側から送受信すべて行えます。
共通オプション（`-p` `-b` `--rtscts` `--chunk` など）はサブコマンドの前後どちらに書いても構いません。両側で同じ `-b` / `--rtscts` にしてください。

```bash
# Jetson（待ち受け。受信先 = ~/share。常駐させてOK）
uv run serial-xfer serve -p /dev/ttyTHS1 -b 115200 --root ~/share
```
```powershell
# Windows
serial-xfer put -p COM5 -b 115200 C:\data\model.bin C:\data\images   # Windows → Jetson（フォルダ可）
serial-xfer put -p COM5 -b 115200 report.pdf --dest incoming          # 相手 root 内の incoming/ へ
serial-xfer ls  -p COM5 -b 115200                                     # Jetson 側一覧
serial-xfer get -p COM5 -b 115200 results\log.csv --outdir C:\recv    # Jetson → Windows
```
最速設定は `-b 3000000 --chunk 59000 --rtscts`（両側に付ける）。
逆向きにしたい場合（Windows を `serve`、Jetson から `put` / `get`）も同じコマンドで可能です。

主なオプション: `-b` ボーレート / `--rtscts` ハードウェアフロー制御 / `--chunk` 1フレームのサイズ(既定2048) / `--timeout` / `--tries`

## 5. 動作確認の順番
1. 配線を確認し、まず `-b 115200`、RTS/CTS なしで `ls` が返るか確認。
2. 小さいファイルを `put` → Jetson 側で内容を確認。
3. `get` で戻して一致を確認。
4. 速度を上げる: `-b 460800` → `921600` → `2000000` → `3000000`（両側同じ値）。高速時は `--chunk` を大きく（最大 59000 程度）し、Latency Timer を 1ms にします。
5. 1 Mbps を超えるなら `--rtscts`（両側に付ける）。Jetson の UART は受信 FIFO があふれやすく（overrun）、フロー制御なしだと再送が増えて Windows → Jetson 方向が遅くなります。

### 実測値（Jetson AGX Orin ⇔ FT232R、Jetson 自身の USB に挿して測定、4 MiB）
| 設定 | put (→Jetson) | get (Jetson→) |
|---|---|---|
| `-b 921600 --chunk 8192` | 75 KiB/s | 75 KiB/s |
| `-b 3000000 --chunk 59000`（RTS/CTS なし） | 189 KiB/s | 265 KiB/s |
| `-b 3000000 --chunk 59000 --rtscts` | **263 KiB/s** | **265 KiB/s** |

3 Mbps は FT232R の上限で、理論値 293 KiB/s の約 90% が出ています。

### 困ったとき
| 症状 | 確認 |
|---|---|
| 「相手から応答がありません」 | TX/RX がクロスか、GND、ボーレート一致、Jetson 側で `serve` 起動中か、nvgetty 停止、ポート名 |
| 一部だけ失敗・遅い | ボーレートを下げる / 配線を短く / GND をしっかり / `--chunk 1024` |
| `--rtscts` で止まる | Pin 11/36 が UART 割り当てか確認（jetson-io）。外すだけでも動く |
| Permission denied | `dialout` グループ追加 → 再ログイン |
| 簡易ループバック試験 | Jetson の Pin8 と Pin10 を短絡し、`uv run serial-loopback -p /dev/ttyTHS1 -b 115200 921600` |

## 6. 仕様メモ
- フレーム: `A5 5A | type | len | payload | CRC32`。壊れたフレームは破棄し、ACK タイムアウトで再送。
- 完了時に **SHA-256** で全体検証し、一致したときだけ最終ファイル名に確定（途中は `*.part`）。
- 中断しても再実行すれば `*.part` から再開。同一ファイルが既にあればスキップ。
- 受信先 root 外へのパス（`..` など）は拒否。同名の別内容ファイルは上書きされます。
- 検証状況: ビット化け・フレーム欠落・ゴミバイトを注入した擬似回線で `tests/test_serial_xfer.py` が全て合格。実機は Jetson AGX Orin ⇔ FT232R（Jetson 自身の USB に挿して接続）で 3 Mbps まで確認済み。**Windows PC との接続はまだ確認していません**。
