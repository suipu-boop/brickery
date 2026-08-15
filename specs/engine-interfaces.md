---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 1ff3ab34626ddcd667748776b4e29487_2c1e17ea98c211f19bec525400826444
    ReservedCode1: ewjwCllRDg3TWButEtuRUf6SWRNu0ZMZpQjJX451OSDVQf0mFwth46DioqKhEzoiMNM9aV667JcPGWRhN+zSnrnEIr0ITtOveG4W0fudxqUPspdlgOG0dHqbDP4vWTlu2mJtUDPInwWKgW43AdkiiLzWpCiA+15ZjOiqQJDg0yCXQq3STW2WzUujkug=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 1ff3ab34626ddcd667748776b4e29487_2c1e17ea98c211f19bec525400826444
    ReservedCode2: ewjwCllRDg3TWButEtuRUf6SWRNu0ZMZpQjJX451OSDVQf0mFwth46DioqKhEzoiMNM9aV667JcPGWRhN+zSnrnEIr0ITtOveG4W0fudxqUPspdlgOG0dHqbDP4vWTlu2mJtUDPInwWKgW43AdkiiLzWpCiA+15ZjOiqQJDg0yCXQq3STW2WzUujkug=
---

# 引擎接口速查表（唯一事实源）

> 用途：写代码时只查本表，不翻源码。若源码接口变更，先更新本表。
> 建立：2026-08-15（根治"反复读同一文件"问题）

## config.py —— 配置读写

| 符号 | 签名 | 说明 |
|---|---|---|
| `EngineConfig` | dataclass | 推理引擎配置 |
| `EngineConfig.backend` | str = "api" | `api`（首选）/ `local`（兜底） |
| `EngineConfig.local_model` | str = "" | GGUF 文件名（相对 models_root）或绝对路径 |
| `EngineConfig.api_url` | str = "" | 仅用户显式填写时非空 |
| `EngineConfig.api_key` | str = "" | 仅用户显式填写时非空 |
| `EngineConfig.api_model` | str = "" | 模型名 |
| `EngineConfig.api_name` | str = "" | UI 显示名，不参与推理 |
| `Config` | dataclass | 顶层配置，含 `home` / `models_root` / `engine` |
| `Config.models_root` | Path | 本地模型根目录 |
| `Config.save()` | -> None | 写回磁盘 |
| `load_config(home=None, models_root=None)` | -> Config | 读取配置，缺省路径由 `paths.resolve_models_root()` 决定 |

## model_catalog.py —— 本地模型目录与下载

| 符号 | 签名 | 说明 |
|---|---|---|
| `GGUF_MODELS` | List[Dict] | 6 款推荐模型（qwen3.5-4b-q4 优先），默认 hf-mirror.com |
| `detect_ram_gb()` | -> float | 检测内存 |
| `detect_chip()` | -> str | 检测芯片 |
| `list_installed(models_root)` | -> List[Dict] | 已安装模型，元素含 `name` |
| `recommend_for_ram(ram_gb, coding=False, ...)` | -> ... | 按内存推荐 |
| `start_download(model_id, models_root, resume=False)` | -> Dict | 启动下载（分块+进度回调+超时） |
| `pause_download(model_id)` | -> Dict | 暂停 |
| `cancel_download(model_id)` | -> Dict | 取消 |
| `resume_download(model_id, models_root)` | -> Dict | 续传 |
| `delete_model_file(name, models_root)` | -> Dict | 删除模型文件 |
| `download_status(model_id)` | -> Dict | 查询状态，含 `active` 字段 |

## setup_wizard.py —— 安装引导（已落地）

- 本地 HTTP 服务 `127.0.0.1:18766`，`serve(host, port, daemon=True)` 启动
- 路由：`GET /`（引导页）、`GET /api/presets`、`GET /api/models`、`GET /api/config`、`POST /api/config`、`POST /api/verify`、`POST /api/download`
- 八家 API 预设：火山/腾讯/DeepSeek/通义/智谱/Kimi/OpenAI/xAI（URL 可编辑，Key 手填）
- 红线：不硬编码推理地址；本地模型仅用户主动触发下载

## 待办（未做完项）

1. `chat_ui.py` 聊天界面（本地 web，走引擎路由，工坊蓝图风）
2. `ipc.py` 积木激活（启动扫描 home/bricks 按形态激活注册进内核）
3. `produce.py` 全量/基础出包 + 重打 DMG
4. skill-library 改造为积木市场（brick-market）
*（内容由AI生成，仅供参考）*
