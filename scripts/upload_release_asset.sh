#!/usr/bin/env bash
# 替换 GitHub Release 同名资产（先删后传，tag 不变）
# 用法: ./upload_release_asset.sh <repo> <tag> <asset_name> <local_file>
# 示例: ./upload_release_asset.sh suipu-boop/brickery-workbench v0.1.0 BrickeryWorkbench-0.1.0.dmg output/BrickeryWorkbench-0.1.0.dmg
# 依赖: git（credential 已缓存）、curl、python3。无 gh CLI 亦可运行。
set -euo pipefail

REPO="${1:?usage: $0 <repo> <tag> <asset_name> <local_file>}"
TAG="${2:?}"
ASSET_NAME="${3:?}"
LOCAL_FILE="${4:?}"

[ -f "$LOCAL_FILE" ] || { echo "错误: 本地文件不存在: $LOCAL_FILE"; exit 1; }

# 取 GitHub token（不打印）
TOKEN="$(printf 'protocol=https\nhost=github.com\n' | git credential fill 2>/dev/null | sed -n 's/^password=//p')"
[ -n "$TOKEN" ] || { echo "错误: 无法通过 git credential fill 获取 token"; exit 1; }

# 查 release id
RELEASE_JSON="$(curl -s -H "Authorization: Bearer $TOKEN" "https://api.github.com/repos/$REPO/releases/tags/$TAG")"
RELEASE_ID="$(echo "$RELEASE_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("id",""))' 2>/dev/null || true)"
[ -n "$RELEASE_ID" ] || { echo "错误: Release 不存在: $REPO tag=$TAG"; exit 1; }

# 删同名旧资产
OLD_ID="$(curl -s -H "Authorization: Bearer $TOKEN" "https://api.github.com/repos/$REPO/releases/$RELEASE_ID/assets" | python3 -c "
import json,sys
name='$ASSET_NAME'
for a in json.load(sys.stdin):
    if a['name']==name: print(a['id'])
" 2>/dev/null || true)"
if [ -n "$OLD_ID" ]; then
  echo "删除旧资产 id=$OLD_ID ($ASSET_NAME)"
  curl -s -X DELETE -H "Authorization: Bearer $TOKEN" "https://api.github.com/repos/$REPO/releases/assets/$OLD_ID" -w "  HTTP %{http_code}\n" -o /dev/null
else
  echo "无同名旧资产，直接上传"
fi

# 上传新资产
echo "上传 $LOCAL_FILE -> $REPO release $TAG ($ASSET_NAME)"
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/octet-stream" \
  --data-binary @"$LOCAL_FILE" \
  "https://uploads.github.com/repos/$REPO/releases/$RELEASE_ID/assets?name=$ASSET_NAME" \
  | python3 -c "import json,sys; r=json.load(sys.stdin); print('上传完成 asset id:', r.get('id'), '| name:', r.get('name'), '| size:', r.get('size'))"
