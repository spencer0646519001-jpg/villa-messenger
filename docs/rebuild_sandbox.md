# Sandbox DB 重建 Checklist

> 用途：當 `data/homestay.db` 需要砍掉重建時（例如漏洞 2 改 tenant_channels
> 唯一約束，因 `CREATE TABLE IF NOT EXISTS` 不會改既有 DB 的約束），照這張
> 便條把 sandbox 復原，**包含把媽媽妹妹的測試 owner userId 塞回去**。
>
> ⚠️ 這顆 DB 是測試/sandbox，裡面的 messages/inquiries/conversation_states
> 都是測試痕跡，砍掉無損。真帳號切換（LIST A 後段）會用「真帳號」的 userId，
> 跟下面這兩個「測試帳號」userId 不同。

最後更新：2026-06-21

---

## 測試帳號 owner userId（#6c 真機測試用）

從 2026-06-21 #6c 真機測試抓到，靠訊息內容認人確認：

- **媽媽**：`U412b0a3bac08db6b247fc618c03e6b99`
- **妹妹**：`Uca0af87426367f1929e4c31455454f32`

> 這是「測試帳號 villa messenger測試 (@817kxntu)」下的 userId，
> 跟正式帳號 (@013xipia) 的 userId 不通用。上線真帳號切換時要重抓。

---

## 重建步驟（repo root，PowerShell）

### 1. 備份舊 DB（保險，不直接刪）
Move-Item data\homestay.db data\homestay.db.bak -Force

### 2. 重建乾淨 DB（套用最新 schema.sql，含新約束）
villa
$env:PYTHONPATH="."
python scripts\seed_sandbox.py

### 3. 把媽媽妹妹塞回 tenant_owners
python scripts\add_owner.py add U412b0a3bac08db6b247fc618c03e6b99 媽媽
python scripts\add_owner.py add Uca0af87426367f1929e4c31455454f32 妹妹
python scripts\add_owner.py list

### 4. 確認新約束生效（漏洞 2 驗證）：查 tenant_channels DDL，UNIQUE 應為 (platform, channel_id)

### 5. 跑測試確認沒壞
ptq

---

## 確認復原無誤後，舊備份可刪
Remove-Item data\homestay.db.bak