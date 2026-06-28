# EVAL-002 设计：开几房报价（按房数计价）— 规则定案

> 接续 LLM 整合（已完成）。这是 LIST A「开几房」工作项。
> 状态：**规则全定，价目表用现有数字（不改价格）→ 下一步写 pricing 实作规格 → Codex**。
> pricing 是核心算法，报错价影响生意。
> 最后更新：2026-06-28（pre-flight 后定案）。工作流：Claude 写规格 → Spencer 审 → Codex → Spencer commit。繁中 + 小学生版。

---

## 0. 问题（真机重现）

客人发 `13人 7/28-29` → 系统**自己假设「包栋/全开」** → 直接报 $19,000，报价文字固定写「房型:包栋」。
问题不是算错（$18,000 四房暑假平日 + $1,000 加床 = $19,000 算法正确），而是**没问客人要开几房就替他假设**。owner 指出：人数一样，开不同房数价格不同，必须先问房数。

## 1. pre-flight 关键发现（现况，写规格的基础）

1. **`room_policy` 已存在于 config，但 pricing 没用它**（`config.json:54-79`）：已定义 8人→2房、10人→3房、12人→4房、13-16人→4房+加床。`total_rooms=4, standard_capacity=12, max_capacity=16`。
2. **价目表 key 是人数命名，本质即房数价**（`config.json:83-105`）：`8_people / 10_people / 12_people` 各含 weekday/saturday/summer_weekday/summer_saturday_or_holiday/spring_festival 五栏。→ **8_people=2房价、10_people=3房价、12_people=4房价**。
3. **现在算法 = 人数自动决定级距**（`pricing_policy.py:90 _resolve_tier`，写死 ≤8/≤10/≤12/≤16）。EVAL-002 要改成**客人选的房数决定级距**，人数退为「检查容量 + 建议最低房数」。
4. **`room_count` 目前完全不存在** parser/state/pricing。要新增。
5. 加人费/宠物费/连住折扣写死 code（`pricing_policy.py:64-66`）：`pet_fee=500*pet`、`long_stay_discount=(nights-1)*1000`、`total=room_subtotal+extra_person_fee+pet_fee-long_stay_discount`。
6. 季节判定写死（`_SUMMER_MONTHS={7,8}`，每晚各自分类，优先序 spring_festival > national_holiday > summer_saturday > summer > saturday > weekday）；春节日期在 config。
7. 报价文字固定 `房型:包棟`（`reply_templates.py:104`）。

## 2. 计价规则（定案）

- **主轴 = 客人选的房数**（不分 4人房/2人房，owner 确认只看房间数）。
- **房数 → 价目表 key**：
  - 2 房 → `8_people` 价
  - 3 房 → `10_people` 价
  - 4 房 → `12_people` 价
- **价格公式 = 房数价（查表）+ 加床费 + 宠物费 − 连住折扣**（沿用现有叠加，只把「人数决定级距」换成「房数决定级距」）。
- **加床**：仅「**开满 4 房**」时，用 2 间双人床加床，最多 +4 人（12→16），每超 1 人 +$1,000。非满 4 房不触发加床（人数超容量改走「提示加房」）。
- **房数范围**：2~4 房系统报价；**1 房 → 不报价，转管理者**。

## 3. 完整流程（定案）

```
第 1 步：客人讲房数了吗？（room_count slot 有没有值）
  没讲 → 问「请问要开几间房？」（用人数建议最低房数，例：10位建议至少3房）
  讲了 → 进第 2 步

第 2 步：检查「客人开的房数」够不够住「人数」
  房数容量 ≥ 人数        → 正常报价（第 3 步）
  房数容量 < 人数，但加房能解决（人数 ≤ 12 或可加床）
                         → 提示加房（例：2房住不下10人 → 建议开3房）
  人数 > 16（超 max_capacity）→ 简单回复 + 转管理者
  1 房 / 任何算不清的奇怪情况 → 简单回复 + 转管理者

第 3 步：算价 = 房数对到的价 + 加床费（仅满4房、13-16人，每超12的人×1000）
         + 宠物费 − 连住折扣（沿用现有）
```

**房数标准容量**（从 room_policy）：2房=8人、3房=10人、4房=12人（满4房可加床到16）。

**核心原则**：能明确算的就算（房数够住→报价、满4房超12→加床）；算不清/超范围（1房、>16人、矛盾情况）→ 简单回复 + 转管理者。**不硬用规则雕花。**（呼应整个专案护城河：有把握的才做，模糊地带交人工。）

**流程顺序关键**：先问房数（没讲才问）→ 再检查够不够住。「提示加房」只在「客人明确讲了房数、但与人数矛盾」时发生，不与「还没问房数」混淆。

## 4. 三层改动

1. **解析层**：抽 `room_count` slot。客人讲「开四房」「要三间」「3房」等 → 抓到；没讲 → 空。规则 parser 先试，认不出走既有 llm_fallback 兜底（新增 room_count 抽取，对齐既有 slot 机制）。
2. **流程层**：
   - 报价前检查 `room_count`：空 → 问房数模板（人数算最低房数当建议）。
   - 检查房数 vs 人数容量：不够但可加房 → 提示加房模板；1房/>16/算不清 → 转管理者（owner push）。
   - 接入点（pre-flight 定位）：`compute_missing_fields`、`InquiryService._handle_missing_info/_missing_info_reply`、composer `_missing_for_state`、`conversation_states` schema/repo/service slot、`log_payload_to_state_slots`、`InquiryParseResult`、LLM output schema。
3. **定价层（核心算法，LLM 不碰）**：`calculate_price` 改成吃 `room_count` 决定 tier（取代 `_resolve_tier(人数)`），房数→价目表 key 映射；加床改绑「满4房且人数>12」；报价文字「房型」改成实际房数（不再固定包栋）。

## 5. 护城河

- 「该不该问房数 / 客人讲了没 / 房数抽取」→ 解析 + 流程，可用规则 + LLM 兜底。
- 「价格怎么算」→ **纯规则、核心算法，LLM 绝不碰**。
- 转管理者 = **现有 owner push 机制**（owner 确认）。

## 6. 价目表 / 季节 — 用现有，不改数字

- 价目表数字沿用 `config.json` 现有 `pricing.base_prices_per_night`（8/10/12_people 五栏），**不改价格**。只是改「用房数选 key」。
- 季节判定、春节日期、暑假月份 {7,8}、加人费/宠物费/连住折扣 — **全部沿用现有 code/config，不动**。EVAL-002 只动「级距由谁决定（人数→房数）」+「加房数检查与问房数流程」+「报价文字房型」。

## 7. 现有测试会被影响（实作要同步）

- `tests/test_pricing_policy.py`：大量断言 `tier == "8_people"/"10_people"/"12_people"` + total 数字。改成房数决定 tier 后，这些测试的输入要从「给人数」改成「给房数」，或 calculate_price 签名加 room_count。Codex 要同步更新。
- `tests/test_reply_templates.py`：断言报价文字（加人费、小计、连住折扣）。「房型:包栋」改成实际房数会动到相关断言。
- ⚠️ pricing 改动风险高，**既有所有 pricing/quote 测试必须重新全绿**，且新增房数相关测试。

## 8. 待写：pricing 实作规格（下一步）

涵盖：
1. `room_count` slot：parser 抽取（规则 + llm_fallback）、InquiryParseResult、LLM output schema、log_payload_to_state_slots、conversation_states schema/repo/service、compute_missing_fields。
2. 问房数流程 + 房数vs人数容量检查 + 提示加房 + 转管理者（1房/>16/算不清）。
3. `calculate_price` 改房数决定 tier（房数→8/10/12_people key 映射）+ 加床绑满4房。
4. 报价文字房型改实际房数。
5. 测试同步 + 新增房数测试。
6. ⚠️ 不改任何价格数字、不动季节/折扣/宠物费逻辑。

## 9. 不在范围

- 「我講了」元抱怨侦测：独立项。
- 房型偏好（4人房 vs 2人房 选择）：owner 确认只看房数，不处理。
- 路线一（收敛解析失败判定点）：上线后。
- 改价目表数字 / 季节规则：不在此项。

## 10. 关联文件

- `llm_fallback_design_v2.md` / `llm_model_selection.md` / `spec_llm_integration.md`：LLM 整合（已完成）。
- `spec_webhook_half_async.md`：半异步（已 commit）。
- `docs/pricing_rules.md`：现有人数级距规则描述（pre-flight 指出与 code 有小落差：未列 national_holiday）。
- 原始 eval：`docs/eval_cases.md` EVAL-002。