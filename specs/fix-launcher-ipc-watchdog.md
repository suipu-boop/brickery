# 修复方案：launcher 双击启动后 IPC 进程自杀

## 背景

web-test-agent 打包为 .app 后，双击启动流程为：

```
launcher（bash，CFBundleExecutable）
  └─ nohup python3 -m brickery.runtime.ipc ... &   # 后台启动 IPC
  └─ open status.html                               # 打开状态页
  └─ exit 0                                         # launcher 立即退出
```

实测（命令行直跑 launcher 与 open 启动均复现）：launcher 退出后 2 秒内 IPC 进程消失，
18765 端口不再监听。而直接 nohup 后台跑 IPC（不经 launcher）时进程存活。

## 根因

`brickery/runtime/ipc.py` 的 `main()` 末尾存在**父进程守护 watchdog**：

```python
_parent_pid = os.getppid()
def _watchdog():
    while not _stop.is_set():
        time.sleep(2)
        if os.getppid() != _parent_pid:
            srv.stop()
            os._exit(0)
threading.Thread(target=_watchdog, daemon=True).start()
```

设计意图：IPC 作为 Swift 宿主 App 的子进程托管，宿主退出（崩溃/强退）时子进程被 reparent，
ppid 变化即自杀，避免遗留孤儿进程。

但 launcher 场景下，IPC 的父进程是 launcher（bash）。launcher 启动 IPC 后立即 `exit 0`，
IPC 被 reparent 到 launchd，ppid 变化 → watchdog 判定"宿主退出"→ 自杀。

## 修复方案

给 watchdog 增加环境变量开关 `BRICKERY_NO_WATCHDOG`：

- **Swift 宿主 App 托管**（默认）：不设该变量，watchdog 生效，行为不变（宿主退出 → 自杀）。
- **launcher 双击启动**：launcher 启动 IPC 时设置 `BRICKERY_NO_WATCHDOG=1`，
  IPC 跳过 watchdog，作为独立服务存活（launcher 只是启动器，退出不影响服务）。

### 改动点

1. `brickery/runtime/ipc.py` `main()`：watchdog 启动前判断
   `os.environ.get("BRICKERY_NO_WATCHDOG") == "1"`，为真则跳过 watchdog 线程。
2. `produce.py` 生成的 launcher 脚本：nohup 启动 IPC 前 `export BRICKERY_NO_WATCHDOG=1`。

### 影响面

- 仅影响 launcher 启动路径；Swift 宿主托管路径零改动。
- 独立存活后，用户停止服务的方式不变（status.html 上的停止命令 / 下次启动时
  `_free_port` 自动清理旧进程）。

## 验证

1. 命令行直跑 launcher → sleep 5 → IPC 进程存活、18765 监听。
2. `open /Applications/web-test-agent.app` → sleep 5 → IPC 进程存活、18765 监听。
3. 重新出包 → cp 到 /Applications → 双击验证。
