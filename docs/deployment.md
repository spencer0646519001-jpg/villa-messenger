# 雲端部署指南

> 本文件記錄 villa_messenger 在雲端環境的實際部署方式,以及上線過程中踩到的坑。
> 目的是讓未來重新部署、換機器、或除錯時,不必重踩一次已經花時間找到答案的問題。
>
> ⚠️ 本 repo 為 public repo。本文件不含真實主機 IP、網域全名、SSH 帳密、API key、
> token、calendar ID,或任何真實的 LINE ID。所有敏感值一律以 `<placeholder>`
> 表示,請自行填入伺服器上的 `.env`。

最後更新:2026-07-29

---

## 部署架構總覽

- **平台**:單一 Linux VPS(Ubuntu),以 Docker Compose 部署。可與其他既有專案
  在同一台主機上以不同的 host port / compose 專案名 side-by-side 共存。
- **反向代理 / HTTPS**:系統套件版 Caddy,設定於 `/etc/caddy/Caddyfile`,套用
  變更用 `systemctl reload caddy`。
- **對外網域**:一個子網域反向代理到本專案的 host port。
- **持久化資料**:SQLite 資料庫存放於 Docker named volume,掛載到容器內固定路徑,
  由 `DATABASE_PATH` 環境變數指定(見 [app/settings.py](../app/settings.py))。
- **不進版控、需手動放上伺服器的檔案**:
  - `.env`(所有環境變數的真實值,參考 repo 根目錄的 `.env.example`)
  - `secrets/service-account.json`(Google 服務帳號憑證,若啟用 Calendar 功能,
    以 read-only bind mount 掛進容器)

若同一台主機已有其他 Docker Compose 專案,部署前務必確認以下三者不與既有專案撞號:

- host port
- Docker Compose 專案名(`-p <name>`)
- Docker volume 名稱

---

## 部署指令範本

以下指令以 repo root 為 `/root/villa-messenger`、compose 專案名為
`villa-messenger`、DB 路徑為 `/data/homestay.db` 為例,依實際環境調整。

```bash
# 1. clone
cd /root
git clone <repo-url> villa-messenger
cd /root/villa-messenger

# 2. 手動建立 .env(填真實值)與 secrets/service-account.json
nano .env
mkdir -p secrets && nano secrets/service-account.json
chmod 600 .env secrets/service-account.json

# 3. build
docker compose -p villa-messenger build

# 4. pre-start schema migration(不可略過,見下方「初次部署」章節)
docker compose -p villa-messenger run --rm \
  -e DATABASE_PATH=/data/homestay.db \
  web \
  python -c "from app.repositories.sqlite import init_db; init_db('/data/homestay.db')"

# 5. 啟動
docker compose -p villa-messenger up -d

# 6. 本機驗證
curl http://127.0.0.1:<host-port>/health

# 7. Caddy 設定(在 Caddyfile 加上子網域區塊後)
caddy validate --config /etc/caddy/Caddyfile
systemctl reload caddy
```

Caddy 設定片段範例:

```
<subdomain>.<domain> {
    reverse_proxy 127.0.0.1:<host-port>
}
```

### 初次部署:schema migration 不會自動套用

`init_db()` 不會在 uvicorn 啟動時自動執行,必須在容器第一次啟動前手動跑一次
(見上方步驟 4)。日後 schema 有變動時也需要重新執行對應的 migration。

### 初次部署:光跑 `init_db` 不夠 —— 還需要 seed channel 和 owner

本系統是多租戶架構,`init_db()` 只建立空的表結構。程式要能正常運作,還需要
seed 兩類資料:

1. **tenant + channel**(`tenant_channels` 表)—— 把 LINE 官方帳號自己的
   `destination`(一個 `U...` ID)對應到租戶。沒有這筆,webhook 會被 reject。
2. **owner**(`tenant_owners` 表)—— 誰該收到 owner 推播。沒有這筆,程式會退回
   使用 `.env` 的 `LINE_TEST_OWNER_USER_ID` 作為備援 owner。

`destination` 這個值**第一次無法預先知道**,必須先讓 webhook 驗證失敗一次,
從 log 撈出來才能拿去 seed:

```bash
docker compose -p villa-messenger logs --since 5m web | grep "unknown channel"
```

seed channel 範例:

```bash
docker compose -p villa-messenger run --rm web python - <<'PYEOF'
from app.repositories.tenant_channel_repository import TenantChannelRepository
from app.repositories.tenant_repository import TenantRepository
DB = "/data/homestay.db"
destination = "<從 log 撈到的 U... destination>"
tenant = TenantRepository(DB).get_by_slug("<tenant-slug>")
tid = int(tenant["id"])
ch = TenantChannelRepository(DB)
if ch.get_by_channel(platform="line", channel_id=destination) is None:
    ch.create_channel(tenant_id=tid, platform="line", channel_id=destination,
                      channel_secret_ref="LINE_TEST_CHANNEL_SECRET")
PYEOF
```

seed owner 請參考 `scripts/add_owner.py` 內的 SQL,**但注意下方「腳本硬編碼相對
路徑」的陷阱**——在容器內執行時必須改用絕對路徑,不可直接跑腳本原檔。

---

## 常用維運指令

```bash
docker compose -p villa-messenger logs -f web             # 看即時 log
docker compose -p villa-messenger up -d --force-recreate  # 改 .env 後重新載入(見坑 4)
docker compose -p villa-messenger down                    # 停(保留 DB volume)
# ⚠️ 千萬別加 -v,那會刪掉 SQLite 資料庫的 volume
```

更新程式碼後重新部署:

```bash
cd /root/villa-messenger && git pull
docker compose -p villa-messenger build
docker compose -p villa-messenger up -d
```

---

## 上線踩坑記錄

以下依實際發生順序記錄,格式為「症狀 → 原因 → 解法」。

### 坑 1:`.dockerignore` 排除 `data/` 導致 tenant config 進不了 image

**症狀**:部署後客人訊息進來,log 出現
`TenantConfigLoadError: Tenant config file not found for tenant '<slug>':
data/tenants/<slug>/config.json`,Google Calendar 空房檢查失效。

**原因**:原本 `.dockerignore` 寫了 `data/`,意圖是排除本機開發用的
SQLite 檔案,但這會把整個 `data/` 資料夾排除,連必要的租戶設定檔
`data/tenants/<slug>/config.json` 也被擋在 image 外。

**解法**:改成只排除資料庫檔案本身,不要排除整個 `data/`:

```
data/*.db
data/*.db-journal
data/*.sqlite
data/*.sqlite3
```

**教訓**:`data/` 底下混了兩種性質的東西——「本機開發資料庫」(不該進 image)
和「租戶靜態設定檔」(必須進 image)。排除規則要精確到檔案層級,不能用資料夾
整體排除。

---

### 坑 2:seed 腳本硬編碼相對路徑,容器內直接跑會寫錯地方

**症狀**:`scripts/seed_sandbox.py` 和 `scripts/add_owner.py` 執行後看似成功,
但正式服務讀不到 seed 的資料;容器重建後 seed 的資料完全消失。

**原因**:兩支腳本都寫死了相對路徑(例如 `data/homestay.db`)。在容器裡直接跑,
資料會寫到容器內的本機路徑,而不是掛載的 volume 路徑,所以正式服務讀不到,
容器一旦重建就會遺失。

**解法**:在容器內執行任何 seed / 查詢腳本時,一律用絕對路徑指向 volume 掛載點
(例如 `/data/homestay.db`),不要直接執行腳本原檔的預設路徑。可以用
`python -c` 或 heredoc 內嵌腳本邏輯並手動指定絕對路徑(見上方 seed 範例)。

**TODO**:讓這些腳本改讀 `settings.database_path` 而非硬編碼相對路徑,避免這個
陷阱(腳本自身的註解也已經標註這點)。

---

### 坑 3:uvicorn 沒設 `--log-level debug`,背景任務錯誤全被靜音

**症狀**:webhook 回 `200 OK`,但客人收不到回覆,而且 log 完全乾淨、看不到任何
錯誤。非常難除錯。

**原因**:專案程式碼用 `logging.getLogger(__name__)`,但沒有任何地方呼叫
`logging.basicConfig()`。本機開發時 `uvicorn --reload` 會自動幫忙設定 logging,
所以看得到訊息;但在 Docker 容器裡跑 uvicorn(無 `--reload`),那些
`logger.warning` / `logger.error` 全部不會輸出。

這特別致命,因為 webhook 背景任務(FastAPI `BackgroundTasks`)的錯誤處理路徑
是靠 `logger.error` 記錄的——錯誤被靜音,代表背景任務出錯時完全沒有線索可查。

**解法**:在 `docker-compose.yml` 的 `command` 覆寫中加上 `--log-level debug`:

```yaml
command: ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000",
          "--timeout-graceful-shutdown", "30", "--log-level", "debug"]
```

**未來改進 TODO**:更根本的解法是在 `app/main.py` 加上 `logging.basicConfig()`,
讓 logging 不依賴 uvicorn 的啟動參數。

---

### 坑 4:`docker compose restart` 不會重新載入 `.env`

**症狀**:改了 `.env` 的 channel secret,`restart` 之後容器仍在用舊的值,導致
`signature verification failed`。

**原因**:`docker compose restart` 只是重啟容器內的行程,不會重新讀取
`env_file`。

**解法**:改用:

```bash
docker compose -p villa-messenger up -d --force-recreate
```

**驗證方式**(確認容器真的吃到新值,只印前綴不洩漏密鑰):

```bash
docker compose -p villa-messenger exec web python -c \
  "import os; s=os.environ.get('LINE_TEST_CHANNEL_SECRET',''); print('prefix:', s[:8], 'len:', len(s))"
```

---

### 坑 5:本機開發環境沒關,會攔截原本該送到雲端的 webhook

**症狀**:雲端明明部署好了,但客人訊息「已讀不回」,雲端 log 也看不到請求,或看到
請求但行為詭異。

**原因**:本機還開著開發用的 uvicorn 加上通道穿透工具(例如 ngrok),而 LINE
Developers Console 的 webhook URL 若仍指向本機通道(或設定切換的過渡期),
訊息會被本機攔截,不會到雲端。

**解法**:切換到雲端測試前,務必先關掉本機的開發伺服器與通道穿透工具,並確認
LINE Developers Console 的 webhook URL 已指向雲端網域。

---

### 坑 6:LINE userId 與 destination 都「綁定特定官方帳號」,換帳號必須重抓

這是切換官方帳號時最容易忽略、也最容易誤判的坑。

**關鍵特性**:同一個人(或同一個 bot),在不同的 LINE 官方帳號眼中,識別碼是
完全不同的。這適用於兩種 ID:

| 識別碼 | 是什麼 | 綁定對象 |
| --- | --- | --- |
| `destination` | 官方帳號自己的 `U...` ID(webhook payload 裡) | 每個官方帳號一個 |
| `platform_user_id` | 使用者(客人 / owner)的 `U...` ID | 每個官方帳號各自獨立 |

**症狀 A(owner 推播全部失敗)**:

```
LINE owner push send failed
httpx.HTTPStatusError: Client error '400 Bad Request'
  for url 'https://api.line.me/v2/bot/message/push'
```

每則客人訊息進來都失敗一次。客人的回覆正常(走 `replyToken`,不受影響),
只有 owner push 掛掉。

**症狀 B(owner 指令無反應)**:owner 傳入內建指令(如查詢狀態、查詢紀錄)時,
系統把他們當成一般客人處理(回報價、追問日期),不認得是 owner。

**原因**:從舊帳號(例如測試帳號)的資料庫沿用了 owner 的 `platform_user_id`。
這些 ID 在新的官方帳號下無效,LINE API 會拒絕(400),owner 識別也對不上。

**解法**:換官方帳號後,owner 資料必須重新抓、重新 seed:

1. 請每位 owner 用 LINE 傳一則訊息給**新的**官方帳號(內容隨意)。
2. 從 `messages` 表撈出他們在新帳號下的 `platform_user_id`:
   ```bash
   docker compose -p villa-messenger run --rm web python -c "
   import sqlite3
   conn = sqlite3.connect('/data/homestay.db')
   conn.row_factory = sqlite3.Row
   rows = conn.execute(
       'SELECT id, platform_user_id, message_text, created_at FROM messages ORDER BY id DESC LIMIT 10'
   ).fetchall()
   for r in rows:
       print(dict(r))
   "
   ```
   從 `message_text` 對照出誰是誰。
3. 用新的 userId upsert 進 `tenant_owners`,並將舊帳號的 owner 記錄設為
   `is_active=0`(否則系統會持續嘗試 push 給無效的 ID 並失敗)。
4. 驗證:
   ```bash
   docker compose -p villa-messenger run --rm web python -c "
   from app.repositories.tenant_owner_repository import TenantOwnerRepository
   print(TenantOwnerRepository('/data/homestay.db').list_active_owner_user_ids(
       tenant_id=1, platform='line'))
   "
   ```

**注意**:owner 資料存在資料庫,程式每次處理訊息都會即時查詢,所以更新後**不需要
重啟容器**,下一則訊息立即生效。這與 `.env` 不同——`.env` 是容器啟動時載入
記憶體,改了必須 `--force-recreate`(見坑 4)。

---

### 坑 7:`_ensure_column` 是手動註冊制,不會自動掃描 `schema.sql`

**症狀**:`schema.sql` 加了新欄位,正式站也照著跑了 `init_db()`,但欄位還是不
存在,程式報 `no such column: xxx`。

**原因**:`init_db()`(見 `app/repositories/sqlite.py`)對「新表」是用
`CREATE TABLE IF NOT EXISTS`,新表會照 `schema.sql` 完整建立,沒問題。但對
「既有表新增欄位」**不是自動比對 `schema.sql` 做的**——每個要補的欄位都必須在
`init_db()` 裡手寫一行對應的 `_ensure_column(connection, table, column, ddl)`
呼叫。`_ensure_column` 本身只做一件事:檢查該欄位在不在,不在就
`ALTER TABLE ADD COLUMN`。

也就是說,`schema.sql` 加了欄位,如果忘記同步在 `init_db()` 補一行
`_ensure_column`,既有資料庫**永遠不會拿到那個欄位**——不會報錯提醒你,只會
在程式真的讀寫到那個欄位時才炸。

**解法**:在 `schema.sql` 新增欄位時,必須同時在 `init_db()` 加一行對應的
`_ensure_column()`,否則既有資料庫永遠不會拿到那個欄位(新表不用,
`CREATE TABLE IF NOT EXISTS` 會處理)。本次(27bc622)實際新增的五行,可當
範例:

```python
_ensure_column(connection, "conversation_states", "room_count", "INTEGER")
_ensure_column(
    connection, "conversation_states", "accumulated_while_off", "INTEGER NOT NULL DEFAULT 0"
)
_ensure_column(connection, "conversation_states", "last_off_mode_update_at", "TEXT")
_ensure_column(connection, "messages", "customer_display_name", "TEXT")
_ensure_column(connection, "tenant_operation_state", "last_digest_sent_date", "TEXT")
```

**補充兩點**:

- `NOT NULL DEFAULT <value>` 這種形式是可以的,SQLite 的 `ADD COLUMN` 支援帶
  預設值的 NOT NULL 欄位,既有資料列會套用該預設值(本次 `accumulated_while_off`
  實測正常)。
- 但 **NOT NULL 且沒有預設值、UNIQUE 約束、欄位改名、型別變更**,`ADD COLUMN`
  都做不到,需要正式 migration 或重建策略。

**部署前自我檢查**(比對 `schema.sql` 改動和 `_ensure_column` 數量是否對得上):

```bash
# 看整個部署區間 schema.sql 改了什麼
git diff <正式站目前的commit>..origin/main -- app/repositories/schema.sql

# 對照新版有沒有補上對應的 _ensure_column
git show origin/main:app/repositories/sqlite.py | grep -n "_ensure_column"
```

兩邊數量對不上,就代表有欄位會被漏掉。

---

### 坑 8:有 schema 變更時,`exec` 用的是舊 image,必須先 build 再用 `run --rm` 做 migration

**症狀**:換版前想先跑 migration「墊底」,用
`docker compose exec web python -c "...init_db..."`,結果新欄位還是沒加上去。

**原因**:`exec` 是進入**目前正在跑的容器**,裡面是**舊 image**——舊版
`sqlite.py` 根本沒有新欄位對應的 `_ensure_column` 呼叫,自然加不出新欄位。必須
用**新 image**跑 migration,而新 image 要先 build 出來,但還不能立刻拿去換版
(舊容器此時仍在正常服務客人)。

**正確順序**:

```bash
# 1. 先 build 新 image(此時舊容器還在服務,客人不受影響)
docker compose -p villa-messenger build

# 2. 用「新 image」跑一次性容器做 migration(用 run --rm,不是 exec)
docker compose -p villa-messenger run --rm web \
  sh -c "cd /app && PYTHONPATH=. python -c \"from app.repositories.sqlite import init_db; init_db('/data/homestay.db')\""

# 3. 驗證欄位/表確實建好(見下方指令)
# 4. 確認無誤才換版
docker compose -p villa-messenger up -d
```

關鍵是:**`run --rm` 用的是剛 build 好的新 image;`exec` 用的是舊容器**,兩者
不能混用來做 migration。

**驗證 migration 的指令**:

```bash
docker compose -p villa-messenger run --rm web \
  python -c "import sqlite3; c=sqlite3.connect('/data/homestay.db'); print('messages:', [r[1] for r in c.execute('PRAGMA table_info(messages)')]); print('conv_states:', [r[1] for r in c.execute('PRAGMA table_info(conversation_states)')])"
```

**另一個要注意的細節**:migration 指令裡的資料庫路徑**一律直接寫死
`/data/homestay.db`**,不要假設腳本會自己讀對 `settings.database_path`。本次
實測,`scripts/set_mode.py` 在容器內執行時解析到了錯誤的相對路徑,甚至建出一個
0 bytes 的空資料庫(細節見下方「尚待處理的 TODO」)——這跟坑 2 記錄的「容器內
一律用絕對路徑」是同一類陷阱,任何 seed / migration / 查詢腳本都要小心,不要
假設它們會自己讀對路徑。

---

### 坑 9:伺服器上未 commit 的手動修改,會擋住下次 `git pull`

**症狀**:`git pull` 直接失敗:

```
error: Your local changes to the following files would be overwritten by merge:
        .dockerignore
        docker-compose.yml
```

**原因**:上次部署救火時,直接在伺服器上改了這兩個檔案應急(見坑 1、坑 3),但
沒有立刻回頭把同樣的修改補進 repo commit。後來這些修復也正確地補進了 repo
(獨立 commit),於是伺服器本機未提交的手動修改,和新 `pull` 下來的版本改到了
同一個地方,產生衝突。

**正確處理方式**(不要直接 `git checkout .` 或 `git stash` 了事):

```bash
# 1. 先備份
cp .dockerignore .dockerignore.server-backup
cp docker-compose.yml docker-compose.yml.server-backup

# 2. 看清楚伺服器上到底手改了什麼
git diff .dockerignore docker-compose.yml

# 3. 看新版(即將 pull 下來的版本)長怎樣
git show origin/main:docker-compose.yml

# 4. 確認新版「完整包含」手改的內容,才丟棄本機修改
git checkout -- .dockerignore docker-compose.yml
git pull
```

**為什麼要比對而不是直接丟棄**:伺服器上的手改可能包含正式環境專屬的設定
(port、volume、環境變數),盲目丟棄可能把服務弄壞。本次比對後確認新版已完整
包含手改內容(`--log-level debug`、`--timeout-graceful-shutdown 30`、精確的
`.dockerignore` glob),才安全覆蓋。

**根本解法**:伺服器上救火修改後,要盡快把同樣的修改補進 repo 並 commit,不要
讓它只存在於單一台機器上,否則遲早在下次 `git pull` 時衝突。

---

## 更新既有服務的標準流程(含 schema 變更)

以下是實際跑過、驗證有效的「更新既有服務」完整順序。適用於有 schema 變更的
部署;若這次沒有 schema 變更,可省略其中 migration 相關步驟。

1. `cd /root/villa-messenger && git log -1 --oneline` —— **先記下目前
   commit**,萬一要回滾會用到。
2. `docker compose -p villa-messenger ps` —— 確認 service 名稱(本專案是
   `web`)。
3. `git fetch && git log --oneline <目前commit>..origin/main` —— 確認落後
   多少 commit。
4. `git diff <目前commit>..origin/main -- app/repositories/schema.sql` ——
   確認 schema 變更範圍,並依坑 7 的方法對照 `_ensure_column` 是否補齊。
5. **備份資料庫**(容器內 + host 各留一份,見下方指令)。
6. 若 `git pull` 因伺服器手改衝突失敗,依坑 9 處理。
7. `build` → 用新 image `run --rm` 跑 migration → 驗證欄位(依坑 8)→ 確認
   無誤才 `up -d`。
8. `docker compose -p villa-messenger logs -f web` 確認出現
   `Application startup complete` 且沒有 `OperationalError`。
9. 用真實 LINE 帳號實際發一則訊息驗證。

**備份指令**:

```bash
docker compose -p villa-messenger exec web \
  sh -c "cp /data/homestay.db /data/homestay.db.bak-$(date +%Y%m%d-%H%M)"
docker compose -p villa-messenger exec web ls -lh /data/

# 再拉一份到 host,避免容器/volume 出事時連備份都沒了
docker compose -p villa-messenger cp web:/data/homestay.db ./homestay-backup-$(date +%Y%m%d-%H%M).db
```

**回滾方式**:

- 程式有問題 → `git checkout <舊commit> && build && up -d`(舊程式碼配新
  schema 通常仍可運作,多出來的欄位不影響舊程式讀寫)。
- 資料庫有問題 → 先停服務,用備份覆蓋 `/data/homestay.db`,再退回舊 commit。

---

## 切換 LINE 官方帳號的完整流程

當要從測試帳號切換到正式官方帳號(或更換任何官方帳號)時,依序執行:

1. **在 LINE Official Account Manager 啟用 Messaging API**
   - 一個 LINE 官方帳號「存在」不代表它會出現在 LINE Developers Console。
     必須先在 OA Manager(設定 → Messaging API)啟用,選擇/建立一個 Provider,
     該帳號才會出現在 Developers Console。
   - 這也解釋了為何一個從未接過 API 的官方帳號,在 Developers Console 會顯示
     「Provider not found」——這是正常現象。

2. **取得三個值**(Developers Console → 該 channel):
   - Channel ID(Basic settings)
   - Channel secret(Basic settings)
   - Channel access token(Messaging API 分頁,捲到頁面最底部,需按
     `Issue` 發行)

3. **更新伺服器 `.env`** 的 channel 三件組,然後執行
   `docker compose -p villa-messenger up -d --force-recreate`(不是 `restart`,
   見坑 4)。驗證新 token 有效:
   ```bash
   set -a; source .env; set +a
   curl -s -o /dev/null -w "HTTP %{http_code}\n" https://api.line.me/v2/bot/info \
     -H "Authorization: Bearer $LINE_TEST_CHANNEL_ACCESS_TOKEN"   # 期望 200
   ```

4. **設定 webhook URL 並按 Verify** —— 第一次一定會失敗(400),這是預期的。
   從 log 撈出新帳號的 destination:
   ```bash
   docker compose -p villa-messenger logs --since 5m web | grep "unknown channel"
   ```

5. **seed 新 channel**(destination → tenant),然後再按一次 Verify → Success
   (見上方「初次部署」章節的 seed channel 範例)。

6. **打開「Use webhook」開關**(Developers Console)—— Verify 成功不代表開關
   有開,開關沒開的話真實訊息不會送達。

7. **重新抓並 seed owner 的 userId**(見坑 6)—— 舊帳號的 owner ID 在新帳號下
   無效。

8. **檢查 OA Manager 的「回應設定」**:
   - 「回應方式」建議選「手動聊天」(而非「手動聊天+自動回應訊息」),否則
     LINE 內建的自動回應會與本系統的回覆同時發出,造成客人收到重複/矛盾訊息。
   - 「加入好友的歡迎訊息」可保留(只在加好友當下觸發一次,不與本系統衝突),
     但建議檢查文案是否與自動助理的行為一致。

**注意**:舊帳號的 channel 記錄可以保留在 `tenant_channels`(多個 channel 可
同時指向同一個 tenant),這樣測試帳號仍可用於除錯。但 owner 記錄必須切換,
因為 push 只會用當前 `.env` 的 access token 發送。

---

## 已知架構限制

1. **Graceful shutdown timeout**——部署指令帶
   `--timeout-graceful-shutdown 30`,讓背景任務(webhook 處理 + 回覆客人)有
   時間跑完,避免 worker 被殺時客人的回覆遺失。這是半異步 webhook 架構
   (FastAPI `BackgroundTasks`,非持久化佇列)的已知風險窗口——極端情況下
   (例如部署當下剛好有訊息在處理中)仍可能遺失回覆。詳見
   [architecture.md](architecture.md)。

2. **Pre-start schema migration**——schema 變動不會在 uvicorn 啟動時自動套用,
   每次部署都必須手動跑一次 `init_db`(見上方「初次部署」章節)。

3. **SQLite 持久化**——資料庫檔案存放於 Docker named volume,搭配可由環境變數
   覆寫的 `DATABASE_PATH`。務必確認 volume 掛載路徑與 `DATABASE_PATH` 一致,
   否則會重演坑 2 的問題。

---

## 尚待處理的 TODO

- `scripts/seed_sandbox.py` / `scripts/add_owner.py` 硬編碼相對路徑,應改讀
  `settings.database_path`(見坑 2)。
- `app/main.py` 應加 `logging.basicConfig()`,讓 logging 不依賴 uvicorn
  啟動參數(見坑 3)。
- Owner push 加上「跳轉到該客人對話」的 LINE deep link(可行性待查)。
- `scripts/set_mode.py` 在容器內執行時會踩到跟坑 2 一樣的路徑陷阱:腳本頂部
  寫死 `DATABASE_PATH = "data/homestay.db"`(相對路徑,腳本自己的註解也承認
  「Hardcoded for now」),不是讀 `app.settings.database_path`,即使
  `docker-compose.yml` 已把 `DATABASE_PATH` 環境變數設成 `/data/homestay.db`
  也不會生效——腳本沒有讀那個環境變數。實測在容器內直接執行會解析到
  `/app/data/homestay.db`,建出一個 0 bytes 的空資料庫,之後任何操作都報
  `no such table: tenants`。優先度低(民宿主人平常用 LINE 指令切換模式,不會
  用到這支腳本;若真的需要在容器內手動切換模式,改用 `docker compose exec web`
  搭配 `OperationModeService` 直接下 `python -c` 並指定絕對路徑,不要直接跑
  `scripts/set_mode.py`),但下次一併修掉 `seed_sandbox.py` / `add_owner.py`
  的硬編碼路徑時,應該把這支腳本也一起改成讀 `settings.database_path`。

---

## 相關文件

- [architecture.md](architecture.md)
- [operation_modes.md](operation_modes.md)
- [limitations.md](limitations.md)
- [rebuild_sandbox.md](rebuild_sandbox.md)
