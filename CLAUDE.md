# プロジェクト: Windows ⇔ Jetson シリアルファイル転送

## 目的
AE-TTL-232R（FT232RQ, USB-microB〜USB-A）でWindows PCとJetsonのGPIO UART(/dev/ttyTHS1)を接続し、
双方向にファイルをやり取りする。

## ファイル（uv 管理。`uv sync` → `uv run serial-xfer ...`）
- `src/serial_data_transfer/serial_xfer.py` : 転送ツール本体（Windows/Jetson共通、依存は pyserial のみ）。コマンド `serial-xfer`。サブコマンド serve / put / get / ls。単体ファイルとしても動く
- `src/serial_data_transfer/gui.py` : Windows 向けの転送画面（tkinter）。コマンド `serial-xfer-gui`（gui-scripts なのでコンソールなし）。
  送信、Jetson 側一覧の閲覧と受信、起動引数のファイルを送信（「送る」/ドロップ）、`--create-shortcuts`
- `src/serial_data_transfer/loopback.py` : Pin8-Pin10 短絡ループバック試験。コマンド `serial-loopback`
- `windows/setup.bat` : Windows のワンクリックセットアップ（uv → ZIP URL から tool install → ショートカット作成 → GUI 起動）。CRLF
- `deploy/install-jetson-service.sh` : Jetson で serve を systemd サービス化（`serial-xfer.service`、要 sudo）
- `tests/test_serial_xfer.py` : ノイズ注入した擬似回線での自動テスト（実機不要）。`uv run pytest`（`-m "not slow"` で短縮）
- `tests/test_gui_client.py` : GUI の転送ロジック（Client）のテスト（画面なし）
- `README.md` : 配線・セットアップ・使い方・Windows への配布方法（wheel / zip / git）

## 配線（確定事項）
AE-TTL-232R TXD→Jetson Pin10(RXD) / RXD→Pin8(TXD) / RTS→Pin36(CTS) / CTS→Pin11(RTS) / GND→Pin6。
**+5V端子(3番)はJetsonに接続しない**（USB 5Vが常時出力されるため電源が並列になる）。
**SW2は3.3V側**（Jetson GPIOは3.3Vロジック）。

## プロトコル要点
フレーム `A5 5A | type | len(2) | payload | CRC32`。ストップ&ウェイトでACK/再送。
START(サイズ+SHA-256+名前) → DATA(offset付き) → END で、SHA-256一致時のみ `.part` を本名にrename。
`.part` からの再開、同一ファイルのスキップ、root外パス(`..`)の拒否あり。
GET要求のACKが落ちた場合は START受信を暗黙ACKとして扱う（修正済み）。

## 現状（2026-11-13 時点のまとめ）
- **Windows PC ⇔ Jetson の実機で、双方向の送受信を確認済み**。Windows 側は `setup.bat` → GUI（`serial-xfer-gui`）で使っている。
- 最速設定は `-b 3000000 --chunk 59000 --rtscts`。
  Windows→Jetson は 10 MB / 38 秒（約 270 KiB/s）、Jetson→Windows は 10 MB / 49.6 秒（約 206 KiB/s）。どちらもエラーなし。
- 配布: GitHub の public リポジトリ（MIT）。ZIP の URL から `uv tool install` する（Git / ログイン不要）。プロキシは setup.bat が自動で設定する。
- 擬似回線テスト（`uv run pytest`）はすべて合格。
- 実機は Jetson AGX Orin Dev Kit（R39.2.1）。`/dev/ttyTHS1` = 3100000.serial（UART1、header Pin8/10 = PR.02/PR.03）。
- 経緯の詳細は `docs/development-history.md`。

## 経緯（日付順。古い記述は消さずに残し、後で分かったことを「→」で追記する）
- 2026-11-09（Jetson での作業開始時）: 擬似回線テストは全て合格（フレーム約25%破損/欠落でも完走）。
  **実機（FT232R ⇔ Jetson UART）では未検証。**
  → 2026-11-12 に実機で検証済み。
- 2026-11-10: dialout 追加済み、ポートは開ける。`loopback_test.py`（Pin8-Pin10 短絡）は 115200/460800/921600 とも **受信 0 バイト**。
  送信は完了扱いになるため、短絡位置か pinmux（GPIO のままで UART になっていない）が疑わしい。原因はまだ特定できていない。
  → 2026-11-12 に、原因は短絡側（物理）と判明。pinmux は正常だった。
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
    → `--rtscts` は同日、Windows PC との接続は 2026-11-13 に確認済み。
- 2026-11-12: 最高速度を探索（同じく Jetson 自身の USB に FT232R を挿して測定）。
  - 生データ 256KiB の連続送信: 3 Mbps（FT232R の上限）までビット誤り（frame/parity）は 0。
    ただし FT232R → Jetson 方向で ttyTHS1 の **受信 overrun** がときどき起きる（TIOCGICOUNT で確認）。
  - RTS/CTS の配線: Jetson RTS(Pin11) → FT232R CTS は動作する。FT232R RTS → Jetson CTS(Pin36) は常に「アサート」と読める
    （未接続か、検出できていない。未確認）。Jetson → FT232R 方向は overrun が出ないので、実用上は問題ない。
  - `--rtscts` を付けると、4 MiB×4 回で overrun 0（付けないと同じ量で 462 回）。
  - **最速設定: `-b 3000000 --chunk 59000 --rtscts` で put 263 / get 265 KiB/s**（理論値 293 の約 90%）。RTS/CTS なしだと put 189 KiB/s。
  - 残りの約 10% は、フレームごとのストップ&ウェイト待ち（約 20ms、FTDI の latency_timer 16ms が主）。
    latency_timer=1 やスライディングウィンドウ化でもう少し伸びる見込み（未検証）。
  - Windows PC での確認はまだしていない。
    → 2026-11-13 に確認済み。
- 2026-11-13: uv でパッケージ化（src レイアウト、`serial-xfer` / `serial-loopback`）。GitHub に公開（private → public）、MIT ライセンスを追加。
  Windows へは ZIP の URL から `uv tool install` する方法を推奨にした（Git / ログイン不要）。
- 2026-11-13: **Windows PC ⇔ Jetson の実機で確認**（Windows PC に ZIP の URL から uv tool install）。
  - Windows → Jetson（put）: 10 MB の bin を 38 秒で送信できた。約 270 KiB/s で、Jetson どうしの最速値（263 KiB/s）と同等。
    設定は README の最速設定（3 Mbps / chunk 59000 / RTS/CTS）と想定しているが、確認はしていない。
  - Jetson → Windows: 未確認。
    → 同日に確認済み（次の項目）。
- 2026-11-13: 送受信を簡単にするため、Jetson 常駐サービスと Windows 用 GUI を追加。
  - Jetson→Windows（get）は 10 MB / 49.6 秒（約 206 KiB/s）でエラーはなし。Windows→Jetson より遅いが、ユーザーは速度より確実性を重視するので対応は保留。
    原因は Pin36（FT RTS→Jetson CTS）が効かないことによる再送と推測しているが、未確認。
  - GUI は Jetson 上で socat の仮想シリアルを使い、本物の serve 相手に一覧・送信・フォルダ移動・受信を確認（ファイル一致）。**Windows 実機ではまだ動かしていない**。
    → 同日、Windows 実機での送受信をユーザーが確認済み。
  - サービス用スクリプトは unit の生成と `systemd-analyze verify` までは確認済み。実際のインストール（sudo）はまだ。
    → インストールと、Jetson 再起動後の自動起動はまだ確認していない。
  - コア側の変更: Sender/Receiver に progress コールバックを追加。コンソールでないとき（pythonw、systemd）は進捗を出さない（`_console()`）。
- 2026-11-13: GitHub の raw ダウンロードで setup.bat の改行が LF になっていた → `*.bat -text` にしてリポジトリにも CRLF で保存。
- 2026-11-13: プロキシ環境で setup.bat が [2/3]（tool install）で止まった → setup.bat がプロキシを自動で設定するようにした
  （引数 → HTTPS_PROXY → `proxy.txt` → Windows のプロキシ設定）。`proxy.txt` に PowerShell の行を書くと失敗したため、
  ファイルから `http(s)://ホスト:ポート` だけを取り出すように修正。
- 2026-11-13: **Windows の setup.bat（プロキシの自動設定込み）→ GUI での送受信をユーザーが確認**。開発の経緯は `docs/development-history.md` にまとめた。

## 次にやること（完了したものも消さずに残す）
1. ~~実機でループバック試験（Pin8-Pin10短絡）→ AE-TTL-232R接続で `ls` 疎通確認（115200, RTS/CTSなし）~~
   → 完了（2026-11-12）。ループバックは短絡不良で失敗したが、FT232R を Jetson の USB に挿す方法で疎通を確認。
2. ~~put/get の往復でファイル一致確認 → ボーレートを 460800 / 921600 と上げて実測速度を記録~~
   → 完了（2026-11-12）。3 Mbps まで記録済み。
3. ~~`--rtscts` の可否確認（Pin11/36のpinmux。必要なら jetson-io で UART1 を有効化）~~
   → 完了（2026-11-12）。Pin11→FT CTS は動作、Pin36 は効いていない（下の 6）。
4. 高速時のスループット改善が必要ならスライディングウィンドウ化を検討（現状はストップ&ウェイト）
   → 保留。ユーザーは速度より確実性を重視。
5. ~~Jetson側 serve を systemd で常駐化（任意）~~
   → スクリプト作成済み（2026-11-13、`deploy/install-jetson-service.sh`）。インストール後の動作は下の 7。
6. Pin36（FT232R RTS → Jetson CTS）が効かない原因の確認（配線の可能性）。直れば Jetson→Windows の再送も減る見込み。
7. Jetson 側サービスをインストールし、Jetson 再起動後に自動で起動して Windows の GUI から接続できるか確認。
8. （任意）Jetson→Windows（get）の速度改善。再送回数の表示、get の送信側に serve の `--timeout` を反映する、など。

## 注意
- Jetsonで /dev/ttyTHS1 を使う前に nvgetty の停止と dialout グループ追加が必要な場合あり（README参照）
- 実機の結果（速度・エラー・pinmux）が分かったらこのファイルの「現状」を更新すること。
  古い記述は消さずに「経緯」に残し、後で分かったことを「→」で追記する。
- リポジトリは public。プロキシのアドレスなど、組織内の情報は書かない（`proxy.txt` は `.gitignore` 済み）。
- Jetson 側でサービスが動いている間はポートを使っているので、Jetson から put/get するときは `sudo systemctl stop serial-xfer`。
