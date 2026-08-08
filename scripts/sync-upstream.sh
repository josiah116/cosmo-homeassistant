#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

if [[ -n "$(git status --porcelain)" ]]; then
  printf 'Refusing to sync: working tree is not clean.\n' >&2
  exit 1
fi

if ! git remote get-url upstream >/dev/null 2>&1; then
  git remote add upstream https://github.com/kunalkhosla/cosmo-homeassistant.git
fi

git fetch upstream main --tags
git switch main
git merge --no-edit upstream/main
python3 scripts/validate.py

printf '\nUpstream merge validated locally. Review with:\n'
printf '  git log --oneline --decorate --graph upstream/main..main\n'
printf '  git diff upstream/main...main\n'
printf 'Then publish with: git push origin main --follow-tags\n'
