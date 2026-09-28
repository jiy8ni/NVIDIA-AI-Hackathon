#!/bin/sh
set -eu

: "${HANDOFF_GIT_REF:?Set the verified remote branch or tag to clone.}"
: "${HANDOFF_REPOSITORY_URL:=https://github.com/jiy8ni/NVIDIA-AI-Hackathon.git}"
: "${HANDOFF_EXPECTED_COMMIT:=}"

workspace=/sandbox/work/HandoffOS
if [ -e "$workspace" ]; then
  echo "Refusing to reuse an existing sandbox worktree." >&2
  exit 64
fi

mkdir -p /sandbox/work
git clone --depth 1 --branch "$HANDOFF_GIT_REF" "$HANDOFF_REPOSITORY_URL" "$workspace"
cd "$workspace"

if [ -n "$HANDOFF_EXPECTED_COMMIT" ]; then
  actual="$(git rev-parse HEAD)"
  if [ "$actual" != "$HANDOFF_EXPECTED_COMMIT" ]; then
    echo "Cloned commit does not match HANDOFF_EXPECTED_COMMIT." >&2
    exit 65
  fi
fi

# Dependencies are installed when the image is built.  Copy only package
# artifacts into the clone; no token or .env file is copied into the sandbox.
cp -a /opt/handoff-deps/services/api/node_modules services/api/
cp -a /opt/handoff-deps/apps/web/node_modules apps/web/

exec node scripts/dev.mjs
