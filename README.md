# Room Indicator

Windows上の指定ウィンドウまたは画面全体を、同じプライベートLANにいる登録済みスマートフォンへHTTPS配信します。PC管理画面はローカル接続だけに限定し、スマートフォンは独自CA証明書とQRコードによる端末登録を使用します。

## 対応環境

- Windows 10 / 11（64ビット）
- Python 3.12（64ビット、配布ZIPに公式インストーラーを同梱）
- PCとスマートフォンが同じ家庭内・社内のプライベートLANに接続されていること
- ゲストWi-Fiの端末間通信禁止やAP isolationが無効であること

公共Wi-Fiでは使用しないでください。画面配信を実行するため、Windowsへ通常ログインした状態で起動する必要があります。

## 配布ZIPを受け取ったPC

1. ZIPを、デスクトップやドキュメントなど書き込み可能な場所へ展開します。
2. `setup.bat` をダブルクリックします。
3. Python 3.12（64ビット）が未導入の場合は確認画面が表示されるので、`Y`を押します。
4. Pythonのインストール画面でインストールを実行します。既に入っている別バージョンのPythonは削除されません。
5. Room Indicatorの準備が完了したら、`run_test.bat` をダブルクリックします。
6. PC管理画面 <http://localhost:5000/> が自動的に開きます。

配布ZIPにはPython 3.12.10の公式64ビットインストーラーと `wheelhouse/` が含まれるため、受取側PCはインターネットを使わず準備できます。`setup.bat` はインストーラーのSHA-256とPython Software Foundationの電子署名を検証してから起動します。Python本体のライセンスは `PYTHON-LICENSE.txt` に同梱しています。

## スマートフォンの初回登録

1. PC管理画面の「スマホのHTTPS初期設定」に表示されるQRコードを読み取ります。
2. スマートフォン画面とPC管理画面に表示された証明書のSHA-256指紋が完全に一致することを確認します。
3. 画面の案内に従い、Room Indicator用のローカルCA証明書を導入します。
4. iPhone / iPadでは「設定」→「一般」→「情報」→「証明書信頼設定」から `Room Indicator Local CA` を有効にします。
5. PC管理画面で「端末を追加」を押し、新しく表示されたQRコードを読み取ります。
6. スマートフォンとPCの6桁コードが一致することを確認し、PC側で許可します。

指紋が一致しない場合は証明書を導入せず、Room Indicatorを終了してネットワークを確認してください。

## 登録後にワンタップで見る

証明書導入と端末登録が完了したスマートフォンでは、映像画面にホーム画面追加の案内が表示されます。

- Android: 「ホーム画面に追加」を押します。
- iPhone / iPad: Safariの共有ボタンから「ホーム画面に追加」を選びます。

以後は、ホーム画面のRoom Indicatorアイコンを1回タップするだけで映像画面を開けます。PC側では先に `run_test.bat` を起動しておく必要があります。

## Windows Firewall

スマートフォンから接続できない場合だけ、Room Indicatorを起動したまま管理者PowerShellで次を実行します。

```powershell
.\configure_firewall.ps1
```

HTTPSポートを変更した場合は、その番号を指定します。

```powershell
.\configure_firewall.ps1 -Port 5444
```

スクリプトは、Room Indicatorが実際に待ち受けているIPとPython実行ファイルに対し、Windowsの「プライベート」ネットワークおよびローカルサブネットからの接続だけを許可します。Windowsのネットワークプロファイルが「パブリック」の場合は許可されません。

## 他PCへ移すときの注意

次のフォルダーやファイルを他人または別PCへコピーしないでください。

- `.venv/`: 作成したPC専用のPython環境
- `tls/`: CA秘密鍵を含む証明書
- `devices.json`: 登録端末情報
- `config.json`: PC固有の配信設定
- `*.log`: 実行ログ

特に `tls/room-indicator-ca-key.pem` を入手した人は、そのCAを信頼したスマートフォンに対する偽サーバー証明書を作成できるため、共有禁止です。新PCでは `setup.bat` と `run_test.bat` を使って証明書を新規生成し、スマートフォンも新しく登録してください。

## 主な設定

`config.json` は初回設定後に生成され、次の項目を利用できます。

- `port`: PC専用管理画面のHTTPポート。既定値は `5000`
- `https_port`: スマートフォン用HTTPSポート。既定値は `5443`
- `lan_ip`: VPNや複数LANがある場合に使用するIPv4アドレス。通常は空欄
- `tls_enabled`: HTTPSを有効にするか。通常は必ず `true`
- `pairing_base_url`: HTTPSリバースプロキシを正しく構成した場合だけ使う公開URL
- `fps`: 配信フレームレート。`1`～`30`

`tls_enabled: false` では端末Cookieと映像が平文になるため、一般利用では使用しないでください。ポート、IP、TLS設定の変更は再起動後に反映されます。

## 開発・検証

初回の開発環境構築:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
```

自動テスト:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
```

実サーバーを起動した状態でのHTTP・HTTPS疎通確認:

```powershell
.\.venv\Scripts\python.exe smoke_test.py
```

## 配布ZIPの作成

開発PCのPowerShellで次を実行します。

```powershell
.\build_release.ps1
```

初回はPython公式サイトからPython 3.12.10の64ビットインストーラーと公式ライセンスを取得します。取得したインストーラーはSHA-256とPython Software Foundationの電子署名を検証し、以後は `vendor/` 内の検証済みファイルを再利用します。

`dist/` にWindows x64向けZIPとSHA-256ファイルが作成されます。ZIPは必要なアプリファイルを明示的に収録するため、`.venv/`、証明書、登録端末、設定、ログ、Git情報は含まれません。
