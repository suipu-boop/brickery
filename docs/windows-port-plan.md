---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 1ff3ab34626ddcd667748776b4e29487_36e64d0fb24311f19c7a525400de85a5
    ReservedCode1: +LhFWlU6IoUavSrO5oWJzokBcHckNmn9s15R770tLvjnJfl0rvxDALy65/K4RYbnVsrDCD501u961Chq5pFfVNbbfqPtSPkVMUpIEmq68NXO10zoc5817a2o2CYf0wdEU+JKQCZ0YVWAYUNyUVD2PgWUM18TkiNQiK5oW5vhGIKl4Kl0y0ZzS3GDKCE=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 1ff3ab34626ddcd667748776b4e29487_36e64d0fb24311f19c7a525400de85a5
    ReservedCode2: +LhFWlU6IoUavSrO5oWJzokBcHckNmn9s15R770tLvjnJfl0rvxDALy65/K4RYbnVsrDCD501u961Chq5pFfVNbbfqPtSPkVMUpIEmq68NXO10zoc5817a2o2CYf0wdEU+JKQCZ0YVWAYUNyUVD2PgWUM18TkiNQiK5oW5vhGIKl4Kl0y0ZzS3GDKCE=
---

# Windows 适配方案设计（brickery）

> 状态：**设计方案（仅设计，不实施）**
> 起草日期：2026-09-17
> 适用仓库：`/Users/suipu/Dev/brickery`
> 约束：不修改源码、不 commit、不 push；本文档为特批申请依据，待用户拍板后再进入实施。

---

## 一、目标与范围

### 1.1 目标

将 brickery 底座（macOS 原生 App 形态）在 Windows 上实现为**功能对等的产品形态**：用户双击启动后获得与本机 macOS 版本一致的体验（本地 Web 面板 + 聊天界面 + 安装引导 + 记忆/文件柜等持久化能力）。

### 1.2 核心原则

- **核心资产纯 Python，不做平台分叉**：`brickery/` 包内引擎、IPC 协议、HTTP 服务、记忆/文件柜/持久化、积木契约等逻辑全部复用，一行不改（或仅做平台分支收敛）。
- **仅适配层**：新增 `platform/` 平台适配层（首发 `win.py`）+ Windows 启动脚本（`run.bat` / `run.ps1`）+ 打包路线（exe），三者构成 Windows 专属薄层。
- **复用既有跨平台分支**：代码中已存在 `platform.system()` 三分支的样例（见 §3.4-07），适配层设计与其对齐。

### 1.3 范围边界

| 属于范围 | 不属于范围（冻结/历史） |
|---|---|
| assistant 三服务模式：ipc 18765 / setup_wizard 18766 / chat_ui 18767 | workbench 模式（web.server 8765，工坊已冻结归档） |
| 用户数据目录、日志、配置、记忆/文件柜数据库 | factory 模式（factory.server 8767，加工厂已冻结归档） |
| 平台系统调用替换（lsof/ps/osascript/sips/Keychain/sysctl 等） | `dmg.py` / Swift 壳 `app/` 的 Windows 重写（替代物见 §六） |
| exe 打包、安装器、签名白名单 | 22 积木分类冻结清单、DocWritePro 等冻结积木的平台化 |

---

## 二、冻结条款与特批申请

### 2.1 摘录说明

按任务要求全库扫描冻结文书。**本仓库无独立 `CHARTER` 文件，也无可直接打开的 `MARKETPLACE_BINARY_EXT.md` 文件**（该文档仅在 `brickery/runtime/skill_library.py:312` 注释中被引用："二进制扩展字段（高配技能，见 MARKETPLACE_BINARY_EXT.md）"，binary_url/binary_size/binary_sha256/binary_launch 字段契约已在 `skill_library.py:313-324` 落地；实际文书应位于 brick-vault 仓库或尚未归档）。以下条款原文摘录自 `specs/` 下现存冻结文书。

### 2.2 条款原文摘录（只摘录，未修改）

**A. `specs/brickery.md`**

> `:37` | G 加工厂（2026-09-17） | 冻结归档，不删历史；未来生态需要时解冻

**B. `specs/handoff-native-app-v2.md`**

> `:25` - 剔除：brickery-workbench（积木工坊，归档冻结）
>
> `:31` - **high-config-doc / DocWritePro**：冻结，不随 app 分发（运行时下载 193MB editor_sdk，违背简化方向）；内核实现保留不删
>
> `:41` - **M1 产品线瘦身**：归档 workbench/meta/factory 仓库（冻结不删历史）、shadeling-skill-repo 并入 brick-vault

**C. `specs/m1-product-line-slim.md`**

> `:41` | A2 | 本地归档 factory / meta / workbench | 每仓库打冻结 tag（`archive-2026-08-29`），整体移入 `~/Dev/archive/`（保留 .git 与全部历史），原路径放 README 指针 | 🟡 移动目录（可逆，历史保留） |
>
> `:43` | A4 | 冻结 skill-repo 目录 | 原目录移入 `~/Dev/archive/shadeling-skill-repo` | 低 |
>
> `:44` | A5 | brick-vault 冻结标记 | 新增 `FROZEN.md`（22 积木分类清单：保留活跃 / 冻结保留 / 冻结归档），不删任何目录 | 低 |
>
> `:45` | A6 | 产品线文档更新 | 在 brickery specs 记录归档清单、三核心说明、冻结清单 | 低 |
>
> `:47` ### 冻结清单（22 个积木分类）
>
> `:53` | 冻结保留（不删） | high-config-doc、demo-studio | 内核实现保留；demo-studio 仅开发期验证工具 |
>
> `:54` | 冻结归档（17 个） | ax / backup-restore / browser / code-quality-chain / doctor / engine-api / engine-local / feishu / hello-marvis / mcp / meeting-minutes / multi-agent / rules / scheduler / skill-library / telegram / visualize | 收进底座原生实现（M3 一次全收），vault 目录冻结不再维护 |
>
> `:59` 2. shadeling-skill-repo 技能源码已并入 brick-vault（差异核对记录可查），原目录冻结。

**D. `specs/native-webview.md`**（唯一出现"仅 macOS"字样的文档，为方案对比表，非严格冻结条款）

> `:57` | 跨平台 | 同一套 web UI 可复用 | 仅 macOS |

**E. `specs/product-line-simplify-native-v2.md`**

> `:51` | **brickery-workbench**（积木工坊） | 从产品线剔除，仓库归档冻结（不删除历史） |
>
> `:69` - **high-config-doc（DocWritePro）—— 冻结**：高配文档引擎积木，运行时下载 ~193MB editor_sdk 做复杂排版，违背简化/稳定方向，本轮不保留、不随 app 分发；内核实现（docwrite_pro.py / edsdk_pro.py）保留不删，留作将来评估。
>
> `:113` | M1 产品线瘦身 | 归档 workbench / meta / factory，明确三核心 | 产品线文档更新，仓库归档冻结 |
>
> `:123` 3. ~~brick-vault 其余积木目录是否冻结归档~~ → **已拍板（2026-08-29）：其余目录冻结归档（不删），底座只取必要能力做内置快照。**
>
> `:124` 4. ~~M1 归并动作~~ → **已拍板（2026-08-29）：workbench / meta / factory 归档冻结（不删历史）+ shadeling-skill-repo 并入 brick-vault。**

### 2.3 需特批解冻条目（方案仅为特批依据）

| 冻结条目 | 对 Windows 适配的意义 | 特批建议 |
|---|---|---|
| `brickery.md:37` 加工厂冻结（G） | 加工厂模式端口 8767 / `factory.server` 不进入 Windows 发布面；适配层不实现该模式，**无需解冻** | 不申请 |
| `handoff-native-app-v2.md:31` / `simplify:69` DocWritePro 冻结 | `edsdk_pro.py:39099` 引擎二进制管理（binary_manager）保持"运行时下载"机制；Windows 需验证 binary_url 是否提供 win 二进制（见 §7 风险 R-06） | 若 Windows 需要本地引擎二进制，需向用户申请**解冻 binary_url 平台矩阵**（新增 win 平台条目，不解除冻结本身） |
| `native-webview.md:57` "仅 macOS" | 方案对比表中的定性描述，非硬冻结；Windows 采用 WebView2 + 同一套 web UI，**不违反条款**（该条款指原生壳跨平台性弱） | 不申请，但需在拍板记录中明确引用此解释 |
| `MARKETPLACE_BINARY_EXT`（缺失文书） | 二进制分发的平台矩阵契约缺失；Windows 打包路线需补充 win 平台二进制约定 | 建议向用户确认文书实际所在仓库并补全 |

> 结论：**Windows 适配不需要解冻任何积木/仓库冻结**，仅需对"二进制分发平台矩阵"（binary_url）做一次特批扩展确认。

---

## 三、适配面清单

> 现状依据：全库 grep + 关键文件实读（`app/Sources/BrickeryApp/main.swift`、`brickery/runtime/{ipc,daemon,supervisor,paths,sandbox,setup_wizard,doc_tools,vault_store,model_catalog,chat_ui,binary_manager,skill_library}.py`、`brickery/{dmg,package}.py`）。

### 3.1 路径类

| # | 位置 | macOS 现状 | Windows 方案 |
|---|---|---|---|
| P-01 | `app/main.swift:16-18` | 用户数据目录硬编码 `~/Library/Application Support/<appName>` | `%LOCALAPPDATA%\<appName>`（由 `platform/win.py:user_data_dir()` 提供） |
| P-02 | `brickery/runtime/paths.py` | `BRICKERY_HOME` 派生，默认 `~/.brickery`；已环境变量化、跨平台安全 | **无需改**；Windows 默认同 `~/.brickery`，或由 launcher 注入 `BRICKERY_HOME=%LOCALAPPDATA%\Brickery` |
| P-03 | `brickery/runtime/sandbox.py:27-28` | 沙箱路径校验含 macOS 专属目录（/System、/Library、/Volumes） | 增加 Windows 专属目录集合（`C:\Windows\System32`、`Program Files`、`C:\` 根、`%APPDATA%` 等），按平台切换黑名单 |
| P-04 | `ppt_brick/demo_theme.py:8,22`、`demo_gen.py:19`、`demo.py:6,31`、`tools.py:136` | 硬编码 `/Users/suipu/Dev/brickery/output` 等开发示例路径 | 收敛为 `paths.get_output_dir()` / 相对路径（示例代码，低优先级） |
| P-05 | `ipc.py:3138`（`_ensure_agent_home`） | 首次启动从 `.app/Contents/Resources` 复制内置模板（bricks/ 等）到数据目录 | 打包后从 exe 资源目录（PyInstaller `_MEIPASS` / onefile 资源）复制，路径由 `platform/win.py:app_resources_dir()` 提供 |

### 3.2 启动类

| # | 位置 | macOS 现状 | Windows 方案 |
|---|---|---|---|
| S-01 | `app/main.swift:47-75` | Swift 壳 `Process` 托管 `python -m brickery.runtime.ipc --home <dataDir> --app-resources <Resources>`；内嵌 Python `Resources/python/bin/python3` | exe 内 PyInstaller 嵌入解释器直接 `runpy` 启动；或 launcher 用 `subprocess` 拉起 `brickery-runtime.exe`（见 §五、§六） |
| S-02 | `app/main.swift:88-99` | 三个服务按端口占用检查分别拉起（ipc/setup_wizard/chat_ui），日志重定向 `dataDir/<log>.log`；环境注入 `PYTHONPATH` / `BRICKERY_NO_WATCHDOG=1` / `BRICKERY_HOME` | `run.ps1` / `run.bat` 复刻同逻辑：查端口 → 拉起 → 日志重定向；`BRICKERY_NO_WATCHDOG=1` 语义保留（launcher 退出后 IPC 独立存活） |
| S-03 | `ipc.py:3162-3258`（main） | `faulthandler.register(SIGUSR1)`；ppid watchdog（父退出自杀）；SIGTERM/SIGINT 优雅停止 | Windows 无 SIGUSR1：`try: faulthandler.register(SIGUSR1) except (ValueError, OSError)`；watchdog 改用 Job Object 或轮询 `os.getppid()` 兜底（见 §7 R-02） |
| S-04 | `daemon.py:96-115` | 后台线程 + `daemon.status`（pid/state）+ `os.kill(pid,0)` 探活 + SIGTERM/SIGINT 优雅停止 | 逻辑本身跨平台；仅 `signal.signal` 注册需 Windows 语义核对（SIGTERM 在 Windows 上可注册但触发有限，daemon 常驻于 ipc 进程内不受影响） |
| S-05 | `supervisor.py:200-230` | `subprocess.Popen([python, -m, runtime.ipc, --port, ...])` + stdout 泵日志 + ppid 自杀 | Popen 跨平台；父进程死亡检测在 Windows 上 `getppid()` 语义不同，改为 Job Object 或心跳文件（见 §7 R-02） |
| S-06 | `scripts/check_alignment.sh` | 18765 探活 shell 脚本 | Windows 等价：`run.ps1` 内 `Test-NetConnection 127.0.0.1 -Port 18765` 或保留 bash（Git Bash / WSL），不强制 |

### 3.3 打包类

| # | 位置 | macOS 现状 | Windows 方案 |
|---|---|---|---|
| B-01 | `brickery/dmg.py` | 整体为 `.app` bundle + DMG 打包；/System/Library/Fonts、`pkill -f`、Applications 软链 | 废弃 macOS 专属步骤；exe 打包见 §六（PyInstaller / Nuitka + NSIS/Inno Setup） |
| B-02 | `pyproject.toml` | setuptools，`name=brickery`，`requires-python>=3.9`，`dependencies=[]`（零依赖红线），`packages.find` 含 `brickery*` | 保持零依赖红线：打包工具仅在构建机存在，不进 runtime `dependencies`；如引入 Pillow/psutil 等须先特批（见 §7 R-03/R-04） |
| B-03 | `app/`（Swift 壳） | WKWebView + NSApplication；菜单、JS 对话框桥接、文件选择面板 | Windows 替换为 WebView2（`Microsoft.Web.WebView2`，Edge 内核，Win10/11 自带或运行时安装）；JS 对话框/文件选择由 WebView2 默认或 `CoreWebView2` 事件桥接 |

### 3.4 二进制 / 系统调用类

| # | 位置 | macOS 现状 | Windows 方案 |
|---|---|---|---|
| C-01 | `ipc.py:468-484` | 端口占用清理：`lsof -tiTCP:<port>` + `ps -p <pid> -o command=` + `os.kill(pid, 9)` | `netstat -ano \| findstr :<port>` + PowerShell `Get-CimInstance Win32_Process -Filter "ProcessId=<pid>"` 校验 + `taskkill /F /PID <pid>`；收敛到 `platform/win.py:kill_pid_on_port()` |
| C-02 | `ipc.py:2927-2930` | 打开文件夹：`platform.system()` 三分支 `open / explorer / xdg-open`（**已有 Windows 分支**） | 直接复用；作为 `platform/win.py:reveal_in_explorer()` 或保持内联 |
| C-03 | `setup_wizard.py:738-741` | `osascript choose folder` 原生文件夹选择 | 方案 A：web UI 内 `<input type="file" webkitdirectory>`；方案 B：PowerShell `FolderBrowserDialog`；方案 C：tkinter `filedialog.askdirectory`（随 Python 分发，零依赖） |
| C-04 | `doc_tools.py:32,371` | 图片处理调用 `/usr/bin/sips` | 无系统等价物：Pillow（新增依赖，需特批）或 PowerShell `System.Drawing`（零依赖但能力弱） |
| C-05 | `vault_store.py:66-116` | 密钥链 `security add/find-generic-password` + `openssl enc` | `openssl` 不随 Windows 分发：密钥用 Windows Credential Manager / DPAPI（`ctypes` 调 `CryptProtectData`）；文件加密改 DPAPI 或引入 `cryptography`（特批） |
| C-06 | `model_catalog.py:130-156` | 物理内存 `sysctl hw.memsize`；CPU `sysctl machdep.cpu.brand_string` | `ctypes` `GlobalMemoryStatusEx` / `GetSystemInfo`（零依赖，收敛进 `platform/win.py:mem_gb()/cpu_brand()`） |
| C-07 | `app/main.swift:50-55` | 端口探测 `/usr/sbin/lsof -iTCP:<port> -sTCP:LISTEN` | `platform/win.py:port_in_use(port)`：`netstat -ano` 或 PowerShell `Get-NetTCPConnection -LocalPort` |
| C-08 | `binary_manager.py` / `skill_library.py:313-324` | 引擎二进制 `binary_url` 下载，端口 39099（edsdk_pro） | 契约字段跨平台无关；需确认 binary_url 平台矩阵是否含 Windows 产物（见 §7 R-06）；下载/拉起逻辑本身跨平台 |
| C-09 | `self_update.py:109` | 更新子进程（subprocess 下载/执行） | 命令细节待实施时核对；Windows 需确认执行器路径与 UAC 提权策略 |
| C-10 | `gateway/connectors`（feishu/telegram） | websocket-client 网关连接器 | 纯网络库，跨平台；无需适配 |

### 3.5 适配点统计

- **合计适配点：27 处**（路径 5、启动 6、打包 3、二进制/系统调用 10、文档/脚本 3 含 check_alignment 与缺失文书）
- 其中 **0 处需要改动核心引擎逻辑**（`engine`/`memory`/`filing`/`cabinet` 等纯 Python 数据层全部不动）；改动集中在适配层新增 + 6 处系统调用分支收敛。

---

## 四、platform/win.py 接口设计

### 4.1 模块结构

```
brickery/platform/            # 新目录（纯新增，不改动既有包）
  __init__.py                 # detect() -> "windows" | "darwin" | "linux"；get_platform_module()
  win.py                      # Windows 实现（本文档定义）
  _posix.py                   # （预留）macOS/Linux 既有行为收敛位，本期不实现
```

> 设计取舍：macOS 既有代码**维持内联现状**，不强制迁移到 `platform/`；`win.py` 只被 Windows 路径调用。待 macOS 侧重构时可逐步收敛，避免本期大改。

### 4.2 函数签名清单（win.py）

```python
# —— 路径映射 ——
def user_data_dir(app_name: str) -> Path
    """%LOCALAPPDATA%\\<app_name>；等价 macOS ~/Library/Application Support/<app_name>"""

def app_resources_dir() -> Path
    """PyInstaller 资源根：sys._MEIPASS（onefile）或 exe 同目录 resources/（onedir）"""

def legacy_path_to_windows(path: str) -> str
    """把用户配置/蓝图中历史 macOS 路径（/Users/... 等）转换为 Windows 路径；转换不了则原样返回"""

# —— 进程管理 ——
def port_in_use(port: int) -> bool
    """netstat -ano 检查端口是否有 LISTEN 占用"""

def kill_pid_on_port(port: int, *, verify_cmd_marker: str = "brickery") -> int
    """找到占用端口的 pid，校验命令行含 marker 后 taskkill /F；返回杀掉的 pid 数（0 表示无/拒绝）"""

def pid_alive(pid: int) -> bool
    """进程存活检查（替代 os.kill(pid, 0) 在 Windows 上的不可靠场景）"""

def terminate_tree(pid: int, timeout: float = 3.0) -> None
    """结束进程树（taskkill /T /F），用于 stop 阶段清理后端"""

def launch_detached(args: list[str], log_path: Path, env: dict) -> int
    """detached 拉起子进程并重定向日志；等价 Swift Process.launch + BRICKERY_NO_WATCHDOG 语义"""

def parent_pid_watchdog(original_parent: int, on_dead: Callable[[], None], interval: float = 2.0) -> None
    """Windows 版 ppid 自杀 watchdog（Job Object 或轮询兜底，见 §7 R-02）"""

# —— 服务启停 ——
def start_assistant_services(home: Path, resources: Path) -> list[dict]
    """按端口占用检查依次拉起 ipc 18765 / setup_wizard 18766 / chat_ui 18767；
       返回 [{service, port, pid, log}]；等价 app/main.swift ServiceManager.start()"""

def stop_assistant_services() -> None
    """按记录 pid 树终止三服务；等价 ServiceManager.stop()"""

# —— 系统调用替代 ——
def mem_gb() -> float
    """ctypes GlobalMemoryStatusEx；等价 model_catalog sysctl hw.memsize"""

def cpu_brand() -> str
    """ctypes GetSystemInfo/注册表；等价 sysctl machdep.cpu.brand_string"""

def choose_folder_dialog() -> str | None
    """tkinter filedialog.askdirectory；等价 setup_wizard osascript choose folder"""

def reveal_in_explorer(path: Path) -> None
    """explorer /select,<path>；等价 ipc.py 既有 Windows 分支（保留内联亦可）"""

def image_resize(src: Path, dst: Path, max_size: tuple[int, int]) -> bool
    """Pillow（若特批）或 System.Drawing 兜底；等价 doc_tools sips"""

# —— 凭据（替代 Keychain）——
def set_secret(service: str, key: str, value: str) -> None
def get_secret(service: str, key: str) -> str | None
    """Credential Manager / DPAPI CryptProtectData；等价 vault_store security 命令"""

# —— 平台信息 ——
def platform_label() -> str
    """'windows'（供 skill/brick 能力声明使用）"""

def is_windows() -> bool
```

### 4.3 调用点映射（win.py 函数 ↔ 现有代码）

| win.py 函数 | 替代的现有代码 | 备注 |
|---|---|---|
| `port_in_use` | `main.swift:50` lsof | Swift 侧；run.ps1 亦可用 |
| `kill_pid_on_port` | `ipc.py:468-484` lsof/ps/kill | 收敛 ipc.py 分支 |
| `mem_gb` / `cpu_brand` | `model_catalog.py:130-156` sysctl | 收敛 model_catalog 分支 |
| `choose_folder_dialog` | `setup_wizard.py:738-741` osascript | 收敛 setup_wizard 分支 |
| `image_resize` | `doc_tools.py:371` sips | 收敛 doc_tools 分支 |
| `set_secret`/`get_secret` | `vault_store.py:73-89` security | 收敛 vault_store 分支 |
| `user_data_dir` | `main.swift:16-18` Library/Application Support | Swift 侧；run.ps1 注入 BRICKERY_HOME |

---

## 五、启动脚本设计

### 5.1 交付物

- `run.bat`：双击即用的最小启动器（cmd，兼容性兜底）。
- `run.ps1`：完整流程（推荐，支持日志、检查、退出码）。
- （可选）`stop.bat` / `stop.ps1`：停止三服务。

### 5.2 流程（run.ps1 伪代码）

```powershell
# ===== run.ps1：Windows 启动 brickery 三服务 =====
$ErrorActionPreference = "Stop"
$ports    = @{ ipc = 18765; setup = 18766; chat = 18767 }
$logDir   = Join-Path $env:LOCALAPPDATA "Brickery\logs"
$dataDir  = Join-Path $env:LOCALAPPDATA "Brickery"          # 注入 BRICKERY_HOME
New-Item -ItemType Directory -Force $logDir, $dataDir | Out-Null

# 1) 检查 Python（>=3.9）
$py = (Get-Command python -ErrorAction SilentlyContinue) ?? (Get-Command py -ErrorAction SilentlyContinue)
if (-not $py) { Write-Error "未找到 Python，请安装 3.9+ 并勾选 Add to PATH"; exit 1 }

# 2) 检查依赖（零依赖红线：stdlib 即可；若特批依赖则在此 pip check）
& $py -c "import brickery.runtime.ipc" 2>$null
if ($LASTEXITCODE -ne 0) { Write-Error "brickery 包不可导入：PYTHONPATH=$PWD"; exit 1 }

# 3) 环境注入（等价 Swift 壳）
$env:PYTHONPATH = $PWD
$env:BRICKERY_HOME = $dataDir
$env:BRICKERY_NO_WATCHDOG = "1"          # launcher 退出后 IPC 独立存活

# 4) 逐端口检查并拉起服务（等价 ServiceManager.start）
foreach ($svc in @("brickery.runtime.ipc","brickery.runtime.setup_wizard","brickery.runtime.chat_ui")) {
    $port = $ports[($svc -split '\.')[-1]]
    if (Test-NetConnection 127.0.0.1 -Port $port -InformationLevel Quiet) { continue }
    $log = Join-Path $logDir ($svc -replace '\.','_' + ".log")
    Start-Process -WindowStyle Hidden -RedirectStandardOutput $log -RedirectStandardError $log `
        -FilePath $py.Source -ArgumentList @("-m", $svc, "--home", $dataDir, "--app-resources", $PWD) 
}
Start-Sleep -Seconds 2
# 5) 打开聊天界面（等价 openPage：有 config 进 18767，无 config 进 18766 引导）
$configPath = Join-Path $dataDir "config.json"
Start-Process (Test-Path $configPath ? "http://127.0.0.1:18767/" : "http://127.0.0.1:18766/")
```

### 5.3 与 macOS 启动对比（逐项）

| 步骤 | macOS（Swift 壳） | Windows（run.ps1） |
|---|---|---|
| Python 解释器 | 内嵌 `Resources/python/bin/python3` | 系统 Python 3.9+（开发态）；exe 打包后由 PyInstaller 嵌入（§六） |
| 环境注入 | PYTHONPATH / BRICKERY_NO_WATCHDOG / BRICKERY_HOME | 同上 |
| 端口占用检查 | lsof | Test-NetConnection / Get-NetTCPConnection |
| 服务拉起 | Process + 日志重定向 | Start-Process + 日志重定向 |
| 页面打开 | WKWebView 加载 URL | 默认浏览器打开 URL（开发态）；打包后 WebView2 壳（§六） |
| 退出清理 | `p.terminate()`（SIGTERM） | `stop.ps1`：taskkill /T（SIGTERM → 失败则 /F） |

### 5.4 run.bat（最小兜底）

```bat
@echo off
REM 最小启动器：仅拉起 ipc + setup_wizard + chat_ui，浏览器打开
set PYTHONPATH=%CD%
set BRICKERY_NO_WATCHDOG=1
set BRICKERY_HOME=%LOCALAPPDATA%\Brickery
start /B python -m brickery.runtime.ipc --home "%BRICKERY_HOME%" --app-resources "%CD%" 
start /B python -m brickery.runtime.setup_wizard
start /B python -m brickery.runtime.chat_ui
timeout /t 2 >nul
start "" "http://127.0.0.1:18766/"
```

---

## 六、exe 打包路线

### 6.1 方案选择

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| **PyInstaller**（onedir 优先） | 生态成熟、文档多、WebView2 集成样例多；支持 `--add-data` 内置资源、`--hidden-import` 控制 | 启动略慢、杀软误报率较高 | **首选**：满足"底座即完整成品"分发路径，onedir 便于更新单文件 |
| Nuitka | 编译为原生码、启动快、反编译难 | 编译链复杂（需 C 编译器）、构建时长高、与动态 import 冲突面大 | 备选：P0/P1 验证通过后视杀软误报情况再评估 |
| python-embed + 自制 launcher | 零第三方构建依赖、体积最小 | 需手写 exe 壳与 WebView2 桥、工作量大 | 不采用（与 PyInstaller 收益不对等） |

**结论：PyInstaller onedir + WebView2 壳（或开发态浏览器兜底）。**

### 6.2 spec 要点（PyInstaller）

```python
# brickery-win.spec（示意）
a = Analysis(
    ["brickery/win_launcher.py"],          # 新入口：等价 Swift AppDelegate（启动三服务 + WebView2）
    pathex=["."],
    datas=[
        ("brickery", "brickery"),          # 内核包全量（含 runtime/memory/fixtures）
        ("brickery/brickery", "brickery/brickery"),  # 嵌套包 ppt_brick 等
        ("brickery/brickery/fixtures", "brickery/brickery/fixtures"),  # 内置模板（_ensure_agent_home 复制源）
        ("brickery/brickery/builtin_skills", "brickery/brickery/builtin_skills"),
    ],
    hiddenimports=[
        "brickery.runtime.ipc", "brickery.runtime.setup_wizard", "brickery.runtime.chat_ui",
        "brickery.runtime.daemon", "brickery.runtime.supervisor", "brickery.runtime.binary_manager",
        "brickery.runtime.connectors.feishu", "brickery.runtime.connectors.telegram",
        "brickery.runtime.gateway", "brickery.brickery.ppt_brick",  # 动态/惰性 import 兜底
    ],
    excludes=["brickery.dmg"],             # macOS 专属模块排除
)
exe = EXE(..., name="Brickery.exe", console=False, icon="assets/brickery.ico")
coll = COLLECT(exe, a.binaries, a.datas, name="Brickery")
```

### 6.3 资源文件

- `_ensure_agent_home`（ipc.py:3138）首次启动从资源目录复制模板 → PyInstaller `_MEIPASS` 或 onedir 下 `resources/`，`platform/win.py:app_resources_dir()` 提供解析。
- WebView2 数据目录 → `%LOCALAPPDATA%\Brickery\WebView2`（持久化 localStorage 等价 WKWebView）。
- 图标/安装器资源 → `assets/` 新目录。

### 6.4 签名与杀软白名单注意事项

1. **代码签名**：Windows 无签名 exe 会触发 SmartScreen"未知发布者"；建议 Code Signing 证书（EV 更佳）。
2. **杀软误报**：PyInstaller 打包特征 + `os.kill`/taskkill/端口清理逻辑易被 AV 标记 → 白名单申请（提交样本到 Defender/360/火绒等）；规避方式：onedir 而非 onefile（onefile 自解压更易误报）。
3. **UAC**：服务常驻 + 端口绑定均在 127.0.0.1 高位端口，**不需要管理员权限**；安装器（NSIS/Inno Setup）按需请求 user 级安装。
4. **网络行为声明**：首次运行下载引擎二进制/模型（binary_url、模型仓库）需在安装器或首次启动页明示，降低安全软件拦截率。

### 6.5 安装器（后续可选）

- 阶段二不强制；分发路线优先"免安装 zip + run.bat"（对齐 macOS DMG 直拖）。
- 需要安装体验时：**Inno Setup**（轻量、脚本化、与 GitHub Release 集成好）。

---

## 七、风险与待验证项

| # | 风险 / 待验证项 | 影响 | 缓解 / 验证方法 |
|---|---|---|---|
| R-01 | `os.getppid()` watchdog（ipc.py:3233、supervisor.py:215）在 Windows 语义不同：父进程退出后 pid 可能被复用，误自杀或留孤儿 | 高：进程生命周期管理失效 | Windows 用 Job Object（`CreateJobObject`）或轮询 + 校验；P0 先行实现 `parent_pid_watchdog()` 并单测 |
| R-02 | `signal.SIGUSR1`（ipc.py:3164 faulthandler）在 Windows 不存在 | 低：仅诊断功能 | `try/except` 注册；Windows 可用 `SIGBREAK` 兜底 |
| R-03 | `vault_store` Keychain + openssl 加密：Windows 无 `security` 命令、openssl 不随系统分发 | 中：文件柜加密不可用 | DPAPI（ctypes，零依赖）优先；引入 `cryptography` 需特批 |
| R-04 | `doc_tools` sips 图片处理：Windows 无等价系统命令 | 中：图片能力降级 | 方案 A Pillow（特批依赖）；方案 B PowerShell System.Drawing（能力弱）；待验证图片处理在 Windows 发布面的优先级 |
| R-05 | `model_catalog` sysctl：Windows 无 | 低 | ctypes 已收敛进 win.py，无需特批 |
| R-06 | **binary_url 平台矩阵**：引擎二进制（39099 edsdk_pro 等）当前 binary_url 指向 macOS 产物；Windows 无对应二进制则引擎类积木不可用 | 高：二进制积木生态断档 | 向用户确认 `MARKETPLACE_BINARY_EXT` 契约与发布面；Windows 首版可声明"引擎积木暂不支持" |
| R-07 | WebView2 运行时：Win10/11 多数自带，但老版本可能缺失 | 中 | 开发态浏览器兜底；安装器检测并引导安装 Evergreen WebView2 Runtime |
| R-08 | 路径分隔符 / 大小写：macOS 大小写不敏感（默认）→ Windows 敏感；既有配置/蓝图含 `/Users/...` 历史路径 | 中 | `legacy_path_to_windows()` 转换 + 测试矩阵 |
| R-09 | 中文/emoji 文件名与 cmd 编码（GBK vs UTF-8） | 低 | run.bat 顶部 `chcp 65001`；run.ps1 默认 UTF-8 |
| R-10 | 冻结文书缺失：`MARKETPLACE_BINARY_EXT.md` 不在本仓库 | 中 | 需用户确认文书实际位置（可能在 brick-vault）后补录引用 |
| R-11 | 零依赖红线（`dependencies=[]`）：Pillow/cryptography 引入需特批 | 中 | 优先 ctypes/System.Drawing 等零依赖实现；确实需要第三方依赖时单独走特批 |

---

## 八、分阶段实施计划（仅计划，不执行）

| 阶段 | 内容 | 验收标准 | 依赖 |
|---|---|---|---|
| **P0 适配层** | 新建 `brickery/platform/__init__.py` + `win.py`（§四全部函数）；收敛 6 处系统调用分支（ipc lsof/ps、model_catalog sysctl、setup_wizard osascript、doc_tools sips、vault_store security、sandbox 路径黑名单）；`try/except` 保护 SIGUSR1 | win.py 单测通过；macOS 行为回归 0 差异 | 特批：无（纯新增+分支收敛）；若用 Pillow/cryptography 需特批 |
| **P1 启动脚本** | `run.bat` / `run.ps1` / `stop.ps1`（§五）；Python 3.9+ 开发态三服务拉起与浏览器打开；日志/退出码/端口占用校验 | Windows 10/11 开发机上三服务全部监听（18765/18766/18767），重复启动幂等 | P0 |
| **P2 exe 打包** | PyInstaller spec + `win_launcher.py` 入口 + WebView2 壳（或浏览器兜底）；资源打包（内置模板、嵌套包）；排除 `dmg.py`；签名与杀软白名单验证 | 免安装 zip 双击可运行；首次启动复制模板成功；SmartScreen/Defender 不拦截（或已提交白名单） | P1 |
| **P3 回归验证** | macOS 全量回归（ipc/chat_ui/setup_wizard/daemon/记忆巩固）；Windows 冒烟（首次启动 → 引导 → 聊天 → 文件柜 → 重启）；binary_url 平台矩阵确认 | 双平台功能对等清单核对完成；引擎积木可用性或降级声明明确 | P2 + R-06 特批 |

> 实施优先级说明：P0 适配层是全部后续的底座；P2 exe 打包可独立于 P1 并行推进（二者仅在 `win_launcher.py` 入口处汇合）。

---

## 附录 A：服务与端口清单（探查结论）

| 端口 | 服务 | 模块 | 启动方式 | 说明 |
|---|---|---|---|---|
| 18765 | IPC 主后端 | `brickery/runtime/ipc.py` | `python -m brickery.runtime.ipc --home <data> --app-resources <res>`；main() 入口 + `__main__` | JSON Lines socket；自动拉起 daemon；连接器；ppid watchdog；lsof 抢占清理 |
| 18766 | 安装引导 | `brickery/runtime/setup_wizard.py` | `python -m brickery.runtime.setup_wizard` | ThreadingHTTPServer；osascript 文件夹选择 |
| 18767 | 聊天 UI | `brickery/runtime/chat_ui.py` | `python -m brickery.runtime.chat_ui` | ThreadingHTTPServer；桥接 18765 IPC |
| 39099 | 引擎二进制 | `binary_manager.py` + `edsdk_pro.py` | `_launch()` 拉起 binary_url 下载的引擎 | DocWritePro 等二进制积木（冻结态） |
| 8765 / 8767 | workbench / factory | `brickery.web.server` / `factory.server` | Swift 壳按 bundle id 切换 | 已冻结归档，不进入 Windows 范围 |
| — | 探活 | `scripts/check_alignment.sh` | shell | 18765 健康检查 |

## 附录 B：探查方法与依据

- 启动/打包：`pyproject.toml` 实读；`ls` 仓库根（无 run.sh/launcher/`__main__`）；`app/Sources/BrickeryApp/main.swift` 全读。
- 平台相关代码：全库 grep（osascript/plutil/defaults/open/pkill/killall/launchctl/subprocess/sysctl/sips/security）+ 关键行段实读。
- 冻结条款：全库 grep「冻结」「仅 macOS」「Windows」+ 原文摘录。
- 端口/服务：ipc.py main 实读、daemon.py 全读、supervisor.py 关键段、chat_ui/setup_wizard 端口常量、binary_manager 引擎端口。
*（内容由AI生成，仅供参考）*
