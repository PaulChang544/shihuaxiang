#!/usr/bin/env bash
# 增量发布到公开仓库（追加式历史，不用 --force）。
# 用法：PROXY=http://127.0.0.1:7897 tools/publish_github.sh "v0.6.3 开源快照" [--dry-run]
#   - 以本地分支 public-release（= 公开仓库最新提交）为基线，把当前 HEAD 的完整树作为
#     一个新提交追加到其上，再快进推送到公开仓库 main；公开历史中永远不含本机私有提交历史。
#   - 推送前做敏感信息兜底扫描：命中服务器地址/密钥路径/部署域名/超管账号名即中止。
set -euo pipefail
cd "$(dirname "$0")/.."

REMOTE=${GITHUB_REMOTE:-https://github.com/PaulChang544/shihuaxiang.git}
PROXY=${PROXY:-}

# 公开提交使用脱敏的作者身份（避免本机 git 配置里的真实邮箱/手机号出现在 GitHub 上）
export GIT_AUTHOR_NAME=${GIT_AUTHOR_NAME:-shihuaxiang-maintainer}
export GIT_AUTHOR_EMAIL=${GIT_AUTHOR_EMAIL:-maintainer@shihuaxiang.local}
export GIT_COMMITTER_NAME=$GIT_AUTHOR_NAME
export GIT_COMMITTER_EMAIL=$GIT_AUTHOR_EMAIL
MSG=${1:-"开源快照 $(date +%F)"}
DRY=${2:-}

GIT=(git)
[ -n "$PROXY" ] && GIT+=(-c "http.proxy=$PROXY" -c "credential.helper=manager")

# 1) 敏感信息兜底扫描（只看被跟踪文件）
if git grep -nE "47\.116\.33\.232|id_rsa_uyoung|mling7702\.xyz|MLING" -- . ':!uv.lock' ':!LICENSE' ':!tools/publish_github.sh' >/dev/null; then
  echo "✗ 发现敏感字串，已中止发布：" >&2
  git grep -nE "47\.116\.33\.232|id_rsa_uyoung|mling7702\.xyz|MLING" -- . ':!uv.lock' ':!LICENSE' ':!tools/publish_github.sh' || true
  exit 1
fi
if git ls-files --error-unmatch AGENTS.md >/dev/null 2>&1; then
  echo "✗ AGENTS.md 仍在版本库中被跟踪，请先 git rm --cached AGENTS.md" >&2
  exit 1
fi

# 2) 同步公开仓库基线（空仓库则走首次推送）
if ${GIT[@]} fetch -q "$REMOTE" "+main:public-release" 2>/dev/null; then
  TREE=$(git rev-parse "HEAD^{tree}")
  COMMIT=$(git commit-tree "$TREE" -p public-release -m "$MSG")
  git branch -f public-release "$COMMIT"
  echo "基线 $(git rev-parse --short public-release~1) → 新快照 $(git rev-parse --short public-release)"
  if [ "$DRY" = "--dry-run" ]; then
    echo "（dry-run：不推送）"
    exit 0
  fi
  ${GIT[@]} push "$REMOTE" "refs/heads/public-release:main"
else
  echo "远端为空，执行首次推送"
  [ "$DRY" = "--dry-run" ] && { echo "（dry-run：不推送）"; exit 0; }
  ${GIT[@]} push "$REMOTE" "HEAD:main"
fi
echo "✓ 已发布：$MSG"
