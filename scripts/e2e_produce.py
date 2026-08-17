"""Brickery e2e 验证：拖拽 → 校验 → 产出 → 独立安装包 全链路。

用法：python3 scripts/e2e_produce.py [--vault /path/to/brick-vault]
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brickery import AssemblyError, load_vault  # noqa: E402
from brickery.produce import ProduceError, ProduceMeta, list_agents, produce  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", default=str(Path.home() / "Dev" / "brick-vault"))
    ap.add_argument("--name", default="e2e-demo")
    ap.add_argument("--bricks", default="ax,visualize,docwrite",
                    help="逗号分隔的积木名")
    args = ap.parse_args()

    selected = [s.strip() for s in args.bricks.split(",") if s.strip()]

    # 1) 静态组装
    print(f"[1/4] 装载积木库：{args.vault}")
    asm = load_vault(args.vault)
    print(f"      共 {len(asm.bricks)} 个积木")
    try:
        plan = asm.assemble(selected)
    except AssemblyError as e:
        print(f"      [失败] 组装失败：{e}")
        return 1
    print(f"      [OK] 组装方案：{plan.order}")
    print(f"      资源合计：{plan.resources_total}")

    # 2) 产出到临时目录（不污染 ~/.brickery）
    with tempfile.TemporaryDirectory() as tmp:
        agents_root = Path(tmp)
        meta = ProduceMeta(name=args.name, description="e2e 验证产出",
                           author="brickery-e2e")
        print(f"[2/4] 产出 agent 包 → {agents_root / args.name}")
        try:
            out = produce(plan, args.vault, meta, agents_root=agents_root)
        except ProduceError as e:
            print(f"      [失败] 产出失败：{e}")
            return 1

        # 3) 校验产出物
        print("[3/4] 校验产出物")
        manifest = json.loads((out / "agent.json").read_text(encoding="utf-8"))
        assert manifest["name"] == args.name, "agent.json name 不符"
        assert manifest["assembly"]["order"] == plan.order, "装配顺序不符"
        brick_files = sorted(p.name for p in (out / "bricks").glob("*.brick.json"))
        assert len(brick_files) == len(plan.order), "积木快照数量不符"
        assert (out / "run.sh").exists(), "run.sh 缺失"
        assert (out / f"{args.name}.app" / "Contents" / "Info.plist").exists(), ".app 骨架缺失"
        # B6：独立运行时打包校验
        rt = out / f"{args.name}.app" / "Contents" / "Resources" / "brickery-runtime" / "brickery"
        assert (rt / "runtime" / "ipc.py").exists(), "brickery-runtime 未打包 runtime/ipc"
        assert (rt / "memory" / "__init__.py").exists(), "brickery-runtime 未打包 memory/"
        assert (rt / "produce.py").exists(), "brickery-runtime 未打包 produce.py"
        run_sh = (out / "run.sh").read_text(encoding="utf-8")
        assert "brickery-runtime" in run_sh, "run.sh 未引用打包运行时"
        assert "shadeling" not in run_sh, "run.sh 仍依赖宿主 shadeling 命令"
        print(f"      [OK] agent.json（{len(brick_files)} 个积木快照）")
        print(f"      [OK] run.sh 启动脚本（独立运行时入口）")
        print(f"      [OK] {args.name}.app 安装包骨架")
        print(f"      [OK] brickery-runtime 已打包进 .app（runtime+memory）")
        print(f"      [OK] 产出目录：{out}")

        # 4) 列出
        print("[4/4] 产出清单")
        for a in list_agents(agents_root=agents_root):
            print(f"      - {a['name']} v{a['version']}（{a['bricks']} 积木，{a['runtime']}）")

    print("\n[OK] e2e 全链路通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
