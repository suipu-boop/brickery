"""外部平台连接器（扩展点）。

连接器框架进底座内核：本包 + runtime.gateway.GatewayRegistry 提供注册与生命周期管理。
具体连接器（飞书 / Telegram 等）为按需积木，由积木市场安装后提供实现并注册；
未装配时 ipc 侧惰性导入失败即优雅降级，不影响核心引擎。

默认不注册任何连接器。飞书连接器见 feishu 模块，OFF-by-default，
需 ~/.brickery/config/feishu.json 配置且 enabled=true 才拉起。
"""
