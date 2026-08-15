---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 1ff3ab34626ddcd667748776b4e29487_31d7168998ba11f18cca525400e6dd8f
    ReservedCode1: sykN2+oGHdxB7ObEbeFMOQMy48dksBQ7Ej3XQCMBDfKL4EfJIX24z0R08KImhNIRULizBfBOWbCzhJa/BgLh7saddOpWtNlzFlcNyT7EshGwry6HURn7oX45gJaG3YnnO5uDQGeK7lx36k1SICxo2u/YPk9FJqFL4dHCZitE17nE2/dTCzw1Ydv9SEw=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 1ff3ab34626ddcd667748776b4e29487_31d7168998ba11f18cca525400e6dd8f
    ReservedCode2: sykN2+oGHdxB7ObEbeFMOQMy48dksBQ7Ej3XQCMBDfKL4EfJIX24z0R08KImhNIRULizBfBOWbCzhJa/BgLh7saddOpWtNlzFlcNyT7EshGwry6HURn7oX45gJaG3YnnO5uDQGeK7lx36k1SICxo2u/YPk9FJqFL4dHCZitE17nE2/dTCzw1Ydv9SEw=
---

# 底座内核设计定稿（v1）

> 状态：已拍板，待实施
> 日期：2026-08-15
> 原则：Shadeling 是生成物蓝本；底座自包含"能跑起来"的一切；记忆系统是原创核心，写死进内核。

## 一、底座内核（写死进 runtime，不经过积木激活器）

开机即用，不依赖积木市场。以下能力直接作为底座固有模块：

| 模块 | 内容 |
|---|---|
| 引擎双后端 | engine-local（本地 GGUF，llama_cpp + Metal）+ engine-api（OpenAI 兼容网络端点） |
| 记忆系统 8 能力 | core（存档/召回/浮现）、portrait（画像）、fixed-core（固定核）、cluster（聚类）、cooccurrence（共现）、suggest（主动推送）、consolidation（夜间巩固）、smol（总结+语义找回） |
| 安装引导 | 八家 API 预设一键填 Key + 本地 GGUF 推荐选择与一键下载 + 验证；默认"网络 API 为主 + 本地 GGUF 兜底" |
| 聊天界面 | 本地 web 聊天界面，走引擎路由；版面照搬 Shadeling 形态（ChatView/CabinetView/DoctorView/GraphCanvasView），套工坊蓝图风 |
| 连接器框架 | 连接器基座（feishu/telegram 具体连接器走按需积木） |

### 记忆系统说明
- 归纳类任务（画像更新/聚类/巩固/总结/固定核智能槽）走归纳引擎 induction_backend，支持 api / local / auto 三模式
- local 模式用本地小模型（如 Qwen3.5-4B），推理不出本机，隐私优先
- 本地小模型推荐选择 + 一键下载是底座固有能力，随安装引导提供

## 二、积木分层（28 积木）

### 预置（出包默认带，7 个）
docwrite、scheduler、rules、doctor、backup-restore、meeting-minutes、visualize

### 按需（积木市场热插拔，10 个）
feishu、telegram、ax、browser、high-config-doc、code-quality-chain、multi-agent、mcp、memory-cabinet、vault

### 改造（1 个）
skill-library → 积木市场（brick-market）：管理功能积木的安装/卸载/热插拔

## 三、界面规范
- 全部套工坊蓝图风（安装引导、聊天界面、状态页），版面格式不变
- 聊天界面照搬 Shadeling 形态：聊天 / 记忆柜 / 医生 / 图谱画布

## 四、待动工项
1. produce.py：出包默认带预置 7 积木；底座内核自包含
2. ipc.py：启动扫描 home/bricks 激活按需积木（积木数非 0）；未配置引擎进安装引导
3. setup_wizard.py：安装引导（八家 API 预设 + 本地 GGUF 推荐下载 + 验证），写 config.json
4. chat_ui.py：本地 web 聊天界面（工坊蓝图风）
5. skill-library 改造为积木市场
6. 重出包到 /Applications，重打 DMG 到桌面验证

## 五、待确认遗留
- 记忆系统 8 能力写死进内核后，对应 memory-* 积木：彻底移除（能力归内核）还是保留为开关（默认开可关）——待用户拍板
*（内容由AI生成，仅供参考）*
