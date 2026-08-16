# Droste

Windows上の指定ウィンドウまたは画面全体を、同じプライベートLANにいる登録済みスマートフォンへHTTPS配信します。PC管理画面はlocalhostだけに限定し、スマートフォンはDrosteがPCごとに生成するローカルCA証明書とQRコードで登録します。

DrosteはLAN専用です。インターネットや公共Wi-Fiへ公開しないでください。

## 対応環境

- Windows 10 / 11（64ビット）
- iPhone / iPadのSafari（主な利用対象）
- PCとスマートフォンが同じ家庭内・社内のプライベートLANに接続されていること
- ゲストWi-Fiの端末間通信禁止やAP isolationが無効であること

画面配信を実行するため、Windowsへ通常ログインした状態で起動する必要があります。

## 配布ZIPを受け取ったPC

1. ZIPをデスクトップやドキュメントなど、書き込み可能な場所へ展開します。
2. 展開したフォルダーの `Droste.exe` をダブルクリックします。
3. 初回起動は単体EXEの準備に少し時間がかかる場合があります。
4. PC管理画面 <http://localhost:5000/> が自動的に開きます。
5. Windows Firewallの確認が出た場合は、信頼できる「プライベート ネットワーク」だけを許可します。「パブリック ネットワーク」は許可しません。

Pythonの導入や `setup.bat` の実行は不要です。ブラウザーを閉じてもDrosteは動作を続けます。管理画面を再度開く場合は通知領域のDrosteアイコンをダブルクリックし、終了する場合は右クリックして「Drosteを終了」を選びます。

起動できない場合は、`Droste.exe`と同じフォルダーに作成される `droste.log` を確認してください。

## スマートフォンの初回登録

1. PC管理画面の「スマホのHTTPS初期設定」に表示されるQRコードを読み取ります。
2. スマートフォン画面とPC管理画面に表示された証明書のSHA-256指紋が完全に一致することを確認します。
3. 画面の案内に従い、Droste用のローカルCA証明書を導入します。
4. iPhone / iPadでは「設定」→「一般」→「情報」→「証明書信頼設定」から `Droste Local CA` を有効にします。
5. PC管理画面で「端末を追加」を押し、新しく表示されたQRコードを読み取ります。
6. スマートフォンとPCの6桁コードが一致することを確認し、PC側で許可します。

指紋が一致しない場合は証明書を導入せず、Drosteを終了してネットワークを確認してください。

## 登録後にワンタップで見る

iPhone / iPadでは、映像画面をSafariで開き、共有ボタンから「ホーム画面に追加」を選びます。以後はホーム画面のDrosteアイコンから開けます。PC側では先に `Droste.exe` を起動してください。

Androidは動作確認用で、ホーム画面アプリとしての利用は対象外です。

## Windows Firewall

スマートフォンから接続できない場合だけ、Drosteを起動したまま管理者PowerShellで次を実行します。

```powershell
.\configure_firewall.ps1
```

HTTPSポートを変更した場合は、その番号を指定します。

```powershell
.\configure_firewall.ps1 -Port 5444
```

このスクリプトは、Drosteが実際に待ち受けているIPと `Droste.exe` に対し、「プライベート」ネットワークおよびローカルサブネットからの接続だけを許可します。

## アンインストール

Drosteはポータブルアプリなので、Windowsへの通常のインストールは行いません。ただし、先にスマートフォンからDrosteのCA証明書を削除する必要があります。詳しくは `UNINSTALL.txt` を参照してください。

## PC固有データ

実行すると `Droste.exe` と同じフォルダーに次のデータが作成されます。他人や別PCへコピーしないでください。

- `tls/`: CA秘密鍵を含む証明書
- `devices.json`: 登録端末情報
- `config.json`: PC固有の配信設定
- `droste.log`: 診断ログ

特に `tls/droste-ca-key.pem` を入手した人は、そのCAを信頼したスマートフォンに対する偽サーバー証明書を作成できます。新PCでは新しい配布ZIPを展開し、証明書と端末登録を新規作成してください。

## 主な設定

`config.json` は設定変更後に生成されます。

- `port`: PC専用管理画面のHTTPポート。既定値は `5000`
- `https_port`: スマートフォン用HTTPSポート。既定値は `5443`
- `lan_ip`: VPNや複数LANがある場合に選ぶプライベートIPv4アドレス
- `fps`: 配信フレームレート。`1`～`30`

スマートフォン側は常にHTTPSです。公開URL、平文HTTP、パブリックIPv4アドレスは設定できません。

## 開発・検証

開発環境を作成します。

```powershell
py -3.13 -m venv .venv
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

## Droste.exeの作成

開発用依存関係を導入してからビルドします。

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.lock.txt
.\build_executable.ps1
```

`build/executable/Droste.exe` が作成されます。PyInstallerの単体EXEなので、配布先へPythonを導入する必要はありません。

## 配布ZIPの作成

```powershell
.\build_release.ps1
```

`dist/` にWindows x64向けZIPとSHA-256ファイルが作成されます。配布ZIPには `Droste.exe`、案内文、Firewall用スクリプト、実際に同梱されたPythonランタイムとライブラリのライセンス文書だけが入ります。証明書、端末情報、設定、ログ、Git情報は入りません。

広く一般公開する最終版では、`Droste.exe`へWindowsコード署名を付けることを推奨します。

LAN境界と依存関係監査の前提は `SECURITY.md` に記録しています。
