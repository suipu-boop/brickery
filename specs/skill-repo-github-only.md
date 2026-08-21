# 积木市场组件源改为 GitHub Only

## 背景

当前积木市场（SkillLibrary）源解析优先级为三级：

1. 用户显式设置的 `skill_repo_url`（设置页可配，落盘）
2. 包内 `fixtures/skill_repo`（file:// 本地离线源，produce 时打包进安装包）
3. 公网默认源 `raw.githubusercontent.com/suipu-boop/shadeling-bricks`

其中第 2 级本地源是 53c1f65 加的离线兜底，用于首启无缓存窗口避免依赖公网。
用户确认：工坊组合安装时组件应只从 GitHub 拉取，不需要本地离线源兜底。

## 改动方案（已确认执行）

### 1. `brickery/runtime/ipc.py` — `_resolve_skill_repo_url`

- 删除"用户自定义 skill_repo_url"与"包内 fixtures/skill_repo file://"两个分支；
- 解析逻辑简化为一行：固定返回公网 GitHub 默认源；
- `_h_config_set` 移除 skill_repo_url 写入分支；状态上报移除该字段。

### 2. `brickery/runtime/config.py`

- 删除 `skill_repo_url` 字段及 save/load 全部序列化逻辑（旧 config.json 中多余 key 会被忽略，兼容）。

### 3. `brickery/produce.py` — `_bundle_runtime`

- 移除打包 `fixtures/skill_repo` 到产物包的逻辑；
- 同步更新函数注释（不再携带离线市场源）。

## 决策点（已确认）

- 用户确认：**自定义源入口一并去掉，市场源纯 GitHub 直连**。
- 离线场景改走「积木包导入」通道（网页下载单块积木 → 传给智能体离线安装），另立方案。

## 影响

- 正式包不再含离线市场源，安装包体积略减小；
- 首次打开市场页若 GitHub 不可达，市场列表为空（可重试或配置自定义源），无本地兜底；
- 记忆柜走本地 cabinet.db，零网络依赖，不受影响。

## 验证

- 产物包内 `grep -r fixtures/skill_repo` 应无结果；
- 安装后市场页从公网 GitHub 正常加载 index.json。
