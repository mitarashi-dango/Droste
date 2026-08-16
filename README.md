# Droste

Drosteは、Windows PCの画面を手元のスマートフォンで見るためのアプリです。PCの画面全体、または選んだウィンドウだけを、同じ家庭内・社内ネットワークに接続したスマートフォンへ配信できます。PCと登録済みスマートフォンの間で使えるグループチャットも備えています。

映像とチャットは同じネットワーク内だけでやり取りされます。インターネット越しの配信には対応していません。公共Wi-Fiでは使用せず、信頼できる家庭内・社内ネットワークで使用してください。

## できること

- PCの画面全体、または指定したウィンドウをスマートフォンで表示
- 登録したスマートフォンだけに配信
- PCと登録済みスマートフォンでグループチャット
- iPhone / iPadとAndroidのホーム画面からワンタップで接続
- インストーラーを使わず、展開したフォルダーから起動

## 対応環境

- Windows 10 / 11（64ビット）
- iPhone / iPadのSafari（現行版）
- Android版Google Chrome（現行版）
- PCとスマートフォンが同じ家庭内・社内のプライベートLANに接続されていること
- ゲストWi-Fiの端末間通信禁止やAP isolationが無効であること

画面配信を実行するため、Windowsへ通常ログインした状態で起動する必要があります。

## ダウンロードして起動する

1. [Releasesページ](https://github.com/mitarashi-dango/Droste/releases/latest)を開き、最新版の `Droste-（バージョン）-windows-x64.zip` をダウンロードします。
2. ZIPの中身を、デスクトップやドキュメントなどのフォルダーへすべて展開します。ZIPを開いたまま `Droste.exe` を実行しないでください。
3. 展開したフォルダーにある `Droste.exe` をダブルクリックします。
4. 初回は起動準備のため、画面が開くまで少し時間がかかる場合があります。
5. Drosteの管理画面が既定のブラウザーで自動的に開きます。
6. Windows Firewallの確認が表示された場合は、信頼できる「プライベート ネットワーク」だけを許可します。「パブリック ネットワーク」は許可しません。

配布ZIPには動作に必要なものが含まれています。別のソフトをインストールしたり、コマンドを入力したりする必要はありません。

ブラウザーに表示されるのはDrosteの操作画面です。Droste本体は、Windows画面右下の通知領域（時計の近く）で動作します。そのため、ブラウザーのタブやウィンドウを閉じただけではDrosteは終了しません。

- 管理画面をもう一度開く: 通知領域のDrosteアイコンをダブルクリック
- Drosteを終了する: 通知領域のDrosteアイコンを右クリックし、「Drosteを終了」を選択

通知領域にアイコンが見当たらない場合は、「▲」を押して隠れているアイコンを表示してください。

起動できない場合は、`Droste.exe`と同じフォルダーに作成される `droste.log` を確認してください。

## スマートフォンの初回登録

初回だけ、スマートフォンにDroste専用の証明書を登録します。この証明書は、PCとスマートフォンの通信を暗号化し、接続先が自分のPCであることを確認するために使われます。

1. PC管理画面の「スマホのHTTPS初期設定」に表示されるQRコードを読み取ります。
2. スマートフォン画面とPC管理画面に表示された証明書のSHA-256指紋が完全に一致することを確認します。
3. 画面で端末に合うボタンを選び、Droste用のローカルCA証明書を導入します。
4. 端末側で証明書を有効にします。
   - iPhone / iPad: 「設定」→「一般」→「情報」→「証明書信頼設定」から `Droste Local CA` を有効にします。
   - Android: 設定アプリで「証明書」を検索し、「CA証明書をインストール」からダウンロードした `droste-ca.crt` を選び、Chromeを開き直します。設定名はメーカーにより異なります。
5. PC管理画面で「端末を追加」を押し、新しく表示されたQRコードを読み取ります。
6. スマートフォンとPCの6桁コードが一致することを確認し、PC側で許可します。

指紋が一致しない場合は証明書を導入せず、Drosteを終了してネットワークを確認してください。

## 2回目から簡単に開く

iPhone / iPadでは、映像画面をSafariで開き、共有ボタンから「ホーム画面に追加」を選びます。

Androidでは、映像画面をChromeで開き、画面内の「Drosteをインストール」、またはChrome右上の「⋮」→「ホーム画面に追加」/「アプリをインストール」を選びます。

以後はどちらもホーム画面のDrosteアイコンから開けます。スマートフォンから見る前に、PCで `Droste.exe` を起動しておいてください。Androidのその他のブラウザーは正式対象外です。

## グループチャット

映像画面の「グループチャット」を開くと、Drosteを起動しているPC（ホストPC）と、すべての登録済みスマートフォンでメッセージを共有できます。PCは「ホストPC」、スマートフォンは端末登録時の名前で表示されます。

- 1件140文字まで
- Enterで送信、Shift+Enterで改行
- グループ名はホストPCだけが変更可能
- 最新200件をPCの `chat.json` に保存
- 未登録端末はメッセージの閲覧・送信とも不可

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
- `chat.json`: グループチャットの最新200件
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
