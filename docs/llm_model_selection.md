# villa_messenger — LLM 模型选型决策

> 用途:记录模型选型定案,接续 `llm_fallback_design_v2.md`(兜底逻辑)与 `spec_webhook_half_async.md`(半异步已完成 commit)。
> 这份定案后 → 写 Codex 实作规格(provider 抽象 + 两家 adapter + _call_llm + eval 横评脚本)。
> 最后更新:2026-06-28
> 工作流:Claude 写规格 → Spencer 审 → Codex 实作 → Spencer commit。繁中 + 小学生版。

---

## 0. 选型背景与动机

- Spencer 上一个专案(sched-mvp / 排版系统)用 GPT-4o。这次想试**开源模型**,动机:
  1. AI applied engineer 的核心能力 = 模型选型 + 权衡效率/价格,开源是必修。
  2. profile 差异化(「会跑开源 + 评测选型」比「会调 GPT API」稀缺)。
  3. 中文理解:中国开源模型(Qwen/DeepSeek/GLM)中文强。
- **硬体现实**:Spencer 只有一台 DigitalOcean 主机(纯 CPU、无 GPU),个人电脑也不确定能跑。
  → **「自己跑模型」现阶段出局**(要 GPU、要钱、要维运)。
  → 改走**托管推理(serverless)**:用开源模型但不自养 GPU,像呼叫 API 一样用。

## 1. 关键观念厘清(写进 profile 叙事也用得上)

- **「用开源模型」≠「自己跑模型」**。两种方式:
  - (A) 自己部署:租 GPU、下权重、自架。最硬核、profile 最亮,但现阶段硬体/预算不支持 → **留到上线后**。
  - (B) 托管推理:平台帮你架好 GPU+模型,按用量付费。→ **现在走这条**。
- **微调留到上线后**:微调需要资料,但 DB 目前空、只有 eval 三条。上线 → 累积真实「解析失败」语料 → 再微调。这正好是完整 profile 故事:「先用开源模型 prompt 上线 → 用真实生产资料微调优化」。

## 2. 任务特性(决定不需要大模型)

- LLM 只做兜底 parser:把 `7/28-29`、`28入住29退房` 翻成 JSON slot + 判裸日期意图。
- 这是**分类/抽取级**任务,高容错(失败走 case 3 退回规则)。**不需要旗舰模型**,最便宜档绰绰有余。
- 预估量:单租户起步,一个月 LLM 成本可能 **< 1 美金**。

## 3. 三家横评结果(2026-06 查证)

| 维度 | DeepSeek | Qwen(阿里云) | Groq(跑开源) |
|---|---|---|---|
| 性质 | 开源 V4 系列 / API | Qwen3 Apache 2.0 开源 + 闭源旗舰 | 跑别人的开源 |
| 便宜档价格(每百万 token) | V4 Flash $0.14/$0.28 | Qwen-Flash 级 约 $0.05/$0.20 | Llama 3.1 8B $0.05/$0.08 |
| 延迟 | 中,中国机房尖峰偶有 503 | 中,有星马/东京/香港机房 | 极快(LPU 500+ t/s,亚秒首 token) |
| 繁中 | 极强 | 顶级(阿里云直营) | 看跑哪个模型 |
| JSON 输出 | 稳,OpenAI 相容端点 | 稳,OpenAI 相容 | 全模型支援 JSON mode |
| OpenAI 相容(抽换成本) | ✅ | ✅ 换 base_url+key 两行 | ✅ 改 base_url |
| 微调友好 | 一般 | 开源权重可自训 + 平台微调 | 不提供长期微调托管 |
| 注册门槛 | 低,送 500 万 token | ⚠️ DashScope 免费额度需中国手机号 | 低,免信用卡 |

**结论**:
- 速度王 Groq 的卖点(低延迟)对本案**边际效益低**——半异步已给 1 分钟预算,速度不再是瓶颈。
- 选型轴心从「快」转向「中文准 + 好上手 + 微调路径 + 横评友好」。

## 4. 选型定案

- **主力:DeepSeek V4 Flash**(上手快、送免费 token、中文强、JSON 稳、够便宜)。
- **并接:Qwen**(中文最顶、开源可微调,当横评对象 + 备援)。
- **一次接两家**,用 `docs/eval_cases.md` 三条真机案例**横评繁中解析准度**。
- **接入管道:OpenRouter**(定案,走 b 不走阿里云直连)。理由:
  - 一个 API key 接通 DeepSeek + Qwen(+ 未来 Llama/GLM 任意开源)。
  - 全 OpenAI 相容,provider 抽象层最薄。
  - **不碰中国手机号验证**(DashScope 国际注册门槛),今天就能开测。
  - 横评加第三、四家只改 config。
- **生产主力先用 DeepSeek**;横评结果若 Qwen 繁中明显更准 → 透过 config 一行把主力切 Qwen(这就是「可抽换」的实际价值展示)。

## 5. 架构:provider 抽象层(可抽换的核心)

- 所有 LLM 呼叫收敛在 `_call_llm()`,下游 `_merge_llm_into_inquiry` 只认固定 `LLMOutput` schema,**不知道背后是哪家模型**。
- 定 Protocol:
  ```python
  class LLMProvider(Protocol):
      def parse(self, *, raw_text, reference_year, trigger, tenant_id) -> LLMOutput: ...
  ```
- 每家一个 adapter 实作它(`DeepSeekProvider`、`QwenProvider`…),都走 OpenAI SDK + OpenRouter base_url,差别只在 model 名称。
- adapter 负责「把各家原始回应翻成统一 `LLMOutput`」,吸收差异。
- **config 驱动**:provider 选哪家、model 名、API key、timeout,全进 config / 环境变数,不写死。
  - 换模型 = 改 config,不改 code。
  - 多租户:未来不同租户用不同 provider = config 按 tenant 映射(呼应 tenant_id 在 _call_llm 的用途)。
  - 测试:注入 FakeProvider 回固定 JSON,不打真 API。

## 6. 横评 eval 脚本(纳入本次实作)

- 一支可重复跑的脚本:吃 `docs/eval_cases.md` 三条 → 分别打 DeepSeek / Qwen(via OpenRouter)→ 比对输出 slot 是否正确 → 出结果表。
- 这支脚本本身是 profile 展示品:「设计统一 eval harness 横评开源模型在繁中解析任务的表现」。
- 「接两家」的唯一意义就是横评;没有 eval 脚本,接两家只是多写一个没用上的 adapter。故纳入。

## 7. timeout / 失败处理(接半异步)

- 半异步已给 reply token 的 ~1 分钟预算,LLM 不再被 5 秒卡。
- `_call_llm` timeout 设 **5 秒**(预算内、留余裕),超时/失败 → 走 `llm_fallback_design_v2.md` 的 **case 3 退回规则**,不报错、不阻断。
- 纯增益:LLM 慢/挂,最坏退回现状。

## 8. 待办（下一步：写 Codex 实作规格）

实作规格要涵盖：
1. `LLMProvider` Protocol + `LLMOutput` dataclass（对齐 `llm_fallback_design_v2.md` 第 4 节 schema，无 tenant_id）。
2. `DeepSeekProvider` + `QwenProvider` 两个 adapter（OpenAI SDK + OpenRouter base_url + 各自 model 名）。
3. config 驱动选 provider（环境变数：OPENROUTER_API_KEY、主力 model、timeout）。
4. `_call_llm()` 实作（吃 raw_text/reference_year/trigger/tenant_id，回 LLMOutput，含 5 秒 timeout + 失败回 case 3 讯号）。
5. 接线：`llm_fallback_parse` 内呼叫 `_call_llm`，接入点 `inquiry_service.handle_message` 内 `parse_inquiry` 之后（现在跑在背景任务里）。
6. Gate 1 / Gate 2 / 三态分流（已在 `llm_fallback_design_v2.md` 定，照搬）。
7. 横评 eval 脚本（第 6 节）。
8. 新依赖：`openai`（SDK）。requirements.txt 要加。
9. 测试：FakeProvider 注入、Gate 触发/不触发、三态分流、timeout 走 case 3。

## 9. 横评实测结果（2026-06-28，via OpenRouter）

跑 `scripts/eval_llm_models.py`，5 条 eval 案例：

| model | 001a `7/28-29` | 001b `28入住29退房` | 003 裸日期意图 | neg1 非订房 | broad1 范围太大 | 通过率 | 延迟 |
|---|---|---|---|---|---|---|---|
| deepseek/deepseek-v4-flash | ✓ | ✗ | ✓ | ✓ | ✓ | 80% | 7.3s |
| qwen/qwen3.6-flash | ✓ | ✗ | ✓ | ✓ | ✓ | 80% | 9.7s |

- **两家通过率相同（80%）**。唯一 ✗ 是 001b，但那是 eval 期望设太严（`28入住29退房` 无月份脉络，模型判 None 是**正确行为**，实际走 case 2 澄清，非模型失败）。
- **核心案例 001a `7/28-29` 两家都过** —— EVAL-001 真机最痛的简写。
- **选型结论：生产主力维持 DeepSeek**。理由：通过率同 Qwen、延迟低 25%（7.3 vs 9.7s）、成本更低（$0.14/$0.28 vs Qwen flash 较贵）。Qwen 留作备援 + 横评纪录。
- profile 素材：「实测 DeepSeek vs Qwen 繁中日期解析，同等准确率，DeepSeek 延迟低 25%、成本低，选为生产主力」。

## 10. timeout 调整（实测后）

- 实测模型延迟 7~10s，原 `LLM_TIMEOUT_SECONDS=5` 太紧（会砍掉大部分呼叫）。
- 已改 **`LLM_TIMEOUT_SECONDS=12`**（本地 `.env`）。涵盖两家正常延迟 + 余裕，远低于 reply token ~1 分钟上限。
- ⚠️ **云端部署提醒**：DigitalOcean 环境变数也要设 `LLM_TIMEOUT_SECONDS=12`，别只改本地。

## 11. 真机验通过（2026-06-28）

- LINE 真机发 `13人 7/28-29` → 系统**一次解析成功**：入住 2026/07/28、退房 2026/07/29、13 人，直接报价，**不再「问了又忘」**。
- LLM 整合闭环完成：横评 001a ✓ → 真机 ✓。LIST A「LLM 整合」**已完成 commit**。

## 12. 关联文件

- `llm_fallback_design_v2.md`：兜底逻辑（两类、Gate 1/2、三态、tenant_id 分层）。
- `spec_webhook_half_async.md`：半异步骨架（已 commit，给 LLM 1 分钟延迟预算）。
- `spec_llm_integration.md`：LLM 整合实作规格（已 commit）。
- memory：云端部署需配 graceful shutdown timeout + `LLM_TIMEOUT_SECONDS=12`。