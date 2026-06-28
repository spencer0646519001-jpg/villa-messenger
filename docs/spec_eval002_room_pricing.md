# 实作规格:EVAL-002 开几房报价（按房数计价）

> 给 Codex 的实作依据。把 `eval_002_room_pricing_design.md`（规则定案）收敛成可落地 code。
> 前置：LLM 整合、半异步均已 commit。
> 工作流：Spencer 审 → Codex 实作 → Spencer commit。繁中 + 小学生版。
> 状态：LIST A 最后一个大功能项。这份 done → 真机验 → 上线。
> ⚠️ **动核心算法（pricing）+ 动大量既有测试，最敏感的一份。报错价影响生意，严格测试。**

---

## 0. 这份做什么（一句话）

报价从「人数自动决定级距」改成「**客人选的房数决定级距**」。报价前若没讲房数 → 先问；房数不够住人数 → 提示加房；1房/超16人/算不清 → 转管理者。**价格数字、季节判定、折扣/宠物费一律不改。**

**护城河**：房数抽取/问房数/检查 = 解析+流程；**价格怎么算 = 纯规则核心算法，LLM 不碰**。转管理者 = 现有 owner push。

---

## 1. pre-flight 现况锚点（实作核对行号）

- 报价主函数 `calculate_price`（`app/domain/pricing_policy.py:10`），回 `PricingResult`（`pricing_models.py:20`）。
- 现在用人数推级距：`guest_count_used = adult+child`（`pricing_policy.py:28`）→ `_resolve_tier(guest_count)`（`:90`，写死 ≤8/≤10/≤12/≤16）。
- 价目表在 tenant config `pricing.base_prices_per_night`（`config.json:83-105`），key = `8_people/10_people/12_people`，各五栏季节。
- **`room_policy` 已存在但 pricing 没用**（`config.json:54-79`）：`total_rooms=4, standard_capacity=12, max_capacity=16`，`room_opening_rules`（8人→2房、10人→3房、12人→4房、13-16人→4房+加床）。
- 加人费/宠物/折扣写死（`pricing_policy.py:64-66`）。季节判定 `_SUMMER_MONTHS={7,8}` + 优先序（`:133-146`）。春节日期 config（`:234`）。
- 报价文字 `房型:包棟` 固定（`reply_templates.py:104`）。
- `room_count` 完全不存在 parser/state/pricing。
- 缺字段检查 `compute_missing_fields`（`inquiry_completeness.py:18`，只看日期/人数/宠物）。
- `InquiryParseResult`（`parser_models.py:34`）无 room slot；`conversation_states`（`schema.sql:168`）无 room 栏；`log_payload_to_state_slots`（`:21`）无 room 映射。

---

## 2. 房数 → 价目表 key / 容量 映射（定案）

| 房数 | 价目表 key | 标准容量 | 可加床 |
|---|---|---|---|
| 2 房 | `8_people` | 8 人 | 否 |
| 3 房 | `10_people` | 10 人 | 否 |
| 4 房 | `12_people` | 12 人 | 是（→ 最多 16）|
| 1 房 | —（不报价，转管理者）| — | — |

- **此映射读 config 的 `room_policy.room_opening_rules`**（决策②：读 config 不写死）。Codex 把 `room_opening_rules` 接进 pricing：用 `rooms_opened` 反查 `max_people`（容量）与对应级距 key。
- 级距 key 与 `base_prices_per_night` 的 key 对齐（`8_people` 等命名维持不变，避免大改 config）。
- ⚠️ `room_opening_rules` 现有结构是「人数→房数」，实作要能「**房数→容量/级距**」反向查。若结构反查不便，Codex 可在 loader 层建一个房数→key/容量的映射表，仍以 room_policy 数字为准（不可与 config 数字矛盾）。

---

## 3. room_count slot 全链路（解析层）

新增 `room_count` 贯穿：

1. **规则抽取**（决策⑤：这次只规则，不接 LLM）：新增 `app/domain/room_count_parser.py`，保守正则认：
   ```
   \d+\s*房           # 3房、3 房
   \d+\s*間(房)?       # 三間、3間房
   [一二兩三四五六七八九十]\s*(房|間)(房)?   # 中文数字
   開\s*\d+            # 開4（后接房/间或单独）
   ```
   - 抽出整数 `room_count`；认不到 → None。
   - 保守起步；之后漏太多再放宽或接 LLM（架构已在）。
2. **InquiryParseResult**（`parser_models.py`）加 `room_count: int | None`。
3. **LLM output schema**：加 `room_count: int | None`（这次 LLM 不主动抽，但 schema 对齐，未来要接时不用再改；adapter 填 None 即可）。
4. **log_payload_to_state_slots**：加 `"room_count": payload.get("parsed_room_count")`。
5. **conversation_states schema**（`schema.sql`）加 `room_count INTEGER`；repo create/update kwargs 同步；`ConversationStateService` slot 处理同步。
6. **compute_missing_fields**：**不要**无条件把 room_count 加进 missing（见第 4 节，它的检查逻辑特殊，不是单纯「有没有值」）。

---

## 4. 报价前流程（流程层）

报价前的判断顺序（在 `InquiryService._handle_pricing` 之前 / `_handle_missing_info` 一带，及 composer `_missing_for_state` 对应）：

```
前提：已 quote-relevant、日期+人数齐（沿用现有 missing_fields 先挡日期/人数/宠物）

第 1 步：room_count 有值吗？
  无 → 回「问房数」模板（模板1），不报价
  有 → 进第 2 步

第 2 步：room_count 检查
  room_count == 1
      → 转管理者（owner push）+ 给客人「转人工」简短回复，不报价
  room_count 不在 {2,3,4}（如 5、0、负数）
      → 转管理者 + 简短回复
  room_count ∈ {2,3,4}：
      查该房数容量（第 2 节表）
      人数 ≤ 容量                → 正常报价（第 3 节 calculate_price）
      人数 > 容量：
          room_count==4 且 12 < 人数 ≤ 16  → 报价 + 加床费（满4房加床）
          其他（人数 > 容量且非满4房可加床场景，例 2房住10人）
                                  → 回「提示加房」模板（模板2），不报价
          人数 > 16                → 转管理者 + 简短回复
  任何算不清/异常                  → 转管理者 + 简短回复
```

**模板（决策③：规格给文字，Codex 建进 `reply_text.py`/`reply_templates.py`；LLM 不产）：**

- **模板1（问房数）**：
  `您好,請問您想開幾間房呢?(本館共 4 間房,4人房 2 間,2人房 2 間)`
- **模板2（提示加房）**：
  `N 位的話,X 間房可能住不下喔,建議開 Y 房,需要為您改成 Y 房報價嗎?`
  （N=人数、X=客人给的房数、Y=容纳 N 人所需最低房数；Y 由 room_policy 反查）
- **转管理者给客人的简短回复**（沿用既有「转人工」语气，与 owner push 并用）：
  `您的需求我們請民宿人員為您進一步確認,稍後回覆您。`
  （同时触发现有 owner push 通知 owner）

⚠️ **「问房数」与「提示加房」时机区分**：模板1 只在「没讲房数」出现（纯问，不带建议）；模板2 只在「讲了房数但不够住」出现（纠错带建议房数）。两者互斥。

**room_count 进 missing 的特殊性**：它不像日期/人数是「有没有值」，而是「有值后还要过容量检查」。所以**不要**简单塞进 `compute_missing_fields` 当第四个必填。建议：日期/人数/宠物先走现有 missing 检查；全齐后再做「room_count 专属检查」（上面第1-2步）。Codex 定位最自然的接法（可能在 `_handle_pricing` 入口加一段 room_count gate），回报实际位置。

---

## 5. 定价层改动（核心算法，最敏感）

`calculate_price`（决策①：加 `room_count` 参数，内部用它选 tier）：

```python
def calculate_price(*, checkin_date, checkout_date,
                    adult_count, child_count=0, infant_count=0, pet_count=0,
                    room_count: int,                      # ← 新增
                    tenant_pricing, tenant_special_dates=None,
                    room_policy: dict) -> PricingResult:  # ← 新增，传 room_policy
```

改动：
1. **tier 由 room_count 决定**（取代 `_resolve_tier(人数)`）：
   - `room_count` → 查 room_policy 得「级距 key + 标准容量」（2→8_people/8、3→10_people/10、4→12_people/12）。
   - 用该 key 查 `base_prices_per_night`，季节判定/每晚加总逻辑**完全不变**。
2. **加床费改绑「满4房且人数>12」**：
   - `room_count==4 且 guest_count_used > 12`：`extra_person_fee = 1000 * (guest_count_used - 12)`，最多到 16（>16 应在流程层第4节已挡，calculate_price 可防御性回 can_quote=False）。
   - `room_count < 4`：**不触发加床**（人数超容量的情况流程层已用「提示加房」挡掉，不会进到这里）。
3. **宠物费、连住折扣、季节判定、价目表数字**：**完全不动**。
4. `PricingResult` 可加 `room_count_used` 栏位（选填，方便报价文字与测试）。

**报价文字**（`reply_templates.py:104`）：`房型:包棟` → 改成依实际房数,例 `房型:開 4 間房` 或 `房數:4 間`。Codex 选用语,与既有风格一致。

---

## 6. ⚠️ 测试同步（这份最大工作量）

`tests/test_pricing_policy.py`：现有断言用「给人数 → 期望 tier/total」。改 calculate_price 签名后：
- 所有呼叫 `calculate_price` 的测试要补 `room_count` 参数。
- 断言逻辑从「人数决定 tier」改成「房数决定 tier」。
- **关键不变**：同样房数 + 季节，total 数字应与改前「对应人数」的 total 相同（因为价目表数字没改）。例：room_count=2 + 平日 = 9000（= 旧 8_people 平日）。用这点验证「改了路径但没改价格」。
- 新增：房数容量检查、满4房加床、提示加房不报价、1房/>16转管理者 的测试。

`tests/test_reply_templates.py`：`房型:包棟` 改实际房数 → 相关断言同步。加人费/小计/折扣数字断言**应不变**（逻辑没改）。

新增整合测试：`13人 7/28-29` → 先问房数（模板1）；答「开4房」→ 报价含加床（$18,000+$1,000=$19,000，与真机一致）。

**验收**：既有所有 pricing/quote/composer 测试重新全绿 + 新增房数测试。

---

## 7. 给 Codex 的回报点

1. **room_opening_rules 反查**：现有结构「人数→房数」，pricing 要「房数→容量/级距」。回报用反查还是建映射表，确认与 config 数字不矛盾。
2. **room_count gate 接入点**：报价前的房数检查最自然接在哪（`_handle_pricing` 入口？`compute_missing_fields` 之后另开一段？composer `_missing_for_state` 如何对应多轮）。回报实际位置。
3. **多轮 state**：room_count 进 conversation_states 后，多轮累积（客人先讲人数日期、下一轮才讲房数）能正确 update slot 并触发报价。确认 composer 路径也接上。
4. **calculate_price 改签名的波及面**：grep 所有呼叫点（inquiry_service、composer 两处），全部补 room_count + room_policy。回报有几处。
5. **转管理者机制**：确认沿用现有 owner push（`_send_owner_push`），1房/>16/算不清都走同一条，客人收到简短回复 + owner 收到通知。
6. **价格不变验证**：跑改前 vs 改后，同房数同季节 total 一致（证明只改路径没改价）。

---

## 8. 不在范围（防 scope creep）

- **不改任何价格数字**、不动季节判定/暑假月份/春节日期/宠物费/连住折扣逻辑。
- **不接 LLM 抽 room_count**（这次只规则；schema 对齐留接口，未来要接加 trigger 即可）。
- 房型偏好（4人房 vs 2人房 选择）：owner 确认只看房数，不处理。
- 「我講了」元抱怨：独立项。
- 路线一（收敛解析失败判定点）：上线后。

---

## 9. 完成后

- 真机重测 `13人 7/28-29`：这次应**先问房数**（模板1）→ 客人答「开4房」→ 报 $19,000（含加床）。
- 测「开2房住10人」→ 提示加房（模板2）。
- 测「1房」→ 转管理者。
- LIST A 剩：云端部署（graceful shutdown + LLM_TIMEOUT_SECONDS=12）、真帐号切换、最后真机验 → 上线。

## 10. 关联文件
- `eval_002_room_pricing_design.md`：规则定案（本规格的设计来源）。
- `docs/pricing_rules.md`：现有人数级距规则（实作后需更新成房数规则）。
- `docs/eval_cases.md`：EVAL-002 原始案例。