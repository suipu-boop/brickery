---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 1ff3ab34626ddcd667748776b4e29487_091b56269d6511f1a238525400e6dd8f
    ReservedCode1: DRWp7illFWFScrXKaPjvpReOdCVJX0W0CcOlal5teQv98BWroOOvRc2QKfTiaWf1TiQhJNcplnwYD4vE1EgUzyzfpmnkQ3q/ywz/IeQOz27FGTEFaYmG+qob0iS40A6GY7QP5hT1+hNMSHomOjpK9hxBRWZmoOFTY/a4Dl2CGIm42VrXGkl484tvo+A=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 1ff3ab34626ddcd667748776b4e29487_091b56269d6511f1a238525400e6dd8f
    ReservedCode2: DRWp7illFWFScrXKaPjvpReOdCVJX0W0CcOlal5teQv98BWroOOvRc2QKfTiaWf1TiQhJNcplnwYD4vE1EgUzyzfpmnkQ3q/ywz/IeQOz27FGTEFaYmG+qob0iS40A6GY7QP5hT1+hNMSHomOjpK9hxBRWZmoOFTY/a4Dl2CGIm42VrXGkl484tvo+A=
---

# 想法备忘：进度积木（progress brick）

> 状态：**想法记录，未评估未动工**
> 提出：2026-08-21（下载新版工坊前）
> 关联：工坊直连 GitHub 改造（specs/workbench-live-market.md）——按需拉取积木落盘等场景无进度反馈

## 痛点

agent 在使用过程中会有很多下载/上传操作，大部分没有进度条，用户只能干等，无法判断任务是否卡住、还剩多少。

典型场景：
- 工坊"按需拉取积木落盘"（fetch_bricks_online 逐个下载，19 块积木无进度反馈）
- agent 执行下载/上传类任务
- 批量任务、长耗时任务

## 想法

做出相关"小积木"，让 agent 直观展示进度：

1. 统一的进度积木（如 `progress` brick），agent 在下/上传、批量任务中调用，上报：阶段 / 百分比 / 速度 / 剩余量
2. 事件通道：复用 ipc 事件流，运行时发 `progress` 事件 → 前端工作台订阅渲染（WebSocket/SSE）
3. 前端展示：工作台状态栏/任务面板实时进度条，支持多任务并行、可取消
4. 兼容：无进度源的旧操作做"不确定进度"动画（indeterminate），有源则精确百分比

## 落地原则（沿用项目惯例）

- 先落 specs 设计文档（目标 / 接口契约 / 前端渲染 / 验收标准）供审阅，确认后再动工
- 核心改动前先审阅，push/Release 需单独确认
*（内容由AI生成，仅供参考）*
