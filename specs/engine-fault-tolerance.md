# 引擎容错与降级策略测试（engine-fault-tolerance）

> 状态：已实施（2026-08-22，test_engine_providers.py 新增 16 例 + test_engine_router.py 增强 5 例，共 33 例全过）
> 日期：2026-08-22

## 背景

引擎容错逻辑已在生产代码中（v0.1.0 已上线），但缺少针对断连/超时/限流的
自动化测试。本规格为测试补充设计，不改变生产行为。

### 现有容错策略（生产代码，已确认）

| 场景 | 生产行为 | 位置 |
|------|---------|------|
| 网络断连（URLError） | `_request_json` 抛 RuntimeError（"无法连接 host"） | engine_providers.py |
| 超时（socket.timeout/TimeoutError） | 抛 RuntimeError（">timeout s 无响应"） | engine_providers.py |
| HTTP 401/403 | 抛 RuntimeError（鉴权失败） | engine_providers.py `_classify_http` |
| HTTP 429 | 抛 RuntimeError（限流/额度耗尽） | engine_providers.py `_classify_http` |
| HTTP 5xx | 抛 RuntimeError（服务端错误） | engine_providers.py `_classify_http` |
| 空内容响应 | `run_turn` 自动重试 1 次（attempt range(2)），仍空则抛 | engine_providers.py |
| API 失败 | 路由层自动降级本地 GGUF（条件化：本地 must 真可用） | engine_router.py |
| 双端失败 | 抛 NoEngineConfigured / 原异常 | engine_router.py |
| 红线 | 不因单次失败死循环重试（失败即降级，不重试网络类错误） | loop.py |

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

- API 断连（URLError 语义）→ 自动降级本地
- API 超时 → 自动降级本地
- API 限流（429）→ 自动降级本地
- 条件化降级闸门：本地 `is_available()=False` 时 API 失败**不**降级（抛错）
- 降级单次即止：API 失败后本地兜底成功，不再二次调用 API（不重试）

### 3. 验证

- `python -m unittest brickery.runtime.tests.test_engine_providers brickery.runtime.tests.test_engine_router -v` 全过
- 全量 `python -m unittest discover -s brickery/runtime/tests -v` 不回归
- CI（brickery 冒烟）绿

## 风险与注意

- 全部 mock 网络层，零出站连接（延续"零默认外连"专项约束）
- 不修改生产代码，仅新增/补充测试（若测试暴露行为与上表不符，则记录并单独评审）
