# villa_messenger — LLM 导入设计决策(V2)

> 用途:跨 session 存档,记住今天敲定的设计,下次回来直接接「模型选型」。
> 状态:**设计已定,实作 prompt 未出**(等模型选型定案才收敛成给 Codex 的干净 prompt)。
> 最后更新:2026-06-22
> 工作流:Spencer 写规格/审 → Codex 实作 → Spencer commit。繁中 + 小学生版。

---

## 0. 上线目标(已变更)

- 原本 V1.5 规则版先上线 → **改成「LLM 接上 + 开几房报价解决,才上线」**。
- 理由:真机测试看到客人面对规则版僵硬补料流程会失去耐心,带这体验上线没意义。
- LLM 从 LIST B 提到 LIST A。

---

## 1. LLM 角色与护城河(红线,别再争)

- **角色 = C(下游兜底),LLM 当「格式翻译 + 意图判断兜底 parser」。**
- 规则 parser 先跑,认得出的标准格式照旧走、**零 LLM 成本**。
- 认不出的才丢 LLM,翻译成 slot,喂回既有 conversation_states。
- **红线:LLM 绝不碰核心算法**——报价计算、多轮状态机、FAQ、3 个 gate、pricing_policy 一律不动。
- **意图判断不是越界**(它是 LLM 的本职);碰算法才是越界。← Spencer 定调
- **LLM 的输出永远只进 slot 或讯号,绝不直接变成客人看到的文字。** 客人可见的每个字仍由 `reply_templates.py` 规则模板产出。

### 关键诊断(决定了 LLM 只补「解析」这一环)
- 真机「问了又忘、客人打『我講了』系统还重复问」,**根因是 parser 解析失败 → slot 喂不进去**,不是多轮记忆缺失。
- stage A/B/C 状态机是好的、verified live 过,只是「没东西可记」。
- 所以 LLM **不需接管对话流程(不需要 B)**,只需补「解析」这一环。

---

## 2. 接法:路线二(已定)

- **LLM 兜底挂在 `parse_inquiry` 最上游**,接入点 `log_payload_to_state_slots` 那条转换层。
- 规则 parser 跑完 → slot 空但疑似在讲 → 叫 LLM 补 slot → 填回 `InquiryParseResult` → **下游完全不动**。
- 路线二的核心好处:**LLM 的产物伪装成规则产物,沿用整条既有路径(含多租户隔离),下游不知道 LLM 存在。**
- **路线一(先收敛「解析失败判定点」再挂 LLM)→ 排到上线后做。先二后一。**
  - 现况:解析失败判定分散在 `missing_fields` / `_is_quote_relevant` / `_has_slot` 三处。

---

## 3. 今天 LLM 兜底要救的两类(范围已定)

| 类 | 案例 | LLM 的活 | Gate 1 触发条件 |
|---|---|---|---|
| **类一:日期格式翻译** | `7/28-29`、`28入住29退房` | 把简写翻成 slot | 日期 slot 缺 ∧ Gate 2 日期感命中 |
| **类二:裸日期意图判断** | `3/15入住3/17退房` | 判断「这则像不像订房」 | 日期齐全 ∧ `_is_quote_relevant()`=False |

- 两类**共用同一次 LLM 呼叫、同一套接线**,只是触发条件不同、输出栏位侧重不同。不是两套系统。
- **类二为什么用 LLM 不用放宽规则**:放宽规则(「有两个日期就开报价」)会①挖回 checkout collision 老坑(M1 修过)②分不出「3/15想订房」vs「合約3/15到期」(差别在脉络不在字面)。规则没脉络,救一条赔三条。LLM 判脉络才不误伤。
- **类二是纯增益**:LLM 判对就救;判错(说不是订房)最坏退回现状(non-inquiry owner push),不会比现在更糟。

---

## 4. 触发条件设计(完整)

### Gate 1(沿用现有产物,零成本)
统一入口 `llm_fallback_parse()` 内部先分流(互斥):
- `dates_complete = checkin_date 有 ∧ checkout_date 有`
- **类一闸门**:`not dates_complete` ∧ `_date_signal_present(raw_text)` 命中
- **类二闸门**:`dates_complete` ∧ `_is_quote_relevant()`=False(规则判非询价但带了完整日期 = 矛盾讯号)
- 都不符 → 不呼叫 LLM,原样返回(零成本)

### Gate 2:`_date_signal_present()` 日期感正则(类一用,保守版)
只补现有 `_DATE_PATTERN` 扫不到的形态,**命中任一即 True**:
```
S1  裸数字日期区间    \d{1,2}\s*/\s*\d{1,2}\s*[-~]\s*\d{1,2}      例: 7/28-29
S2  入退房挂裸数字    (?:入住|退房)\s*\d{1,2}(?!\s*(?:/|月))
                     |\d{1,2}\s*(?:入住|退房)                    例: 28入住29退房
S3  日数/晚数         \d{1,2}\s*(?:晚|夜|天|日遊|日游)            例: 住3晚
S4  相对/近期日期词    (?:下個?月|這個?月|下週|下周|本週|這週|週末|周末|連假|月底|月初|月中)
```
- **S4 已收窄**:拿掉 `暑假/寒假/春節/過年` 等大区间词。理由:大区间 LLM 也补不出确切 checkin,补出来反污染 slot。`暑假四人` 这类改由类二或既有 missing prompt 接。

### 护城河验收(标准格式必须不触发)
| 原文 | dates_complete | Gate 2 | 触发? | LLM 成本 |
|---|---|---|---|---|
| `5/12入住5/14退房` | ✅ | — | 否 | 0 |
| `6／14有房嗎` | checkin✅ checkout✗ | S 无命中 | 否 | 0 |
| `你好在嗎` | ✗ | 无命中 | 否 | 0 |
| `7/28-29` | ✗ | S1✅ | 类一 | 1 |
| `28入住29退房` | ✗ | S2✅ | 类一 | 1 |
| `3/15入住3/17退房` | ✅ | (类二闸门) | 类二 | 1 |

---

## 5. LLM 输出 schema

对齐 sched-mvp / pre-flight 那套 state slot,**加三个新栏位**:
```json
{
  "intent": "price|availability|booking|faq|other|unknown",
  "checkin_date":   "YYYY-MM-DD | null",
  "checkout_date":  "YYYY-MM-DD | null",
  "adult_count":    "int | null",
  "child_count":    "int | null",
  "infant_count":   "int | null",
  "pet_count":      "int | null",
  "has_pet":        "bool | null",
  "last_message_text": "string",

  "is_booking_intent":    "bool | null",      // 类二专用;类一可 null
  "needs_clarification":  "bool",             // case 2 用
  "clarification_reason": "date_range_too_broad | null"
}
```
- **无 `guest_count`**(对齐现有 state slot,stage C 用 adult+child 推回人数)。
- **schema 不含 tenant_id**(见第 7 节多租户)。

---

## 6. 三态分流:`_merge_llm_into_inquiry()`
```
case 1  能补出确切日期(checkin/checkout 非 null, needs_clarification=False)
        → 填回 InquiryParseResult 的日期/人数 slot
        → 类二额外:is_booking_intent=True → intent 升级 booking/price
          (让 _is_quote_relevant 之后回 True)
        → 下游照常走 quote-relevant

case 2  needs_clarification=True (reason=date_range_too_broad)
        → slot 不填(留 None)
        → InquiryParseResult 挂讯号 needs_clarification
        → composer/handle_missing_info 看到讯号 → 回既有模板的「请问哪几天?」追问
          (模板新增一句,LLM 不产文字 —— 守 (y) 路线)

case 3  LLM 判定不是日期 / 类二判 is_booking_intent=False
        → 原样返回,不动 slot、不升级 intent
        → 退回现状(类一:走 missing prompt;类二:non-inquiry owner push)
        → 纯增益,判错最坏=退回现况
```
- **case 2 追问句路线 = (y)**:LLM 只回讯号 `{needs_clarification, clarification_reason}`,追问句仍走 `reply_templates.py` 规则模板。LLM 不产客人可见文字。← Spencer 同意

---

## 7. 多租户(tenant_id)处理 — 已定

- **LLM 输出 schema 不加 tenant_id。** tenant_id 是安全边界(刚修过两个隔离漏洞),绝不该是 LLM 生成的栏位,否则把隔离漏洞从 code 层下放到模型层。
- **tenant_id 全程由 code 携带、code 验证**,LLM 碰都不该碰。
- 现有路径已正确分层:slot=「内容」(无 tenant_id),tenant_id 在 **repository 写入层**注入(`conversation_state_repository.py:103-129`),不在 slot 里。
- **LLM 兜底要兼容多租户,只需两处加输入参数(不动输出 schema):**
  1. `llm_fallback_parse(inquiry, raw_text, reference_year, *, tenant_id)` — 从 `inquiry_service` 插入点传入。
  2. `_call_llm(raw_text, reference_year, trigger, *, tenant_id)` — 用途=未来分租户模型选型/计费/限流;**不进 prompt、不进输出 schema**。
- 兼容确认:LLM 补的日期/人数 → 流进 `log_payload` → `log_payload_to_state_slots` 照常转换(本来就不处理 tenant_id)→ repository 照常带 tenant_id 写入。**整条既有路径(含隔离)不用改。**

---

## 8. 接线点(唯一插入点)

`app/services/inquiry_service.py`,`parse_inquiry()` 之后、`_is_quote_relevant()` 之前:
```python
inquiry = parse_inquiry(message.text)
inquiry = llm_fallback_parse(inquiry, message.text, reference_year=..., tenant_id=...)  # ← 新增
if not self._is_quote_relevant(inquiry):
    return self._handle_non_inquiry(message, inquiry)
```
- 新档:`app/domain/llm_fallback.py`(`llm_fallback_parse` / `_date_signal_present` / `_call_llm` / `_merge_llm_into_inquiry`)。

---

## 9. 今天**不做**(防 scope creep)

- 人数模糊讲法(`我們五個`/`一家四口`/`2+1`):现有 parser 人数覆盖够(`_TOTAL_GUESTS` 等),保守版不纳 Gate 2。
- 「我講了」元抱怨侦测:独立项(意图/情绪问题,parser 兜底接不到也不该接 → 挫折信号侦测/转人工)。
- 开几房 / room_count slot(EVAL-002):独立项,且含规则部分(「该不该问房数」偏 LLM、「价格计算」要在 pricing_policy 加 room_count slot 是规则,两块拆开)。
- 模型选型、prompt 实际内容、timeout/重试:**下一轮谈**。
- 路线一(收敛解析失败判定点):上线后。

---

## 10. 给 Codex 的待回报点(实作 prompt 出的时候带上)

1. `_is_quote_relevant` 接法:(a) 抽成 `parser_models.py` 纯函式两边共用,或 (b) `llm_fallback_parse` 多收 `is_quote_relevant: bool` 参数由 `inquiry_service` 算好传入。**倾向 (b)**,改动小。
2. case 2 澄清追问接在 `_handle_missing_info()`(`:223-246`)还是 composer?**倾向前者**(它本来就是缺料追问的家),Codex 定位后回报。
3. `InquiryParseResult` 加 `needs_clarification`/`clarification_reason` 栏位,确认不破坏现有序列化 / `log_payload` 转换。
4. `_call_llm()` 这轮只留 **介面 stub**(吃 raw_text + trigger + tenant_id,回第 5 节 schema),**不要这轮硬接某家 API**。async 与否、timeout 等模型选型定案再补。

---

## 11. 下次起点(回来要谈的)

### 主线:模型选型(本规格第 11 项,今天未谈)
1. 本地 vs API、哪家、成本、延迟。
2. **延迟 ↔ webhook 快回 200 的关联**:慢会触发刚修好的 #7 webhook 重送。这是选型硬约束。
3. tenant_id 在 `_call_llm` 的用途(要不要分租户用不同模型)正好在选型一并谈。
4. 选型定案 → `_call_llm()` stub 介面定形 → 才把本设计收敛成给 Codex 的**干净实作 prompt**(去掉讨论态,只留做什么/怎么接/验收)。

### eval
- `docs/eval_cases.md` 那三条真机案例当 eval 起点:
  - EVAL-001(简写日期 + 跨轮 + 元抱怨)→ 今天救「日期格式翻译」环节(类一)。
  - EVAL-002(开几房)→ 独立项。
  - EVAL-003(裸日期不带价格词)→ 今天救(类二,选 A+选项2)。

---

## 12. LIST A 现况(上线前)

- 已完成 commit:add_owner / 紀錄 / #6c 真机 / #7 webhook 去重 / 两个 tenant 隔离漏洞 + 重建 DB。
- 剩:**LLM 整合 + 开几房** / 云端部署 / 真帐号切换 / 最后真机验。

---

## 附:现有 parser 边界(pre-flight 实测,写 Gate 2 的金标准)

- 日期 `_DATE_PATTERN`(`date_parser.py:7-10`):认得 `5/12`、`05/12`、`5月12`、`5月12日`、全形(靠 NFKC)、两枚裸日期顺序当入住/退房、`入住`/`退房` 标签前后。**认不得**:`7/28-29`、`28入住29退房`、`7月底`、`下週六`、`8.1`、`0801`、`2026/8/1`。`8/1~8/3` 当前被两枚裸日期吃到。
- 人数(`guest_count_parser.py`):`_TOTAL_GUESTS` 前缀全 optional → **裸数字+人/位就吃**(`13人`、`4位`、`一共4位` 都认得)。`10大兩小`/`兩大一小`/`5位` 都认得。**认不得**:`我們五個`、`一家四口`、`2+1`、`夫妻+小孩`。→ 所以 Gate 2 几乎不用做人数侦测,主力是日期。
- intent(`inquiry_intent.py:4-27`):price=多少錢/價格/價錢/費用/報價;availability=還有房/有房/空房/可訂/有空;booking=訂房/預訂/保留;faq=可不可以/能不能/可以嗎/能嗎/嗎/?/?;other=請問/想問/詢問/問一下。**不命中就 non-inquiry**(`3/15入住3/17退房`、`13人 7/28-29`、`10大兩小` 都会掉进 non-inquiry)。
- 真机原始语料:**无**。只有 `docs/eval_cases.md` 三条 + 测试字串。DB(`data/homestay.db`)目前为空。