# 实作规格:LLM 兜底整合(provider 抽象 + DeepSeek/Qwen via OpenRouter)

> 给 Codex 的实作依据。把 `llm_fallback_design_v2.md`(兜底逻辑)、`llm_model_selection.md`(选型)收敛成可落地 code。
> 前置：`spec_webhook_half_async.md` 已 commit（webhook 半异步，pipeline 跑在背景 threadpool，LLM 有 ~1 分钟 reply token 预算）。
> 工作流：Spencer 审 → Codex 实作 → Spencer commit。繁中 + 小学生版。
> 状态：LIST A 最后一个大功能项。这份 done → 横评 → 上线前真机验。

---

## 0. 这份做什么（一句话）

在 `parse_inquiry` 之后、`_is_quote_relevant` 之前，插一个 LLM 兜底层：规则 parser 认不出的简写日期/裸日期意图，丢 LLM（DeepSeek 主力 / Qwen 备援，via OpenRouter）翻成 slot，填回 `InquiryParseResult`，下游不动。LLM 慢/失败一律退回规则（case 3）。**纯增益。**

**护城河（写进 module docstring，不准违反）**：LLM 只做模糊语意解析 + 意图判断，绝不碰报价/状态机/FAQ/gate/pricing。LLM 输出只进 slot 或讯号，绝不直接变成客人看到的文字。坏 JSON / 超时 / 例外一律退回规则结果。

---

## 1. 新增档案总览

```
app/domain/llm_fallback.py            # 入口 llm_fallback_parse + Gate + 三态分流 + _call_llm
app/domain/llm_provider.py            # LLMProvider Protocol + LLMOutput dataclass
app/adapters/llm/__init__.py
app/adapters/llm/openrouter_base.py   # OpenAI SDK + OpenRouter 共用基底
app/adapters/llm/deepseek_provider.py # DeepSeekProvider
app/adapters/llm/qwen_provider.py     # QwenProvider
app/adapters/llm/fake_provider.py     # FakeProvider（测试用，回固定 LLMOutput）
scripts/eval_llm_models.py            # 横评脚本（手动跑，真打 API）
tests/test_llm_fallback.py            # 单元测试（全用 FakeProvider，不打真 API）
```

修改：
```
app/services/inquiry_service.py       # 接线：parse_inquiry 后呼叫 llm_fallback_parse
app/domain/parser_models.py           # InquiryParseResult 加 needs_clarification / clarification_reason
requirements.txt                      # 加 openai
.env.example（或等价 config 范本）    # 加 OpenRouter 相关环境变数
```

---

## 2. LLMOutput schema 与 Protocol（`llm_provider.py`）

### LLMOutput（对齐 `llm_fallback_design_v2.md` 第 4 节，**无 tenant_id**）
```python
from dataclasses import dataclass

@dataclass
class LLMOutput:
    intent: str | None                 # price|availability|booking|faq|other|unknown
    checkin_date: str | None           # "YYYY-MM-DD"
    checkout_date: str | None
    adult_count: int | None
    child_count: int | None
    infant_count: int | None
    pet_count: int | None
    has_pet: bool | None
    last_message_text: str | None
    is_booking_intent: bool | None      # 类二专用；类一可 None
    needs_clarification: bool           # case 2 用
    clarification_reason: str | None    # "date_range_too_broad" | None
```
- **无 guest_count**（stage C 用 adult+child 推回）。
- **无 tenant_id**（安全边界，全程 code 携带；见 `llm_fallback_design_v2.md` 第 7 节）。

### Protocol
```python
from typing import Protocol

class LLMProvider(Protocol):
    def parse(self, *, raw_text: str, reference_year: int,
              trigger: str, tenant_id: int) -> LLMOutput | None:
        """回 LLMOutput；任何失败（超时/坏 JSON/例外）回 None，上层据此走 case 3。"""
        ...
```
- `trigger`：`"type_1_date_translation"` 或 `"type_2_intent_judgment"`，让 prompt 侧重不同。
- `tenant_id`：传入但**仅供未来分租户路由/计费/限流**，不进 prompt、不进输出。

---

## 3. provider adapter（OpenAI SDK + OpenRouter）

### 共用基底（`openrouter_base.py`）
- 用 `openai` SDK 的**同步** client（决策①：同步，不 async）：
  ```python
  from openai import OpenAI
  client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=<OPENROUTER_API_KEY>)
  ```
- 共用一个 `call_openrouter(*, model, system_prompt, user_text, timeout_s) -> str | None`：
  - `response_format={"type": "json_object"}`（决策③保险一：逼 JSON）。
  - `timeout=timeout_s`（决策⑦：5 秒）。
  - 任何例外（超时、网路、HTTP 错）→ `logger.warning` + 回 `None`（不抛）。
  - 回传 raw JSON 字串（还没 parse）。

### DeepSeekProvider / QwenProvider
- 各自带 model 字串（OpenRouter 命名，如 `deepseek/deepseek-chat`、`qwen/qwen-2.5-...`；**实际可用字串 Codex 去 OpenRouter 文件查最新，写进 config 注解**）。
- `parse()` 流程（两家共用逻辑，建议抽到基底）：
  1. 组 system prompt（见第 4 节）+ user_text（raw_text）。
  2. `call_openrouter(...)` 拿 raw JSON 字串；`None` → 直接回 `None`（走 case 3）。
  3. **决策③保险二**：`json.loads` 包 try/except；parse 失败 → `logger.warning` + 回 `None`。
  4. 把 dict 映射成 `LLMOutput`；缺栏位给 None；型别不对（如日期不是字串）→ 当该栏 None。
  5. **防污染**：日期栏位做基本格式验证（regex `^\d{4}-\d{2}-\d{2}$`），不符 → 该栏 None。回 `LLMOutput`。

### FakeProvider（`fake_provider.py`）
- 建构时吃一个预设 `LLMOutput`（或一个 `dict[str, LLMOutput]` 按 raw_text 映射），`parse()` 直接回。
- 单元测试用它注入，不打真 API（决策⑤）。

---

## 4. prompt 设计（写进 adapter；保守、零碎）

system prompt 要点（繁中写，因为客人讲繁中）：
- 角色：你是民宿订房讯息的「栏位抽取器」。只输出 JSON，不要任何解释。
- 任务：从客人讯息抽出入住/退房日期、人数（大人/小孩/婴儿）、宠物、意图。
- **日期一律输出 `YYYY-MM-DD`**；年份用传入的 `reference_year`（除非讯息明写年份）。
- 简写规则范例（few-shot，针对 type_1）：
  - `7/28-29` → checkin `2026-07-28`, checkout `2026-07-29`
  - `28入住29退房`（在已知月份脉络下补完；若无月份脉络无法确定 → 该栏 None）
- type_2（裸日期意图判断）：判断这则「像不像订房意图」→ `is_booking_intent` true/false。
  - 像订房（讲了入住退房日期、想询价）→ true
  - 只是提到日期但非订房（如「合約3/15到期」「上次3/15來過」）→ false
- **范围太大**（如「暑假」「下個月」补不出确切日期）→ `needs_clarification: true`, `clarification_reason: "date_range_too_broad"`，日期留 null（决策走 case 2）。
- 严格输出 JSON schema（列出第 2 节所有栏位）。无法判断的栏位填 null。
- **不要**自己生成给客人的回覆文字（守护城河：LLM 不产客人可见文字）。

---

## 5. 入口与分流（`llm_fallback.py`）

### `llm_fallback_parse`
```python
def llm_fallback_parse(inquiry, raw_text, *, reference_year, is_quote_relevant,
                       tenant_id, provider) -> InquiryParseResult:
    has_checkin = inquiry.checkin_date is not None
    has_checkout = inquiry.checkout_date is not None
    dates_complete = has_checkin and has_checkout

    # 类一闸门：日期缺 ∧ 日期感命中
    if (not dates_complete) and _date_signal_present(raw_text):
        trigger = "type_1_date_translation"
    # 类二闸门：日期齐全 ∧ 规则判非询价
    elif dates_complete and (not is_quote_relevant):
        trigger = "type_2_intent_judgment"
    else:
        return inquiry  # 不呼叫 LLM，零成本

    llm_out = provider.parse(raw_text=raw_text, reference_year=reference_year,
                             trigger=trigger, tenant_id=tenant_id)
    if llm_out is None:
        return inquiry  # case 3：LLM 失败/超时，原样退回规则结果

    return _merge_llm_into_inquiry(inquiry, llm_out, trigger)
```
- **决策①**：`provider.parse` 同步阻塞（已在背景 threadpool，OK）。
- **决策②**：`provider` 由上层依 config 注入（生产注入 DeepSeekProvider；测试注入 FakeProvider）。

### `_date_signal_present`（Gate 2 保守正则，照 `llm_fallback_design_v2.md` 第 4 节）
```
S1  \d{1,2}\s*/\s*\d{1,2}\s*[-~]\s*\d{1,2}              # 7/28-29
S2  (?:入住|退房)\s*\d{1,2}(?!\s*(?:/|月)) | \d{1,2}\s*(?:入住|退房)  # 28入住29退房
S3  \d{1,2}\s*(?:晚|夜|天|日遊|日游)                     # 住3晚
S4  (?:下個?月|這個?月|下週|下周|本週|這週|週末|周末|連假|月底|月初|月中)
# S4 已收窄，不含 暑假/寒假/春節/過年（大区间补不出确切日期）
```

### `_merge_llm_into_inquiry`（三态分流，照设计第 5 节）
```
case 1  llm_out 有确切日期 ∧ needs_clarification=False
        → 填回 inquiry.checkin_date/checkout_date（+人数若有）
        → 类二且 is_booking_intent=True → 升级 inquiry.intent（让下游 _is_quote_relevant 回 True）
        → return 升级后的 inquiry

case 2  llm_out.needs_clarification=True
        → slot 不填
        → 在 inquiry 挂 needs_clarification=True, clarification_reason 透传
        → return（下游 composer/handle_missing_info 看讯号回「请问哪几天」模板）

case 3  已在 llm_fallback_parse 处理（llm_out is None 时原样退回）。
        另：llm_out 非 None 但日期仍 null 且 is_booking_intent=False（类二判非订房）
        → 不动 slot、不升级、不挂讯号 → return inquiry（退回现状）
```
- **填回 inquiry 注意**：`InquiryParseResult` 是既有 dataclass，确认是否 frozen；若 frozen 用 `dataclasses.replace` 产新物件，别就地改。Codex 核对。

---

## 6. 接线（`inquiry_service.py`）

`handle_message` 内 `parse_inquiry` 之后：
```python
inquiry = parse_inquiry(message.text)
inquiry = llm_fallback_parse(
    inquiry, message.text,
    reference_year=<现有取得方式>,
    is_quote_relevant=self._is_quote_relevant(inquiry),  # 决策②(b)：算好传入
    tenant_id=message.tenant_id,
    provider=self._llm_provider,                          # 注入
)
if not self._is_quote_relevant(inquiry):                  # 重算（可能已升级）
    return self._handle_non_inquiry(message, inquiry)
```
- `is_quote_relevant` **先算一次传进去**（给类二闸门判断），LLM 升级后**再算一次**（line 89 那个判断）。两次都用现有 `_is_quote_relevant`，不动它。
- `self._llm_provider`：在 `_build_inquiry_service`（line 168 附近）依 config 建立并注入。config 决定 DeepSeek/Qwen/Fake。

### case 2 讯号下游处理
- `_handle_missing_info()`（`:223-246`）回 missing prompt 前，先看 `inquiry.needs_clarification`：
  - True 且 reason=date_range_too_broad → 回**范围澄清模板**（`reply_templates.py` 新增一句，如「請問您大約是哪幾天呢？例如 7/28入住、7/29退房」）。
  - False → 现有 missing prompt 照旧。
- **决策（y）守护城河**：澄清句来自模板，不是 LLM 生成。

---

## 7. config（环境变数，决策②）

```
OPENROUTER_API_KEY=sk-or-...
LLM_PROVIDER=deepseek          # deepseek | qwen | fake（决定生产主力，换模型改这行）
LLM_PRIMARY_MODEL=deepseek/deepseek-v4-flash   # OpenRouter model id（2026-06 查证，$0.14/$0.28）
LLM_TIMEOUT_SECONDS=5
LLM_ENABLED=true               # 总开关：false 时 llm_fallback_parse 直接原样返回（等于纯规则版，方便上线前 A/B 与回退）

# OpenRouter model id 参考（2026-06 查证；Codex 实作时确认仍可用即可，不必重查）：
#   DeepSeek 主力： deepseek/deepseek-v4-flash      （$0.14 / $0.28，本任务首选）
#   Qwen 横评对象： qwen/qwen3.6-flash              （$0.1875 / $1.125）
#                  或更省 qwen/qwen3.5-9b           （$0.10 / $0.15）
#   旗舰不需要（本任务太简单）：deepseek-v4-pro / qwen3.6-plus 都不用
```
- `LLM_ENABLED=false` 是安全阀：万一 LLM 出问题，一个开关回到纯规则版，不用回滚 code。
- provider 工厂：依 `LLM_PROVIDER` 建对应 adapter；`fake` 给测试/本地用。

---

## 8. 横评脚本（`scripts/eval_llm_models.py`，决策④）

- **放 `scripts/`，不进 pytest**（手动跑，真打 API）。
- 吃下面结构化期望（我提供，Codex 建成 fixture）：

```python
EVAL_CASES = [
    # EVAL-001 简写日期
    {"id": "001a", "text": "13人 7/28-29", "reference_year": 2026,
     "expect": {"checkin_date": "2026-07-28", "checkout_date": "2026-07-29"}},
    {"id": "001b", "text": "28入住29退房", "reference_year": 2026,
     "expect": {"checkin_date": "2026-05-28", "checkout_date": "2026-05-29"},
     "note": "无月份脉络时可能判 None；此期望假设 prompt 给了 5 月脉络。无脉络则接受 None+needs_clarification"},
    # EVAL-003 裸日期意图
    {"id": "003", "text": "3/15入住3/17退房", "reference_year": 2026,
     "expect": {"is_booking_intent": True, "checkin_date": "2026-03-15", "checkout_date": "2026-03-17"}},
    # 反例：非订房意图
    {"id": "neg1", "text": "合約3/15到期", "reference_year": 2026,
     "expect": {"is_booking_intent": False}},
    # 范围太大 → case 2
    {"id": "broad1", "text": "暑假大概想訂", "reference_year": 2026,
     "expect": {"needs_clarification": True, "clarification_reason": "date_range_too_broad"}},
]
```
- 脚本流程：对 `["deepseek/deepseek-v4-flash", "qwen/qwen3.6-flash"]` 每个 model，跑全部 case → 比对 `expect` 的每个 key 是否命中 → 印出表格「model × case 通过/失败」+ 总通过率 + 每次延迟。
- **判对**：只比对 `expect` 里列出的 key（其他栏位不管）。日期严格相等；bool 严格相等。
- 输出范例（印 console 即可，不用存档）：
  ```
  model              001a  001b  003   neg1  broad1  pass%   avg_latency
  deepseek/...        ✓     ✓     ✓     ✓     ✓      100%    1.2s
  qwen/...            ✓     ✗     ✓     ✓     ✓       80%    1.8s
  ```

---

## 9. 测试（`test_llm_fallback.py`，决策⑤：全 Fake，不打真 API）

必测：
1. **Gate 不触发零成本**：`5/12入住5/14退房`、`你好在嗎`、`6／14有房嗎` → provider.parse **不被呼叫**（用 mock 断言 call count = 0）。
2. **类一触发**：`7/28-29`、`28入住29退房` → 触发；FakeProvider 回正确日期 → inquiry 被填回正确 checkin/checkout。
3. **类二触发 + 升级**：`3/15入住3/17退房`（规则判非 quote-relevant）→ 触发；Fake 回 is_booking_intent=True + 日期 → inquiry.intent 升级 → 之后 `_is_quote_relevant` 回 True。
4. **case 2**：Fake 回 needs_clarification=True → inquiry 挂讯号 → （整合层）回范围澄清模板。
5. **case 3（失败退回）**：Fake.parse 回 None（模拟超时/坏 JSON）→ inquiry 原样返回，下游行为同纯规则版。
6. **类二判非订房**：Fake 回 is_booking_intent=False → 不升级、不动 slot。
7. **LLM_ENABLED=false**：llm_fallback_parse 直接原样返回，provider 不被呼叫。
8. **既有 789 测试全绿**（接线不破坏现有）。

provider 层另测（不打真 API，mock `call_openrouter` 回字串）：
9. 坏 JSON 字串 → provider.parse 回 None。
10. 日期格式不符 `YYYY-MM-DD` → 该栏被清成 None（防污染）。

---

## 10. 给 Codex 的回报点

1. **InquiryParseResult 是否 frozen**：决定填回用就地改还是 `dataclasses.replace`。确认不破坏现有序列化 / `log_payload` 转换（新增的 needs_clarification 要不要进 log_payload？建议**不进 state slot**，只在单轮决策用——确认 `log_payload_to_state_slots` 不受影响）。
2. **reference_year 现有取得方式**：`parse_inquiry` 用的 reference_year 从哪来，沿用同一个传给 llm_fallback_parse。
3. **OpenRouter model 字串**：规格第 7 节已填实际 id（`deepseek/deepseek-v4-flash`、`qwen/qwen3.6-flash`，2026-06 查证）。实作时到 openrouter.ai/models 确认仍可用即可，若已更名取当前对应款。
4. **openai SDK 版本**：requirements.txt 加 `openai`，确认版本支援 `response_format` json_object + `timeout` 参数 + 自订 base_url。
5. **provider 注入点**：`_build_inquiry_service` 加 provider 工厂；确认背景 threadpool 内建立 OpenAI client 没有 thread-safety 问题（建议每次请求建新 client，或确认 SDK client 可重用）。
6. **case 2 模板**：`reply_templates.py` 新增范围澄清句的位置与既有模板风格对齐，回报实际接在 `_handle_missing_info` 还是 composer（沿用半异步后的现状定位）。

---

## 11. 不在这次范围（防 scope creep）

- 「我講了」元抱怨侦测：独立项。
- 开几房 / room_count（EVAL-002）：独立项，含规则部分。
- 微调：上线后有真实语料再做。
- 自架 GPU 跑模型：上线后。
- 路线一（收敛解析失败判定点）：上线后。
- 第三、四家模型横评：config 可扩，需要时再加，不在本次。

---

## 12. 完成后

- 跑横评脚本 → 看 DeepSeek vs Qwen 在三条 eval 的繁中解析表现 → 决定生产主力（默认 DeepSeek，若 Qwen 明显更准改 config）。
- 真机验（媽媽當客人那套）：重测 EVAL-001 序列，确认 `7/28-29`、`28入住29退房` 现在能被解析、不再「问了又忘」。
- LIST A 剩：云端部署（记得 graceful shutdown）、真帐号切换、最后真机验 → 上线。
