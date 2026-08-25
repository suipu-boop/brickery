# 引擎容错与降级策略测试（engine-fault-tolerance）

> 状态：已修订（2026-08-25，路由层「API 失败自动降级本地」已反转，见下）
> 首版：2026-08-22（test_engine_providers.py 新增 16 例 + test_engine_router.py 增强 5 例，共 33 例全过）
> 修订：2026-08-25 拍板「本地 GGUF 不做聊天/推理兜底」→ 路由层不再自动降级，
>       engine_router.py 的 complete/run_turn 只走用户显式选择的后端，失败直接上浮。

## 背景

引擎容错逻辑已在生产代码中（v0.1.0 已上线），但缺少针对断连/超时/限流的
自动化测试。本规格为测试补充设计。**2026-08-25 修订：路由层行为从「API 失败
自动降级本地」改为「直接上浮报错」**（本地小模型质量不足以承担对话/推理，
只做规划类幕后任务；用户显式选 backend=local 时才使用本地引擎）。

### 现有容错策略（生产代码，已确认）

| 场景 | 生产行为 | 位置 |
|------|---------|------|
| 网络断连（URLError） | `_request_json` 抛 RuntimeError（"无法连接 host"） | engine_providers.py |
| 超时（socket.timeout/TimeoutError） | 抛 RuntimeError（">timeout s 无响应"） | engine_providers.py |
| HTTP 401/403 | 抛 RuntimeError（鉴权失败） | engine_providers.py `_classify_http` |
| HTTP 429 | 抛 RuntimeError（限流/额度耗尽） | engine_providers.py `_classify_http` |
| HTTP 5xx | 抛 RuntimeError（服务端错误） | engine_providers.py `_classify_http` |
| 空内容响应 | `run_turn` 自动重试 1 次（attempt range(2)），仍空则抛 | engine_providers.py |
| API 失败 | **直接上浮报错（不降级本地）**（2026-08-25 拍板） | engine_router.py |
| 后端未配置 | 抛 NoEngineConfigured（零出站连接） | engine_router.py |
| 红线 | 不因单次失败死循环重试（网络类错误不重试；仅空内容重试 1 次） | loop.py / engine_providers.py |

## 测试设计

### 1. 新建 `brickery/runtime/tests/test_engine_providers.py`

覆盖 ApiEngine 层（mock urllib，零真实网络）：

- `_classify_http` 分类文案：401/403、429、5xx、其他 code
- `_request_json` 成功路径：返回解析后的 JSON
- `_request_json` 网络断连：`urllib.error.URLError` → RuntimeError 含"无法连接"
- `_request_json` 超时：`socket.timeout` → RuntimeError 含"超时"
- `_request_json` HTTP 429 → RuntimeError 含"限流"
- `_request_json` HTTP 401 → RuntimeError 含"鉴权"
- `_request_json` HTTP 500 → RuntimeError 含"服务端"
- `is_available`：url+key 齐全才 True，缺一 False
- `run_turn` 空内容重试：首次空内容 → 第二次成功返回结果；两次都空 → 抛"空内容"
- `run_turn` 网络错误不重试：URLError 直接抛（仅空内容触发重试）

### 2. 增强 `brickery/runtime/tests/test_engine_router.py`

（2026-08-25 修订：原「API 失败自动降级本地」用例已反转）

- API 断连（URLError 语义）→ 直接上浮报错，本地**不**被调用
- API 超时 → 直接上浮报错，本地**不**被调用
- API 限流（429）→ 直接上浮报错，本地**不**被调用
- 本地显式选择（backend=local）失败 → 直接上浮报错，API **不**被调用
- 调用单次即止：API 失败后不重试、不降级（complete 只调用一次）
- 本地 `is_available()=False` 时不影响 backend=api 正常调用

### 3. 验证

- `python -m unittest brickery.runtime.tests.test_engine_providers brickery.runtime.tests.test_engine_router -v` 全过
- 全量 `python -m unittest discover -s brickery/runtime/tests -v` 不回归
- CI（brickery 冒烟）绿

## 风险与注意

- 全部 mock 网络层，零出站连接（延续"零默认外连"专项约束）
- 路由层无自动降级后，API 故障直接暴露给用户：调用方（loop.py / chat_ui.py /
  ipc.py）均以 except Exception / NoEngineConfigured 宽捕获，展示错误不崩溃
