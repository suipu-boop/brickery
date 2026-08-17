---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 1ff3ab34626ddcd667748776b4e29487_404797939a2811f19bec525400826444
    ReservedCode1: ekAbX6ehVl6jJBOHz6UtF8W31uHrbITWanDmutLRFf/VVdVvWNoLJxQfmd74c102jgDkqBVXQHn3hOaXOHV+eZYLJZFHCtdqZGbmGPoT6IXYMdOJKbqna47I24b0czEzbby4g98I9jxPsNPqlM/OdQc3i3QjbTVThmHR2xPUs0Bx9fk1BvqFKzHLnMg=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 1ff3ab34626ddcd667748776b4e29487_404797939a2811f19bec525400826444
    ReservedCode2: ekAbX6ehVl6jJBOHz6UtF8W31uHrbITWanDmutLRFf/VVdVvWNoLJxQfmd74c102jgDkqBVXQHn3hOaXOHV+eZYLJZFHCtdqZGbmGPoT6IXYMdOJKbqna47I24b0czEzbby4g98I9jxPsNPqlM/OdQc3i3QjbTVThmHR2xPUs0Bx9fk1BvqFKzHLnMg=
---

# 工作台界面重设计（workbench UI redesign）

> 目标：应对积木库上百+积木的布局可扩展性；组装区显式呈现"底座（推理引擎）"的存在。
> 改动范围：`web/index.html`（前端布局与交互）+ `brickery/web/server.py`（/api/bricks 返回 engine 底座数据）。
> 后端组装/产出链路（assembler.py / produce.py）无需改动。

## 一、现状问题

1. **积木库单列长列表**：左栏固定 340px，每个积木卡片头部堆叠 name/ver/risk/加入按钮/summary/标签，纵向占位大。上百个积木时滚动极长，分类折叠只能缓解，无快速定位手段。
2. **组装区不显示底座**：`/api/bricks` 过滤掉 `category=engine` 的积木（engine 为底座默认能力），用户组装时看不到"推理引擎"这一底座的存在，也不知道 agent 默认带什么内核能力。

## 二、底座（engine）数据现状

vault 中 engine 分类共 2 个积木，均无 requires、无冲突，可被 assemble 正常解析：

| 积木 | 说明 | 风险 | 资源 |
|------|------|------|------|
| engine-local | 本地 GGUF 推理（llama_cpp + Metal），推理不出本机 | low | 内存 4096MB / 磁盘 4096MB / 无网络 |
| engine-api | OpenAI 兼容网络推理端点，api_url/api_key 用户显式填写 | medium | 内存 16MB / 磁盘 1MB / 需联网 |

## 三、方案

### 3.1 积木库：分类折叠 + 网格卡片 + 分类导航

- **网格卡片**：`cat-body` 由单列改为 CSS grid（2 列，窄屏回退 1 列）。积木卡片改为紧凑模式：
  - 头部一行：name + risk 标签 + 加入按钮（summary 不再常驻，改为单行截断或移入详情）。
  - 点击卡片展开详情（保留现有 open 机制）。
- **分类导航条**：积木库顶部（搜索框下方）加一排分类 tab：`全部 / 基础 / tool / connector / binary / service / …`（动态取自数据）。点击过滤到该分类，解决长滚动找分类。
- **已加入筛选**：风险筛选旁加"已加入"按钮，只看已选中的积木。
- **全部折叠/展开**：分类头区域加"全部展开 / 全部折叠"快捷操作。
- **组装区**：保持纵向顺序列表（安装顺序是核心语义），chip 紧凑化（缩小内边距、依赖信息单行截断）。

### 3.2 组装区：显式展示底座（推理引擎）

- 组装区顶部新增**底座区块**（位于空态提示之上），虚线框 + "底座 · 推理引擎"标签，视觉上区别于普通积木，可折叠。
- 展示两个引擎选项卡片：`engine-local`（默认选中，低风险）与 `engine-api`（中风险），用户二选一。
- 选中结果作为 selected 的一部分传给 `/api/assemble` 与 `/api/produce`（engine 积木已在 vault 清单中，后端无需改动）。
- 底座区块始终可见（即使未选任何小积木），让用户明确"底座已就位"。

### 3.3 后端改动（最小）

- `/api/bricks` 增加返回 `engines` 字段：engine 分类积木的完整展示数据（name/summary/risk/resources 等），供前端底座区块渲染。普通 bricks 仍过滤 engine。

## 四、交互细节

- 分类 tab 与风险筛选、搜索可叠加（AND 关系）。
- 底座引擎切换后立即触发 `checkPlan()` 重新校验资源（engine-local 内存预算 4096MB 会显著影响合计，需让用户看到）。
- 空态文案更新：提示"底座已就位，从左侧添加积木开始拼装"。

## 五、验收标准

1. 积木库在 100+ 积木下：分类折叠 + 网格卡片 + 分类导航，滚动长度可控，无布局错乱。
2. 组装区顶部始终显示底座区块，可切换 engine-local / engine-api，切换后方案资源合计实时更新。
3. 产出链路：selected 含引擎时 assemble/produce 正常通过，产出 agent 包。
4. 窄屏（<1100px）回退单列，功能不缺失。
*（内容由AI生成，仅供参考）*
