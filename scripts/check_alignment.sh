#!/bin/bash
# Brickery 一致性校验脚本（spec: auto-follow-single-source.md Phase 4）
#
# 四层核对 + vault 三层积木清单对照，输出一致性矩阵：
#   L1 仓库层   本地 main vs origin/main HEAD / 未提交改动
#   L2 副本层   运行副本 brickery 包 vs 本地内核（逐文件 sha256）
#   L3 远端层   本地 HEAD vs GitHub API 远端 main HEAD
#   L4 进程层   端口 18765/18766/18767 探活
#   V  vault 层 vault 真身 vs brick-vault 本地 vs 远端 main
#
# 用法:
#   bash scripts/check_alignment.sh            # 全量（本机）
#   bash scripts/check_alignment.sh --scope=repo  # 仅仓库+远端层（CI 用）
#   bash scripts/check_alignment.sh --quiet    # 只输出矩阵与结论
# 退出码: 0 = 全 PASS（含 SKIP/WARN）；1 = 存在 FAIL（用于发布门禁/CI）
set -uo pipefail

# ---------- 配置 ----------
REPOS=(
  "/Users/suipu/Dev/brickery"
  "/Users/suipu/Dev/brick-vault"
  "/Users/suipu/Dev/brickery-workbench"
  "/Users/suipu/Dev/brickery-factory"
  "/Users/suipu/Dev/brickery-meta"
)
GH_OWNER="suipu-boop"
COPIES=(
  "/Applications/shadelingmac0.0.1.app/Contents/Resources/brickery-runtime/brickery:生成app运行副本"
  "/Applications/BrickeryWorkbench.app/Contents/Resources/brickery-runtime/brickery:工坊app运行副本"
)
PORTS=(18765 18766 18767)
VAULT_DIR="${HOME}/.brickery/vault/bricks"
VAULT_LOCAL="/Users/suipu/Dev/brick-vault/bricks"
CORE_REPO="/Users/suipu/Dev/brickery"

SCOPE="all"
QUIET=0
for arg in "$@"; do
  case "${arg}" in
    --scope=*) SCOPE="${arg#*=}" ;;
    --quiet) QUIET=1 ;;
  esac
done

PASS_N=0; FAIL_N=0; WARN_N=0; SKIP_N=0
MATRIX=()

say() { [ "${QUIET}" -eq 1 ] || echo "$@"; }

mark() { # mark <状态> <行文本>
  local st="$1"; shift
  MATRIX+=("${st}|$*")
  case "${st}" in
    PASS) PASS_N=$((PASS_N+1)) ;;
    FAIL) FAIL_N=$((FAIL_N+1)) ;;
    WARN) WARN_N=$((WARN_N+1)) ;;
    SKIP) SKIP_N=$((SKIP_N+1)) ;;
  esac
}

git_head() { git -C "$1" rev-parse HEAD 2>/dev/null || echo ""; }
git_origin() { git -C "$1" rev-parse "origin/$2" 2>/dev/null || echo ""; }
git_dirty() { [ -n "$(git -C "$1" status --porcelain 2>/dev/null)" ] && echo "dirty" || echo "clean"; }

# remote_sha <repo目录名> → 远端 main sha 或空（api 失败降级 git ls-remote）
remote_sha() {
  local name="$1" s=""
  # 目录名 → 远端仓库名映射（brick-vault 本地目录对应 shadeling-bricks 仓库）
  case "${name}" in
    brick-vault) name="shadeling-bricks" ;;
  esac
  s="$(curl -fsS --retry 2 --retry-all-errors --max-time 12 "https://api.github.com/repos/${GH_OWNER}/${name}/commits/main" 2>/dev/null \
    | python3 -c 'import sys,json; print(json.load(sys.stdin)["sha"])' 2>/dev/null || echo '')"
  if [ -z "${s}" ]; then
    s="$(git -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=10 ls-remote "https://github.com/${GH_OWNER}/${name}.git" refs/heads/main 2>/dev/null | cut -f1)"
  fi
  echo "${s}"
}

# ---------- L1 仓库层 ----------
l1_repos() {
  say ""
  say "== [L1] 仓库层：本地 main vs origin/main =="
  for r in "${REPOS[@]}"; do
    [ -d "${r}/.git" ] || { mark SKIP "L1 ${r} 非 git 仓库，跳过"; continue; }
    local name br
    name="$(basename "${r}")"
    br="$(git -C "${r}" symbolic-ref --short HEAD 2>/dev/null || echo 'detached')"
    local head origin dirty
    head="$(git_head "${r}")"; origin="$(git_origin "${r}" main)"; dirty="$(git_dirty "${r}")"
    if [ "${br}" = "main" ]; then
      if [ "${head}" = "${origin}" ] && [ "${dirty}" = "clean" ]; then
        mark PASS "L1 ${name} main=${origin} clean"
      elif [ "${head}" != "${origin}" ]; then
        mark FAIL "L1 ${name} main 落后/超前 origin/main (local=${head:0:12} origin=${origin:0:12})"
      else
        mark WARN "L1 ${name} main 与远端一致但有未提交改动"
      fi
    else
      if [ "${SCOPE}" = "repo" ]; then
        mark SKIP "L1 ${name} CI 模式（当前分支=${br}），仓库层比对仅 main 分支执行"
        continue
      fi
      local mhead
      mhead="$(git -C "${r}" rev-parse main 2>/dev/null || echo '')"
      if [ "${mhead}" = "${origin}" ] && [ "${dirty}" = "clean" ]; then
        mark PASS "L1 ${name} 当前分支=${br}（main 与 origin/main 一致，clean）"
      else
        mark WARN "L1 ${name} 当前分支=${br}（main=${mhead:0:12} vs origin=${origin:0:12}，${dirty}）"
      fi
    fi
  done
}

# ---------- L2 副本层（全量模式） ----------
l2_copies() {
  say ""
  say "== [L2] 副本层：运行副本 vs 本地内核（逐文件 sha256） =="
  [ "${SCOPE}" = "repo" ] && { mark SKIP "L2 副本层仅本机模式执行"; return; }
  local local_pkg="${CORE_REPO}/brickery"
  for entry in "${COPIES[@]}"; do
    local dst label
    dst="${entry%%:*}"; label="${entry#*:}"
    if [ ! -d "${dst}" ]; then
      mark SKIP "L2 ${label} 副本不存在：${dst}"
      continue
    fi
    # 以本地内核为基准，逐文件比较。
    # 排除：__pycache__/pyc/version.json（打包时写入）、.venv（环境残留）、
    #   brickery/ 嵌套子包 + fixtures/ + builtin_skills/（打包结构差异）、*/tests/*（测试不随包）
    local diff_list=""
    diff_list="$(cd "${local_pkg}" && find . -type f \
      ! -path "*/__pycache__/*" ! -name "*.pyc" ! -name "version.json" \
      ! -path "./.venv/*" \
      ! -path "./brickery/*" ! -path "./fixtures/*" ! -path "./builtin_skills/*" \
      ! -path "*/tests/*" \
      -exec shasum -a 256 {} \; | sort -k2 | while read -r h f; do
        if [ -f "${dst}/${f}" ]; then
          dh="$(shasum -a 256 "${dst}/${f}" | cut -d' ' -f1)"
          [ "${dh}" = "${h}" ] || echo "${f}"
        else
          echo "${f}"
        fi
      done)"
    if [ -z "${diff_list}" ]; then
      mark PASS "L2 ${label} 与本地内核完全一致"
    else
      local n
      n="$(echo "${diff_list}" | grep -c .)"
      mark FAIL "L2 ${label} 差异 ${n} 个文件：$(echo "${diff_list}" | head -5 | tr '\n' ' ')"
    fi
  done
}

# ---------- L3 远端层 ----------
l3_remote() {
  say ""
  say "== [L3] 远端层：本地 HEAD vs GitHub API main =="
  for r in "${REPOS[@]}"; do
    [ -d "${r}/.git" ] || { mark SKIP "L3 $(basename "${r}") 非 git 仓库"; continue; }
    local name head remote
    name="$(basename "${r}")"
    head="$(git_head "${r}")"
    local br
    br="$(git -C "${r}" symbolic-ref --short HEAD 2>/dev/null || echo 'detached')"
    if [ "${SCOPE}" = "repo" ] && [ "${br}" != "main" ]; then
      mark SKIP "L3 ${name} CI 模式（当前分支=${br}），远端比对仅 main 分支执行"
      continue
    fi
    remote="$(remote_sha "${name}")"
    if [ -z "${remote}" ]; then
      mark SKIP "L3 ${name} 远端不可达（离线/限流），本地=${head:0:12}"
      continue
    fi
    if [ "${head}" = "${remote}" ]; then
      mark PASS "L3 ${name} 本地=远端main=${remote}"
    else
      mark FAIL "L3 ${name} 本地=${head:0:12} vs 远端main=${remote:0:12}"
    fi
  done
}

# ---------- L4 进程层（全量模式） ----------
l4_procs() {
  say ""
  say "== [L4] 进程层：端口探活 =="
  [ "${SCOPE}" = "repo" ] && { mark SKIP "L4 进程层仅本机模式执行"; return; }
  for p in "${PORTS[@]}"; do
    local line
    line="$(lsof -nP -iTCP:${p} -sTCP:LISTEN 2>/dev/null | tail -n +2 | head -1)"
    if [ -n "${line}" ]; then
      local pid
      pid="$(echo "${line}" | awk '{print $2}')"
      mark PASS "L4 端口 ${p} 监听中（pid=${pid}）"
    else
      mark FAIL "L4 端口 ${p} 未监听"
    fi
  done
}

# ---------- V vault 三层清单对照 ----------
vault_ids() { # 目录下积木 id 集合（brick.json 所在目录名）
  local d="$1"
  [ -d "${d}" ] || return 0
  find "${d}" -mindepth 1 -maxdepth 1 -type d -exec basename {} \; | sort
}

v_vault() {
  say ""
  say "== [V] vault 三层积木清单对照 =="
  [ "${SCOPE}" = "repo" ] && { mark SKIP "V vault 真身层仅本机模式执行"; return; }
  local true_ids local_ids
  true_ids="$(vault_ids "${VAULT_DIR}")"
  local_ids="$(vault_ids "${VAULT_LOCAL}")"
  local tn ln
  tn="$(echo "${true_ids}" | grep -c . || true)"
  ln="$(echo "${local_ids}" | grep -c . || true)"
  # V1 真身 vs brick-vault 本地
  local only_true only_local
  only_true="$(comm -23 <(echo "${true_ids}") <(echo "${local_ids}"))"
  only_local="$(comm -13 <(echo "${true_ids}") <(echo "${local_ids}"))"
  if [ -z "${only_true}" ] && [ -z "${only_local}" ]; then
    mark PASS "V1 vault真身(${tn}) = brick-vault本地(${ln})"
  else
    mark FAIL "V1 vault真身(${tn}) vs brick-vault本地(${ln}) 差异：仅真身=[$(echo "${only_true}"|tr '\n' ' ')] 仅本地=[$(echo "${only_local}"|tr '\n' ' ')]"
  fi
  # V2 真身 vs 远端 main（brick-vault 仓库 origin/main 一致性 + 远端 API/ls-remote）
  local vremote
  vremote="$(remote_sha "brick-vault")"
  local vlhead vlorigin
  vlhead="$(git_head "${VAULT_LOCAL}/.." 2>/dev/null || echo '')"
  vlorigin="$(git_origin "/Users/suipu/Dev/brick-vault" main)"
  if [ -z "${vremote}" ]; then
    if [ "${vlhead}" = "${vlorigin}" ] && [ -z "${only_true}" ]; then
      mark PASS "V2 远端不可达，本地仓库=origin/main 且真身无额外积木"
    else
      mark WARN "V2 远端不可达，本地仓库 origin/main=${vlorigin:0:12}，真身差异=[$(echo "${only_true}"|tr '\n' ' ')]"
    fi
  else
    if [ "${vlhead}" = "${vremote}" ] && [ -z "${only_true}" ]; then
      mark PASS "V2 vault真身 无 brick-vault 缺失积木，本地仓库=远端main=${vremote}"
    else
      mark FAIL "V2 真身缺远端积木=[$(echo "${only_true}"|tr '\n' ' ')] 或本地仓库≠远端（本地=${vlhead:0:12} 远端=${vremote:0:12}）"
    fi
  fi
  # V3 brick-vault 本地 vs 远端 main（git 引用对比）
  if [ -n "${vlorigin}" ]; then
    if [ "${vlhead}" = "${vlorigin}" ]; then
      mark PASS "V3 brick-vault 本地=origin/main=${vlorigin}"
    else
      mark FAIL "V3 brick-vault 本地=${vlhead:0:12} vs origin/main=${vlorigin:0:12}"
    fi
  else
    mark SKIP "V3 brick-vault origin/main 引用缺失（未 fetch？）"
  fi
}

# ---------- 输出 ----------
main() {
  say "Brickery 一致性校验（scope=${SCOPE}）"
  l1_repos
  l2_copies
  l3_remote
  l4_procs
  v_vault

  say ""
  say "== 矩阵 =="
  printf "%-6s %s\n" "状态" "条目"
  for row in "${MATRIX[@]}"; do
    printf "%-6s %s\n" "${row%%|*}" "${row#*|}"
  done

  say ""
  say "== 结论 =="
  say "PASS=${PASS_N}  FAIL=${FAIL_N}  WARN=${WARN_N}  SKIP=${SKIP_N}"
  if [ "${FAIL_N}" -gt 0 ]; then
    say "结果：FAIL（存在 ${FAIL_N} 处不一致）"
    return 1
  fi
  say "结果：PASS"
  return 0
}

main
exit $?
