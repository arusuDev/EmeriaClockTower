# EmeriaClockTower

エメリア電波塔に付属の時計台です。
Oracle Cloud 上で動かす想定の Discord bot で、**ポモドーロタイマー**機能を備えています。

## 機能

- `/pomodoro start work:<分> rest:<分> cycles:<回>` — コマンドを実行した人がいるボイスチャンネルでポモドーロを開始します。
  - **作業中**：そのVCにいる人を全員 *サーバーミュート* します。
  - **休憩中**：botがミュートしたメンバーの *サーバーミュート を解除* します（talkable）。
  - 作業中にVCに入ってきた人も自動でミュートします。
- `/pomodoro stop` — 実行中のポモドーロを停止し、bot が付けたミュートを全て解除します。
- `/pomodoro status` — 現在のフェーズ（作業中/休憩中）、サイクル、残り時間を確認します。

パラメータのデフォルト値は `work=25` / `rest=5` / `cycles=4`（古典的なポモドーロ）。最後の作業サイクルの後には休憩は入れずに終了します。

> bot がメンバーをミュート／解除するため、対象サーバー・対象VCで **「メンバーをミュート」** 権限が付与されている必要があります。

## 必要な設定

### Discord Developer Portal

1. [Discord Developer Portal](https://discord.com/developers/applications) で Application を作成。
2. **Bot** タブでトークンを取得（`DISCORD_TOKEN` に設定）。
3. **Privileged Gateway Intents** で **Server Members Intent** を ON にします（VC参加時のメンバー情報に必要）。
4. **OAuth2 → URL Generator** で以下のスコープ／権限を選択してサーバーへ招待：
   - scopes: `bot`, `applications.commands`
   - permissions: `View Channels`, `Send Messages`, `Connect`, `Mute Members`

### 環境変数

`.env.example` をコピーして `.env` を作り、`DISCORD_TOKEN` を設定します。
開発中はテスト用ギルドに即時反映させるため `DISCORD_GUILD_ID` を設定すると便利です（未設定だとグローバル登録で反映に最大1時間ほどかかります）。

```
cp .env.example .env
$EDITOR .env
```

## ローカル実行

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python bot.py
```

## Oracle Cloud へのデプロイ（Ubuntu想定）

最小構成（Always Free の `VM.Standard.E2.1.Micro` や `A1.Flex` で十分動きます）：

```bash
# 依存
sudo apt update && sudo apt install -y python3-venv git

# 配置
sudo mkdir -p /opt/EmeriaClockTower
sudo chown "$USER":"$USER" /opt/EmeriaClockTower
git clone https://github.com/arusudev/emeriaclocktower.git /opt/EmeriaClockTower
cd /opt/EmeriaClockTower

# 仮想環境＋依存
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt

# 環境変数
cp .env.example .env
$EDITOR .env   # DISCORD_TOKEN を記入

# systemd ユニットを設置（User=ubuntu 固定なので必要なら編集）
sudo cp deploy/emeria-clocktower.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now emeria-clocktower.service

# ログ
journalctl -u emeria-clocktower.service -f
```

再起動しても自動で立ち上がり、bot がクラッシュしても systemd が `Restart=on-failure` で復帰します。

## 動作イメージ

```
ユーザーがVCに参加
  ↓
/pomodoro start work:25 rest:5 cycles:4
  ↓
[作業 25分] VCメンバー全員をサーバーミュート（途中参加者も）
  ↓
[休憩  5分] bot がミュートしたメンバーのみ解除
  ↓
…サイクル繰り返し…
  ↓
最後の作業サイクル終了 → 自動で全解除し完了
```

## 実装メモ

- ポモドーロの状態はギルド単位でメモリ上に保持します（bot再起動で消えます）。
- 元々モデレーターによってサーバーミュートされているメンバーには触りません（作業開始時の状態を尊重）。
- VCから退出したメンバーは追跡対象から外します。
