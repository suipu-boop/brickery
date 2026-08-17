"""§1 主循环（clean room）。

把「用户输入 → 上下文构建（含浮现记忆）→ 工具 / 技能筛选 → 引擎推理 → 结果落盘与记忆存档 → 回合后异步归纳」
串成一条可重入、可中断、出错不崩的主干。
红线：主循环不得直接写数据库（只经 MemorySystem）；不得在未存档成功前清空上下文；
不得因单次失败进入死循环重试。

本阶段已接入 §3.5 浮现引擎：
- 回合前：条件闸门（O2）决定是否向 prompt 注入相关记忆（否决每轮灌）。
- 回合后：fire-and-forget 异步调用影子模型归纳（O6），回填 entities/decisions/todos（O5）。
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from typing import Callable, List, Optional

from .engine_router import EngineRouter, NoEngineConfigured
from .skills import SkillRegistry
from .tools import (
    ToolRegistry, tool_to_schema, PermissionPolicy, AllowAllPolicy,
    ConfirmationGateway, AutoApproveGateway, AutoDenyGateway, RiskLevel,
    Mode, PlanPermissionPolicy, AcceptEditsGateway,
)
from .interoception import InteroceptionSystem

# Vault 能力提示：仅告知 agent 本地 Vault 工具的存在与适用场景，不注入任何用户数据。
# 与「纯按需、不常驻注入」设计一致；解决「agent 忽略 Vault」问题（联动 L1）。
VAULT_CAPABILITY_HINT = (
    "【本地资产 Vault 能力 · 始终可用】\n"
    "- 你拥有一个本地 Vault（用户个人资产库：证件/合同/图片/收藏网页/已装技能/笔记），数据在用户本机，不上云。\n"
    "- 当用户的问题涉及「他自己的」证件、资料、收藏、个人事实或已装能力时，先调用 vault_query 检索再作答，不要凭空编造。\n"
    "- 支持「提醒」：vault_query 设 upcoming_days>0 可返回未来 N 天内临近到期/生效的资产（如用户问「有什么快到期的」）。\n"
    "- 当对话中出现值得长期保存的内容（合同/证件信息/有用链接/用户说「记一下/存一下/收藏」），可提议调用 vault_save 帮用户存入 Vault；该操作为写入，会请用户确认，绝不强写。"
)


class AgentLoop:
    """主干循环：组装记忆 / 引擎 / 工具 / 技能，产出回复并落盘存档。"""

    def __init__(self, memory, engine_router: EngineRouter,
                 tools: Optional[ToolRegistry] = None,
                 skills: Optional[SkillRegistry] = None,
                 shadow_engine: Optional[object] = None,
                 session_id: Optional[str] = None,
                 history_window: int = 8,
                 should_stop: Optional[Callable[[], bool]] = None,
                 permission: Optional[PermissionPolicy] = None,
                 confirmation: Optional[ConfirmationGateway] = None,
                 mode: "Mode" = Mode.NORMAL,
                 max_tool_calls: int = 20,
                 tool_result_limit: int = 4000,
                 max_context_tokens: int = 8192,
                 context_window: int = 128_000,
                 rules: Optional[list] = None):
        self.memory = memory
        self.engine = engine_router
        self.tools = tools or ToolRegistry()
        self.skills = skills or SkillRegistry()
        self.shadow = shadow_engine  # ShadowEngine 实例（O5/O6 归纳用），可空
        self.history_window = history_window  # O7 历史窗口（硬上限，可配，默认 8）
        self.session = session_id or ("sess_" + uuid.uuid4().hex[:12])
        self._should_stop = should_stop
        self._last_active = time.time()  # 用于闲置时长计算（O2 长间隔闸门）
        # 执行模式（§4.4 / §5 模式切换）：决定默认权限闸门与确认网关。
        self.mode = mode if isinstance(mode, Mode) else Mode.from_str(mode)
        # §4.4 执行层权限闸门：默认随模式推导（headless/测试见下），也可显式覆盖。
        if permission is not None:
            self.permission = permission
        elif self.mode == Mode.PLAN:
            # Plan 模式：静态层即否决 MEDIUM/HIGH，只读思考。
            self.permission = PlanPermissionPolicy()
        else:
            self.permission = AllowAllPolicy()
        # §3.4 交互确认网关：MEDIUM/HIGH 风险工具在执行前阻塞等确认。
        # 默认随模式推导；Swift 真弹窗经 IPC 实现此接口（覆盖默认）。
        if confirmation is not None:
            self.confirmation = confirmation
        elif self.mode == Mode.PLAN:
            self.confirmation = AutoDenyGateway()       # Plan：高/中风险一律拒绝
        elif self.mode == Mode.ACCEPT_EDITS:
            self.confirmation = AcceptEditsGateway()    # 可信会话：MEDIUM 自动批准
        else:
            self.confirmation = AutoApproveGateway()    # 工作模式（现状）
        # 单轮工具调用硬上限（借 Cursor 的 20 次 cap，防失控循环 + 控 context 膨胀）。
        self.max_tool_calls = max_tool_calls
        # 工具结果回填前的截断上限（防长 stdout 撑爆 context）。
        # n_ctx 治理：结果上限不得超过上下文 token 预算，避免单条工具撑爆窗口。
        self.max_context_tokens = max(256, int(max_context_tokens))
        self.tool_result_limit = min(int(tool_result_limit), self.max_context_tokens)
        # 真实上下文窗口（API 通常 128K；本地 GGUF 受 n_ctx 限制）。
        # 仅用于内感受「上下文利用率」分母——与 max_context_tokens（n_ctx 治理预算）
        # 是两回事，不得混用，否则利用率虚高（坑⑦）。
        self.context_window = max(1, int(context_window))
        # §P2 持久规则（Hooks 轻量版）：始终注入 prompt 的「宪法」层指令。
        self.rules = list(rules) if rules else []
        # 本轮命中记录：供上层（IPC / UI）回传「这轮推了哪些工具与技能」。
        self.last_skills: List[str] = []
        self.last_tools: List[str] = []
        # 本轮工具执行明细（截断后），供测试断言与 UI 展示「干了什么」。
        self.last_tool_log: List[str] = []
        # §4.5 内感受系统：每回合采集自身运行状态（不阻塞主流程）
        self.intero = InteroceptionSystem()
        # 最近一次内感受状态（供阶段 II 浮现注入）；首轮为空，下一轮起可用
        self._intero_state = None
        self._tool_latencies: List[float] = []

    def _check_stop(self) -> None:
        if self._should_stop and self._should_stop():
            raise InterruptedError("主循环已被请求停止")

    def _format_memory(self, cands: List[dict]) -> str:
        if not cands:
            return ""
        lines = []
        for c in cands[:8]:
            # R2 修正：topic_summary 缺失时回退到原始摘要 raw_summary，避免摘要写空导致记忆静默丢失
            summary = c.get('topic_summary') or c.get('raw_summary') or ''
            line = f"- {summary}"
            ents = c.get('entities') or []
            if ents:
                line += f"（涉及：{', '.join(ents[:5])}）"
            lines.append(line)
        return "\n".join(lines)

    def _build_prompt(self, user_message: str, matched_skills,
                      history: Optional[list] = None,
                      memory_text: Optional[str] = None,
                      open_context_text: Optional[str] = None,
                      rules: Optional[list] = None,
                      intero_text: Optional[str] = None) -> str:
        """组装 prompt：固定核 + 持久规则 + 历史轮次 + 命中技能 + 浮现记忆 + 本轮输入。

        **块顺序遵循前缀缓存（prefix caching）原则**：把「每轮几乎不变、且随对话
        单调增长」的块（固定核 + 规则 + 历史）排在前面，「每轮变动」的块（技能/记忆）排在
        后面、紧贴本轮输入。这样跨轮调用时 `核+规则+历史_N` 是 `核+规则+历史_{N+1}` 的
        字节前缀，前缀缓存（DeepSeek 命中价约 1/50）才能复用到历史为止，而非被
        前排的变动块打断（坑⑥：原顺序把变动的技能/记忆排在稳定历史前，缓存失效）。

        history 形如 [{"role": "user"/"assistant", "text": "..."}]，按时间正序。
        为空时行为与单轮完全一致（保持既有契约）。
        """
        parts: List[str] = []
        rules = rules if rules is not None else self.rules
        # 0) 固定核（手动槽 + 智能槽，每轮稳定，排在规则前）
        core_text = self._core_text()
        if core_text:
            parts.append(core_text)
        # 1) 持久规则（每轮完全相同，最稳的前缀）
        if rules:
            parts.append("【持久规则 · 始终遵循】\n" +
                         "\n".join(f"- {r}" for r in rules))
        # 1.5) Vault 能力提示（稳定前缀，仅告知工具存在与何时用，不注入任何用户数据）
        parts.append(VAULT_CAPABILITY_HINT)
        # 2) 对话历史（稳定大块，随轮单调增长→天然可复用前缀）
        if history:
            lines = []
            for h in history[-self.history_window:]:
                role = h.get("role", "")
                text = (h.get("text") or "").strip()
                if not text:
                    continue
                who = "用户" if role == "user" else "助手"
                lines.append(f"{who}：{text}")
            if lines:
                parts.append("【对话历史】\n" + "\n".join(lines))
        # 3) 命中技能（每轮变动，紧贴输入以保注意力）
        # A+B 分级注入：优先注入轻量 summary（UI 同款目录），content 按上限截断
        # 防灌爆上下文；超长 content 提示可经技能面板手动触发获取完整内容。
        injections = []
        for s in matched_skills:
            block = self._skill_injection_text(s)
            if block:
                injections.append(block)
        if injections:
            parts.append("【上下文提示】\n" + "\n".join(injections))
        # 4) 浮现记忆（每轮变动）
        if memory_text:
            parts.append("【相关记忆】\n" + memory_text)
        # 4.5 开场上下文（新会话主动浮现，消灭失忆感；首轮后工具循环复用 first_prompt 不重算）
        if open_context_text:
            parts.append("【近期上下文 · 新会话开场】\n" + open_context_text)
        # 4.6 §4.5 II 浮现注入（O2 条件触发：仅显著偏离基线/连续恶化时注入，省 token）
        if intero_text:
            parts.append(
                "【内感受状态 · 仅作运行体感参考，不改变你的执行权限与确认逻辑】\n"
                + intero_text)
        if not parts:
            return user_message
        return "\n\n".join(parts) + f"\n\n{user_message}"

    # 命中技能注入 content 的单技能上限（字符）。超过则截断，避免单个技能灌爆上下文。
    SKILL_CONTENT_CAP = 2000

    def _core_text(self) -> str:
        """固定核注入（手动槽 + 智能槽），每轮稳定块。
        
        加载策略：惰性导入 fixed_core，避免 loop 模块的直接依赖。
        空核直接返回空字符串，不增加 prompt 开销。
        """
        try:
            from ..memory.fixed_core import get_all_core_text as _gact
            from ..memory.fixed_core import get_core as _get_core
            text = _gact()
            if not text:
                return ""
            # 身份引导：若用户在「认识我们」里给本 AI 起了名、或告知了称呼，
            # 在固定核开头固化一句，让模型明确自我称呼与对用户的称呼，增强亲和感。
            core = _get_core() or {}
            an = (core.get("assistant_name") or "").strip()
            un = (core.get("user_name") or "").strip()
            identities = []
            if an:
                identities.append(f"你叫{an}")
            if un:
                identities.append(f"用户名叫{un}")
            head = f"（{('、'.join(identities))}，请用这些称呼对话）\n" if identities else ""
            return f"【固定核】\n{head}{text}"
        except Exception:
            return ""

    def _skill_injection_text(self, s) -> str:
        """A+B 分级注入：返回该技能应注入 prompt 的文本。

        - 有 summary：以 summary 作轻量目录；content 短则附上，长则提示可手动触发。
        - 仅 content（无 summary，向后兼容旧技能）：按上限截断。
        用 getattr 防御缺失字段（测试桩 / 旧数据兼容）。
        """
        cap = self.SKILL_CONTENT_CAP
        summary = getattr(s, "summary", "") or ""
        content = getattr(s, "content", "") or ""
        if summary:
            block = summary
            if content:
                if len(content) <= cap:
                    block += "\n\n" + content
                else:
                    block += ("\n\n（完整内容较长已省略；可在「技能与工具」面板手动触发"
                              f"「{getattr(s, 'name', '')}」获取）")
            return block
        if content:
            if len(content) <= cap:
                return content
            return content[:cap] + "\n…(内容已截断)"
        return ""

    def _async_consolidate(self, texts: List[str]) -> None:
        """回合后异步归纳（O6 fire-and-forget）。出错静默，不阻断主流程。"""
        if self.shadow is None:
            return
        try:
            structured = self.shadow.consolidate(texts)
            self.memory.write_structured(self.session, **structured)
        except Exception:
            pass

    def run(self, user_message: str, project: str = "",
            history: Optional[list] = None,
            open_context_text: Optional[str] = None,
            on_token: Optional[Callable[[str], None]] = None) -> str:
        self._tool_latencies = []
        # 1. 先存档用户输入（保证已存档数据不丢，即使后续推理失败）
        self.memory.archive(self.session, [user_message], project=project)

        # 2. 可中断检查点（取消后已存档的输入保留）
        self._check_stop()

        # 3. 浮现注入（O2 条件触发）：先算闲置时长，再决定注入哪些记忆
        # 安全降级：memory.surface 失败或非 list（如测试桩）时不注入，不阻断主流程。
        memory_text = ""
        try:
            now = time.time()
            idle_seconds = max(0.0, now - self._last_active) if self._last_active else 0.0
            recent_history = [h.get("text", "") for h in (history or [])][-3:]
            surface_cands = self.memory.surface(
                user_message, project=project,
                recent_history=recent_history, idle_seconds=idle_seconds,
                shadow=self.shadow,
            )
            if isinstance(surface_cands, list):
                memory_text = self._format_memory(surface_cands)
        except Exception:
            memory_text = ""

        # 4. 上下文 + 技能/工具筛选（工具仅筛选，具体执行在闭环里按引擎指令进行）
        matched_skills = self.skills.match(user_message)
        selected_tools = self.tools.select(user_message)
        self.last_skills = [s.name for s in matched_skills]
        self.last_tools = [t.name for t in selected_tools]
        tool_schemas = [tool_to_schema(t) for t in selected_tools]
        self.last_tool_log = []

        # 5. 构建首轮 prompt（注入浮现记忆 + 历史轮次 + 本轮输入）
        first_prompt = self._build_prompt(user_message, matched_skills, history,
                                           memory_text=memory_text,
                                           open_context_text=open_context_text,
                                           rules=self.rules,
                                           intero_text=self._intero_prompt_block())

        # 6. Function-Calling 闭环：引擎 → 取 tool_call → 执行 → 截断回填 → 再 run，
        #    直到无 tool_call 或达 max_tool_calls。安全降级：无引擎/异常均不崩。
        reply = ""
        executed = 0
        last_usage = None  # 坑⑥ 修复：捕获真实 token 用量（取最后一轮=峰值上下文）
        try:
            turn_prompt = first_prompt
            while executed < self.max_tool_calls:
                # 流式：仅最终回复轮（无 tool_calls）会实时回调 on_token；
                # 工具轮引擎不外流 content（ApiEngine 已按 saw_tool_call 抑制）。
                # 旧引擎/测试桩不支持 stream 时 TypeError 降级为非流式，功能不退化。
                try:
                    result = self.engine.run_turn(
                        turn_prompt, tools=tool_schemas,
                        stream=True, on_token=on_token)
                except TypeError:
                    result = self.engine.run_turn(turn_prompt, tools=tool_schemas)
                if result.usage is not None:
                    last_usage = result.usage
                if not result.tool_calls:
                    reply = result.text or ""
                    break
                for tc in result.tool_calls:
                    if executed >= self.max_tool_calls:
                        break
                    executed += 1
                    tool = self.tools.get(tc.name)
                    # —— 权限闸门（§4.4）：执行前必须经策略裁决 ——
                    if not self.permission.authorize(tc, tool):
                        self.last_tools.append(tc.name)
                        self.last_tool_log.append(
                            f"[工具调用被权限策略拒绝：{tc.name}]")
                        continue
                    # 未知工具 / 无 handler：安全跳过（须在确认闸门前，避免 tool 为 None 崩）
                    if tool is None:
                        self.last_tools.append(tc.name)
                        self.last_tool_log.append(f"[工具不存在：{tc.name}]")
                        continue
                    if tool.handler is None:
                        self.last_tools.append(tc.name)
                        self.last_tool_log.append(
                            f"[工具 {tc.name} 无可执行实现（仅参与上下文筛选）]")
                        continue
                    # —— 交互确认闸门（§3.4）：MEDIUM/HIGH 风险工具阻塞等确认 ——
                    if tool.risk in (RiskLevel.MEDIUM, RiskLevel.HIGH):
                        if not self.confirmation.ask(tc, tool):
                            self.last_tools.append(tc.name)
                            self.last_tool_log.append(
                                f"[工具调用被用户拒绝：{tc.name}]")
                            continue
                    # —— 执行 handler（出错不拖垮主循环）——
                    t0 = time.perf_counter()
                    try:
                        raw = tool.handler(**(tc.arguments or {}))
                    except Exception as e:  # noqa: BLE001
                        raw = f"{type(e).__name__}: {e}"
                    self._tool_latencies.append((time.perf_counter() - t0) * 1000.0)
                    out = "" if raw is None else str(raw)
                    if len(out) > self.tool_result_limit:
                        dropped = len(out) - self.tool_result_limit
                        out = out[:self.tool_result_limit] + f"...[已截断 {dropped} 字]"
                    self.last_tools.append(tc.name)
                    self.last_tool_log.append(
                        f"调用 {tc.name}("
                        f"{json.dumps(tc.arguments, ensure_ascii=False)}) -> {out}")
                # 把工具执行结果回填，驱动下一轮推理给出最终回复
                turn_prompt = self._build_tool_round_prompt(first_prompt,
                                                            self.last_tool_log)
            else:
                # while 正常耗尽（达上限）才进入此处
                reply = (f"（工具调用已达上限 {self.max_tool_calls}，"
                         f"停止自动执行。）")
        except NoEngineConfigured as e:
            return f"抱歉，当前没有可用的推理后端。{e}"
        except InterruptedError:
            raise  # 取消指令不转成正常回复
        except Exception as e:
            return f"抱歉，本次推理出错了：{e}"

        # 6.5 清理可能泄漏的思考块（<think>…</think>），给用户干净回复
        reply = self._strip_think(reply)

        # 7. 存档回复（与本轮用户输入合并，幂等更新同一会话记录，保留完整轮次上下文）
        self.memory.archive(self.session, [user_message, reply], project=project)

        # 8. 回合后异步归纳（O6）+ 更新活跃时间
        self._last_active = time.time()
        # §4.5 内感受：回合后同步采集（<5ms），不阻塞主流程
        try:
            self._collect_interoception(user_message, reply, history,
                                        first_prompt, executed,
                                        usage=last_usage)
        except Exception:
            pass
        if self.shadow is not None:
            threading.Thread(
                target=self._async_consolidate,
                args=([user_message, reply],),
                daemon=True,
            ).start()

        return reply

    def _collect_interoception(self, user_message, reply, history,
                               first_prompt, executed, usage=None) -> None:
        """§4.5 采集本轮运行状态 → 内感受系统融合+持久化。出错静默。

        context_utilization 现在用**真实 token 用量**（来自引擎 usage 字段）做分子，
        分母用**真实上下文窗口**（self.context_window，API 通常 128K），不再用字符
        粗估（compute_token_estimate）除以 n_ctx 治理预算（8192）——否则 API 模式下
        第 2-3 轮就误报满负荷、且真有 5x 成本暴涨也抓不住（坑⑦）。
        """
        try:
            from .interoception import (
                TurnObservations, compute_token_estimate, ngram_repetition,
            )
            logs = self.last_tool_log
            total = max(1, len(logs))
            ERR_RE = re.compile(r"->\s*\w+(Error|Exception):")
            BAD = ("被权限策略拒绝", "被用户拒绝", "工具不存在", "无可执行实现")
            failed = sum(1 for l in logs
                         if any(k in l for k in BAD) or ERR_RE.search(l))
            internal_latency = (
                sum(self._tool_latencies) / len(self._tool_latencies)
                if self._tool_latencies else 0.0)
            # 真实优先：usage.prompt_tokens 是 API/本地引擎的精确计费口径；
            # 拿不到（如旧式引擎无 usage）才回退到字符粗估（低置信）。
            if usage is not None and usage.prompt_tokens > 0:
                ctx_tokens = usage.prompt_tokens
                ctx_source = "real"
                cache_hit = usage.cached_tokens
            else:
                ctx_tokens = compute_token_estimate(first_prompt)
                ctx_source = "estimate"
                cache_hit = 0
            util = min(1.0, ctx_tokens / max(1, self.context_window))
            rep = ngram_repetition(reply)
            obs = TurnObservations(
                tool_latency_internal=internal_latency,
                tool_failure_rate=failed / total,
                tool_retry_count=0.0,
                context_utilization=util,
                context_fragmentation=0.0,
                reasoning_depth=float(executed),
                reasoning_backtrack=0.0,
                output_repetition=rep,
                memory_retrieval_quality=None,
                # 坑⑥ 量化落点：原始 token 口径，供诊断/UI 直接展示真实用量
                context_tokens=ctx_tokens,
                context_token_source=ctx_source,
                prompt_cache_hit_tokens=cache_hit,
                context_window=self.context_window,
            )
            self._intero_state = self.intero.observe_and_update(obs)
        except Exception:
            pass

    def _intero_prompt_block(self) -> str:
        """§4.5 II 浮现注入文本：仅当 EmergenceDecision 判定应浮现时返回，
        O2 条件触发（省 token）。返回空串表示不注入。

        红线①遵守：文本仅作「运行体感参考」，明确声明不改变执行权限与确认逻辑，
        不触碰沙箱边界（自适应 IV 才做推理策略层降级，且锁死红线）。
        """
        st = self._intero_state
        if st is None:
            return ""
        try:
            from .interoception import EmergenceDecision, SensorReading
            readings = [
                SensorReading(
                    sensor_id=r.get("sensor_id", ""),
                    value=float(r.get("value", 0.0)),
                    baseline=float(r.get("baseline", 0.0)),
                    deviation=float(r.get("deviation", 0.0)),
                    confidence=float(r.get("confidence", 0.0)),
                )
                for r in (st.readings or [])
            ]
            if not EmergenceDecision.should_emerge(
                    st.state, st.trend, readings):
                return ""
            labels = {
                "cognitive_load": "认知负荷",
                "execution_friction": "执行阻力",
                "memory_coherence": "记忆一致性",
                "output_fluency": "输出流畅度",
                "overall_ease": "整体舒适度",
            }
            lines = []
            for k, lab in labels.items():
                v = st.state.get(k)
                if v is None:
                    continue
                t = st.trend.get(k, "stable")
                arrow = {"worsening": "↑", "improving": "↓",
                         "stable": "→"}.get(t, "→")
                lines.append(f"- {lab}：{v:.0%}（{arrow}）")
            if st.alerts:
                lines.append("预警：" + "；".join(st.alerts))
            return "\n".join(lines)
        except Exception:
            return ""

    def _build_tool_round_prompt(self, original_prompt: str,
                                  tool_log: List[str]) -> str:
        """把工具执行记录回填，作为下一轮推理的输入。"""
        log_text = "\n".join(tool_log)
        return (original_prompt +
                "\n\n【工具执行结果】\n" + log_text +
                "\n\n请基于以上工具结果，给用户一个完整、自然的回复。")

    @staticmethod
    def _strip_think(text: str) -> str:
        """去掉模型可能泄漏的 <think>…</think> 思考块，给用户干净回复。"""
        if not text:
            return text
        return re.sub(r"<think>.*?</think>", "", text,
                      flags=re.S | re.I).strip()
