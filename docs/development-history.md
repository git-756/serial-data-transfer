# 開発の経緯とトラブルシュート記録

Windows PC と Jetson を AE-TTL-232R（FT232R）でつなぎ、UART でファイルをやり取りするツールを、実機で動くまで仕上げた記録です。
期間は 2026-11-09 〜 2026-11-13 です。

## 最終的な構成

```
[Windows PC]                                    [Jetson AGX Orin]
 デスクトップの「Jetson ファイル転送」             serial-xfer serve（systemd で常駐）
 （serial-xfer-gui）                                受信先 ~/share
   │ USB                                              │
 AE-TTL-232R (FT232R) ── TXD/RXD/RTS/CTS/GND ── 40pin ヘッダ /dev/ttyTHS1
```

| 項目 | 内容 |
|---|---|
| 通信 | 3 Mbps、RTS/CTS あり、1 フレーム 59000 バイト、CRC32 + ACK/再送、SHA-256 で最終確認 |
| 速度 | Windows→Jetson: 10 MB / 38 秒（約 270 KiB/s）。Jetson→Windows: 10 MB / 49.6 秒（約 206 KiB/s） |
| Jetson 側 | `deploy/install-jetson-service.sh` で `serve` を常駐 |
| Windows 側 | `windows/setup.bat` をダブルクリック → uv・ツール・ショートカットを自動で用意 → 画面から送受信 |
| 配布 | GitHub の public リポジトリ（MIT）。ZIP の URL から `uv tool install` するので、Git も GitHub のログインも不要 |

---

## 経緯（時系列）

### 1. 続きを始めようとしたらコードがなかった
- **状況**: CLAUDE.md には「`serial_xfer.py` などは作成済み、擬似回線テストは合格」とあった。しかし Jetson のリポジトリには `uv init` の雛形しかなかった。
- **原因**: 前回の作業は別の環境（Windows 側）で行っていた。
- **対応**: Windows 側からファイルをコピーした。新しく作り直さなかったのは、両側で同じコードを使わないとプロトコルが一致しないため。
- **結果**: Jetson 上でも擬似回線テストがすべて合格した。

### 2. `/dev/ttyTHS1` を開けない（Permission denied）
- **原因**: ユーザーが `dialout` グループに入っていなかった。
- **対応**: `sudo usermod -aG dialout $USER` を実行し、**ログインし直した**。
- **注意点**: 1 回目は `getent group dialout` の結果が空で、設定が反映されていなかった。また、グループの変更は**ログインし直すまで、すでに起動しているアプリには反映されない**。

### 3. ループバック試験で受信が 0 バイト
- **状況**: Pin8（TXD）と Pin10（RXD）を短絡して試験したところ、115200 / 460800 / 921600 のどの速度でも受信が 0 バイトだった。送信の処理は正常に終わっていた。
- **切り分け**:
  1. デバイスツリーを確認した。`/dev/ttyTHS1` は `3100000.serial`（UART1）で、ヘッダの Pin8/10 は PR.02/PR.03 に対応しており、使うポートは正しかった。
  2. `pinmux-pins` を見ると `MUX UNCLAIMED` と表示された。ただし Jetson ではブートローダがピンの機能を設定するので、**この表示は正常な場合でも出るため、判断材料にならない**。
  3. `pinconf-groups` を確認した。TX は `tristate=0`（出力が有効）、RX は `enable-input=1`（入力が有効）とプルアップで、UART として正しい設定だった。
  4. jetson-io でも Pin8/10 が UART1 に割り当てられていた。
  5. ここまででソフト側の原因はなくなったので、物理側を疑った。**AE-TTL-232R を Jetson 自身の USB に挿し**、`ttyTHS1` ⇔ `ttyUSB0` で直接通信して試した。
- **結論**: 3 速度とも、双方向でエラー 0 だった。ループバック試験の失敗は、短絡の位置か接触の問題だった。
- **学び**: AE-TTL-232R を Jetson に挿して試すと、Windows を使わずに Jetson 1 台で実機の検証を完結できる。その後の速度測定も、この方法で行った。

### 4. README どおりのコマンドが必ずエラーになる
- **状況**: `serial_xfer.py -p COM5 -b 115200 put ...` を実行すると「-p が必要です」というエラーになった。
- **原因**: argparse で、メインのパーサーと各サブコマンドの両方に `-p` を必須で定義していた。
- **対応**: サブコマンド側の既定値を `SUPPRESS`（未指定なら値を設定しない）にし、`-p` が指定されているかは最後にまとめて確認するようにした。これで、共通オプションをサブコマンドの前にも後ろにも書けるようになった。

### 5. 最高速度を探して RTS/CTS が必要と分かった
- **方法**: 生データを 256 KiB 連続で送り、`TIOCGICOUNT`（カーネルの送受信エラー件数を取得する ioctl）で frame / parity / overrun の件数を数えた。
- **分かったこと**:
  - 3 Mbps（FT232R の上限）でも、ビット化け（frame/parity）は 0 だった。線の品質は問題ない。
  - FT232R→Jetson の向きでは、Jetson の受信 FIFO が**あふれる（overrun）**ことがあった。あふれたフレームは再送になるので、その向きの `put` だけが遅かった（189 KiB/s）。
  - `--rtscts` を付けると、overrun は 0 になった。put も get も **263 / 265 KiB/s** になり、理論値の約 90% が出た。
- **配線の確認**: RTS を切り替えながら、相手側の CTS の読み値を見た。
  - Jetson RTS（Pin11）→ FT232R CTS: 正しく連動した。overrun 対策に必要なのはこちら。
  - FT232R RTS → Jetson CTS（Pin36）: 常に「アサート」と読めた。未接続の可能性があり、原因は分かっていない。

| 設定（4 MiB） | put（→Jetson） | get（Jetson→） |
|---|---|---|
| 921600, chunk 8192 | 75 KiB/s | 75 KiB/s |
| 3000000, chunk 59000, RTS/CTS なし | 189 KiB/s | 265 KiB/s |
| **3000000, chunk 59000, `--rtscts`** | **263 KiB/s** | **265 KiB/s** |

### 6. uv でパッケージ化し、Windows に配れるようにした
- コードを `src/serial_data_transfer/` に移し、`serial-xfer` と `serial-loopback` のコマンドを用意した。依存（pyserial）と `uv.lock` で管理する。
- テストを `tests/` に移し、pytest で実行できるようにした（15% ノイズの長いテストは `slow` マーカーで除外できる）。
- Windows への配り方として、wheel をコピーする方法と、ZIP を展開して `uv sync` する方法を用意した。どちらも Jetson 上で、インストールして動くところまで確認した。

### 7. GitHub で公開した
- 最初は private リポジトリにした。その後ユーザーが public に変更した。
- **public にして問題ないかの確認**: 全コミットの履歴を検索した。パスワード、キー、IP アドレス、個人のメールアドレスは含まれていなかった（コミットの作者は noreply アドレス）。
- **LICENSE**: MIT（Copyright © 2026 samurai-human-go!）を追加した。

### 8. Windows でインストールしようとしたらログインを求められた
- **原因の見当**: 存在しない URL（例: `transfar` のようなつづり違い）にアクセスすると、GitHub はログインを求める。正しい URL なら、ログインなしで clone できることを確認した。
- **Git やログインが使えない PC 向けの対応**: Git がなくても入れられるように、**ZIP の URL から直接インストール**する方法を推奨にした。
  ```
  uv tool install https://github.com/git-756/serial-data-transfer/archive/refs/heads/main.zip
  ```

### 9. Windows 実機での確認（CLI）
- Windows→Jetson: 10 MB を 38 秒（約 270 KiB/s）で送れた。Jetson どうしで測った最速値と同等。
- Jetson→Windows: 10 MB で 49.6 秒（約 206 KiB/s）だった。エラーはないが、やや遅い。
  - 推測している原因: Pin36 が効いておらず Windows 側の受信があふれることによる再送と、get の送信側の再送待ちが 2 秒で固定されていること。
  - ユーザーの判断で「エラーなく送れれば速度は問題ない」とし、対応は保留した。

### 10. 送受信を簡単にした（常駐サービスと GUI）
- **Jetson**: `deploy/install-jetson-service.sh` で `serve` を systemd サービスにした。起動時に自動で始まり、落ちても自動で再起動する。生成した unit は `systemd-analyze verify` で確認した。
- **Windows**: `serial-xfer-gui`（tkinter で作ったので追加の依存なし）。
  - エクスプローラーのダイアログでファイル/フォルダを選んで送信できる。Jetson 側の一覧を見ながら、選んだものを受信できる。
  - 右クリックの「送る」→「Jetson へ送る」や、アイコンへのドラッグ&ドロップでも送れる。進捗バー、中止（中止後は続きから再開できる）、設定の自動保存がある。
- **`windows/setup.bat`**: ダブルクリックで、uv → ツール → ショートカット作成 → GUI 起動までを行う。
- **コア側の変更**: 進捗のコールバックを追加した。コンソールがない環境（pythonw や systemd）では進捗を出さないようにした（`sys.stdout` が None でも落ちない）。
- **検証**: socat で仮想のシリアルポートの組を作り、本物の `serve` を相手に GUI を操作した（一覧・送信・フォルダ移動・受信）。

### 11. GitHub から落とした setup.bat の改行が LF になっていた
- **原因**: `.gitattributes` の `eol=crlf` は、チェックアウトするときに変換する設定だった。そのため、リポジトリ内のファイル（＝ GitHub からそのままダウンロードされる内容）は LF のままだった。改行が LF の bat では、cmd が `goto` の移動先を見失うことがある。
- **対応**: `*.bat -text` にして、リポジトリにも CRLF のまま保存するようにした。

### 12. プロキシで setup.bat が [2/3] で止まる
- **状況**: PowerShell で `$env:HTTP_PROXY` / `$env:HTTPS_PROXY` を設定してから bat を実行すると成功した。ダブルクリックだけでは止まった。
- **原因**: uv はプロキシを環境変数からしか読まない。Windows のプロキシ設定は自動では使われない。
- **対応 1**: bat が次の順でプロキシを探して設定するようにした。
  1. 引数
  2. すでに設定済みの `HTTPS_PROXY`
  3. 同じフォルダの `proxy.txt`
  4. Windows のプロキシ設定（`GetSystemWebProxy`。PAC による自動設定も含む）
- **2 回目の失敗**: `proxy.txt` に PowerShell の 2 行をそのまま書いたところ、まだ失敗した。bat は proxy.txt を**コマンドとして実行するのではなく、URL が書かれたファイルとして読む**ため、`$env:... = "..."` という行がそのままプロキシのアドレスとして使われていた。
  - 参考: 環境変数の設定方法は、PowerShell では `$env:NAME = "値"`、cmd では `set NAME=値`。
- **対応 2**: ファイルの中から、最初の `http(s)://ホスト:ポート` だけを正規表現で取り出すようにした。URL だけ、PowerShell の行、cmd の set 行のどれでも使える。値が `http` で始まらない場合は警告を出す。
- **結果**: [2/3] を通過し、Windows の GUI で送受信できることを確認した。

### 13. プロキシアドレスを公開しないための対策
- プロキシのアドレスは、リポジトリに一切書かない（bat に直接書かず、`proxy.txt` か Windows の設定から読む）。
- `proxy.txt` は `.gitignore` に入れた。
- push のたびに、全コミットにプロキシのアドレスが含まれていないかを検索して確認した（結果はすべて 0 件）。

---

## 学び・チェックリスト

- **Jetson の UART が使えないとき**: `dialout` グループに入っているか（ログインし直したか）→ nvgetty が止まっているか → pinconf → 物理的な配線、の順に確認する。`MUX UNCLAIMED` という表示だけで判断しない。
- **実機の検証**: FT232R を Jetson の USB に挿すと、Windows を使わずに `ttyTHS1` ⇔ `ttyUSB0` で両方向を試せる。
- **高速化**: 1 Mbps を超えるなら `--rtscts` を使う。原因が overrun か線の品質かは、`TIOCGICOUNT` で区別できる。
- **Windows で配る bat**: リポジトリにも CRLF で保存する（`*.bat -text`）。
- **プロキシ**: uv は環境変数しか見ない。プロキシの情報は、公開リポジトリに入れずローカルのファイルから読む。
- **GitHub がログインを求めてきたら**: まず URL のつづりを疑う。

## 残っている課題

- Pin36（FT232R RTS → Jetson CTS）が効かない原因の確認。配線の可能性がある。
- Jetson→Windows が Windows→Jetson より約 3 割遅い件（エラーはないため保留中）。
- Jetson 側のサービスが、Jetson を再起動したあとも自動で起動して接続できるかの確認。

## コミット履歴

| コミット | 日付 | 内容 |
|---|---|---|
| 7dad120 | 11-12 | 転送ツールを追加（CLI の修正を含む） |
| 8596bc0 | 11-12 | 最高速度の実測結果と RTS/CTS の推奨を追記 |
| 5d3c3e0 | 11-13 | uv でパッケージ化し、Windows に配れる形に整理 |
| ac6c5b0 | 11-13 | private リポジトリからのインストール手順 |
| fac4544 | 11-13 | public リポジトリ向けに手順を更新 |
| 3a36abf | 11-13 | MIT ライセンスを追加 |
| 7bc6782 | 11-13 | ログイン不要な ZIP の URL からのインストール |
| df79772 | 11-13 | Windows 用 GUI と Jetson 常駐サービス |
| 0395956 | 11-13 | setup.bat をリポジトリでも CRLF で保存 |
| 37239a8 | 11-13 | setup.bat がプロキシを自動で設定 |
| b86da1a | 11-13 | proxy.txt から URL 部分だけを取り出す |
