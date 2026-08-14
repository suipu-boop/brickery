"""§4.4 执行层沙箱（clean room，纯自研）。

真实文件系统 / 命令执行前的**安全裁决层**：路径白名单 + 危险命令前缀拦截 + 输出/超时上限。
红线：不发起网络请求；不执行任意代码——只做路径/字符串校验与 subprocess 超时包装。

出厂默认值见 DEFAULT_*（2026-08-06 随朴拍板冻结，见 CAPABILITY_PLAN §3.4）。
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Tuple


# —— 出厂默认常量（冻结，不中途改）——
# 写权限开放目录（均在本用户家目录内）
DEFAULT_WRITE_ROOTS: List[str] = [
    "~/Desktop", "~/Documents", "~/Downloads", "~/Dev",
    "~/.shadeling", "~/shadeling-runtime",
]
# 读权限：整个用户家目录内可读（默认 ~/）
DEFAULT_READ_ROOTS: List[str] = ["~"]
# 系统区：无论读写均拒绝（防越权碰系统/他人）
SYSTEM_DENY_PREFIXES: List[str] = [
    "/System", "/Library", "/private", "/usr", "/bin", "/sbin",
    "/etc", "/var", "/opt", "/Volumes", "/Network",
]
# 源码保护区：运行时工具不得写入 Shadeling 自身源码目录（防自污染）
PROTECTED_SOURCE_DIRS: List[str] = [
    # 开发期源码目录
    str(Path("~/Dev/Shadeling").expanduser().resolve()),
]
# 禁命令前缀（Bash 高危）。顺序敏感：宽前缀（sudo）放前，避免被后续子串误归因。
DANGEROUS_COMMAND_PREFIXES: List[str] = [
    "sudo ", "rm -rf /", "mkfs", "dd if=", "chmod -r 000", "chmod -R 000",
]
# 输出上限 50KB（超限截断回填，防撑爆 context）
OUTPUT_LIMIT: int = 50 * 1024
# 命令超时 30s（超时杀进程）
TIMEOUT: int = 30


def _expand(roots: List[str]) -> List[str]:
    out = []
    for r in roots:
        try:
            out.append(str(Path(r).expanduser().resolve()))
        except (OSError, RuntimeError):
            # 解析失败的路径跳过（不会成为合法白名单项）
            continue
    return out


def _resolve_path(path: str) -> Path:
    """尽量解析路径（存在则完全解析含符号链接；不存在则解析父目录）。"""
    p = Path(path).expanduser()
    try:
        if p.exists():
            return p.resolve()
        return p.parent.resolve() / p.name
    except (OSError, RuntimeError):
        return p


class Sandbox:
    """路径白名单 + 命令前缀拦截 + 输出/超时上限。"""

    def __init__(self,
                 write_roots: List[str] = None,
                 read_roots: List[str] = None,
                 deny_prefixes: List[str] = None,
                 dangerous_commands: List[str] = None,
                 protected_dirs: List[str] = None,
                 output_limit: int = OUTPUT_LIMIT,
                 timeout: int = TIMEOUT):
        self.write_roots = list(write_roots if write_roots is not None
                                else DEFAULT_WRITE_ROOTS)
        self.read_roots = list(read_roots if read_roots is not None
                               else DEFAULT_READ_ROOTS)
        self.deny_prefixes = list(deny_prefixes if deny_prefixes is not None
                                  else SYSTEM_DENY_PREFIXES)
        self.dangerous_commands = list(
            dangerous_commands if dangerous_commands is not None
            else DANGEROUS_COMMAND_PREFIXES)
        self.protected_dirs = list(
            protected_dirs if protected_dirs is not None
            else PROTECTED_SOURCE_DIRS)
        self.output_limit = output_limit
        self.timeout = timeout

    # —— 路径裁决 ——
    def check_path_write(self, path: str) -> Tuple[bool, str]:
        try:
            s = str(_resolve_path(path))
        except (OSError, RuntimeError):
            return False, f"路径无法解析：{path}"
        for d in self.deny_prefixes:
            if s.startswith(d):
                return False, f"系统区禁止写入：{path}"
        # 源码保护区：即使 ~/Dev 在写白名单内，也不允许写 Shadeling 自身源码
        for p in self.protected_dirs:
            if s.startswith(p):
                return False, f"源码保护区禁止写入（{p}）：{path}"
        allowed = _expand(self.write_roots)
        if not any(s.startswith(a) for a in allowed):
            return False, f"不在写权限白名单（仅允许：{', '.join(self.write_roots)}）：{path}"
        return True, ""

    def check_path_read(self, path: str) -> Tuple[bool, str]:
        try:
            s = str(_resolve_path(path))
        except (OSError, RuntimeError):
            return False, f"路径无法解析：{path}"
        for d in self.deny_prefixes:
            if s.startswith(d):
                return False, f"系统区禁止读取：{path}"
        allowed = _expand(self.read_roots)
        if not any(s.startswith(a) for a in allowed):
            return False, f"不在读权限白名单：{path}"
        return True, ""

    # —— 命令裁决 ——
    def check_command(self, cmd: str) -> Tuple[bool, str]:
        c = " ".join(cmd.split())  # 归一化空白
        low = c.lower()
        # 管道给 shell 执行（curl|sh、wget|bash、… | eval 等）
        if re.search(r"\|\s*(sh|bash|zsh|eval)\s*$", low) or \
           re.search(r"\|\s*(sh|bash|zsh)\b", low):
            return False, "禁止把命令输出管道给 shell 执行（如 curl … | sh）"
        for d in self.dangerous_commands:
            dl = d.lower()
            if low.startswith(dl) or f" {dl}" in low:
                return False, f"高危命令前缀被禁：{d.strip()}"
        return True, ""

    # —— 受控执行（Bash handler 用）——
    def run_captured(self, cmd: str, cwd: str = None) -> Tuple[int, str, str]:
        """经沙箱裁决后运行命令，返回 (returncode, stdout, stderr)，输出截断到上限。"""
        ok, reason = self.check_command(cmd)
        if not ok:
            return 126, "", f"[沙箱拒绝] {reason}"
        try:
            res = subprocess.run(
                cmd, shell=True, cwd=cwd,
                capture_output=True, text=True,
                timeout=self.timeout,
            )
            out = res.stdout or ""
            err = res.stderr or ""
            if len(out) > self.output_limit:
                out = out[:self.output_limit] + f"\n...[stdout 已截断 {len(out) - self.output_limit} 字节]"
            if len(err) > self.output_limit:
                err = err[:self.output_limit] + f"\n...[stderr 已截断 {len(err) - self.output_limit} 字节]"
            return res.returncode, out, err
        except subprocess.TimeoutExpired:
            return 124, "", f"[沙箱] 命令超时（>{self.timeout}s）已终止"
        except Exception as e:  # noqa: BLE001
            return 1, "", f"{type(e).__name__}: {e}"


def default_sandbox() -> Sandbox:
    """出厂默认沙箱（随朴 2026-08-06 拍板）。"""
    return Sandbox()
