# Windows ⇔ Jetson シリアルファイル転送 (AE-TTL-232R)

## かんたん操作（おすすめ）

配線（下の「1. 配線」）が済んでいれば、次の2つで「**Jetson は常に受信待ち、Windows はアイコンから画面で送受信**」になります。

### Jetson：常に待ち受けにする（初回のみ）
```bash
cd ~/756_project/serial-data-transfer
./deploy/install-jetson-service.sh
```
- sudo のパスワードを聞かれます。以後は Jetson の起動時に自動で `serve` が動きます（最速設定、受信先は `~/share`）。
- 設定を変えるとき: `ROOT=~/data BAUD=921600 RTSCTS=0 ./deploy/install-jetson-service.sh`（もう一度実行すると上書き）
- ログ: `journalctl -u serial-xfer -f` / 停止: `sudo systemctl stop serial-xfer` / 削除: `./deploy/install-jetson-service.sh uninstall`
- サービスが動いている間はポートを使っているので、Jetson 側から `put` / `get` するときは先に停止してください。

### Windows：セットアップ（初回のみ。更新するときも同じ）
1. [windows/setup.bat](windows/setup.bat) を保存します（GitHub のファイル画面の「Download raw file」ボタン）。
2. 保存した `setup.bat` をダブルクリックします。次の処理を自動で行います（GitHub のログイン不要）。
   uv のインストール → ツールのインストール/更新 → ショートカット作成 → 転送画面の起動
   - 「Windows によって PC が保護されました」と出たら「詳細情報」→「実行」。
   - **会社のプロキシ**: 通常は Windows のプロキシ設定（ブラウザと同じ）を自動で使います。
     それで「[2/3]」で止まる場合は、`setup.bat` と同じフォルダに `proxy.txt` を作り、
     プロキシのアドレスを書いてください（例: `http://proxy.example.com:8080`）。
     PowerShell 用の `$env:HTTPS_PROXY = "..."` やコマンドプロンプト用の `set HTTPS_PROXY=...` をそのまま貼っても、
     中の `http://ホスト:ポート` だけを取り出して使います。
     コマンドプロンプトから `setup.bat http://proxy.example.com:8080` のように渡すこともできます。
     `proxy.txt` は自分の PC に置くだけにして、リポジトリには入れないでください（`.gitignore` 済み）。
3. 以後は、デスクトップの「**Jetson ファイル転送**」をダブルクリックするだけです。

### Windows：転送画面の使い方
- **送信**: 「ファイルを送信…」「フォルダを送信…」で、エクスプローラーの画面から選びます。一覧で開いている Jetson 側のフォルダに送られます。
- エクスプローラーでファイルを右クリック →「送る」→「**Jetson へ送る**」でも送れます（確認のあと送信）。デスクトップのアイコンにドラッグ&ドロップしても同じです。
- **受信**: 一覧で選び（Ctrl / Shift で複数選択。フォルダも可）、「選択したものを受信…」→ 保存先を選びます。
- ポートは FTDI（AE-TTL-232R）を自動で選びます。速度と RTS/CTS は Jetson 側のサービスと同じ値にしてください（既定はどちらも 3000000 / RTS/CTS あり）。設定は自動で保存されます。
- 転送中は「中止」で止められます。途中まで送ったファイルは、次に同じファイルを送ると続きから再開します。

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
リポジトリは public なので、**どの方法も GitHub のログインは不要**です。

| 方法 | Windows でやること | 使い方 | 更新 |
|---|---|---|---|
| **A. ZIP の URL から（おすすめ、Git 不要）** | `uv tool install https://github.com/git-756/serial-data-transfer/archive/refs/heads/main.zip` | どこからでも `serial-xfer ...` | 同じコマンドに `--force` |
| B. git から | [Git for Windows](https://git-scm.com/download/win) を入れて、`uv tool install git+https://github.com/git-756/serial-data-transfer` | どこからでも `serial-xfer ...` | `uv tool upgrade serial-data-transfer` |
| C. clone / ダウンロード | `git clone https://github.com/git-756/serial-data-transfer.git`（または GitHub の「Code」→「Download ZIP」で落として展開）→ `cd serial-data-transfer` → `uv sync` | フォルダ内で `uv run serial-xfer ...` | `git pull`（ZIP なら落とし直し）→ `uv sync` |
| D. Jetson から持っていく | 下の手順で作った `.whl` をコピーして `uv tool install .\serial_data_transfer-0.1.0-py3-none-any.whl` | どこからでも `serial-xfer ...` | 新しい wheel で `--force` |

D の配布ファイルは Jetson 側で作ります（`dist/` に wheel と zip ができます）。
```bash
uv build                                                      # dist/serial_data_transfer-0.1.0-py3-none-any.whl
git archive --format=zip --prefix=serial-data-transfer/ -o dist/serial-data-transfer.zip HEAD
```

**ログインを求められたら**: URL のつづりを確認してください（`transfer`。存在しない URL だと GitHub はログインを求めます）。
会社のネットワークでは、uv のインストールと pyserial の取得に astral.sh / github.com / pypi.org への通信が必要です。
プロキシ経由の場合は、PowerShell で `$env:HTTPS_PROXY = "http://<プロキシ>:<ポート>"` を設定してから実行します（`setup.bat` はプロキシを自動で設定します）。
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
