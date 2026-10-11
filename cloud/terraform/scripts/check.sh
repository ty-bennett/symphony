#!/usr/bin/env bash
# Offline checks for every Terraform root and module under cloud/terraform.
# Makes no AWS calls: providers are mocked in tests and backends are skipped.
set -euo pipefail

cd "$(dirname "$0")/.."

terraform fmt -check -recursive

dirs=()
for dir in modules/*/ */; do
  dir="${dir%/}"
  case "$dir" in
    modules | scripts) continue ;;
  esac
  compgen -G "$dir/*.tf" >/dev/null || continue
  dirs+=("$dir")
done

for dir in "${dirs[@]}"; do
  echo "==> $dir"
  terraform -chdir="$dir" init -backend=false -input=false -lockfile=readonly >/dev/null
  terraform -chdir="$dir" validate -no-color
  if compgen -G "$dir/tests/*.tftest.hcl" >/dev/null; then
    terraform -chdir="$dir" test -no-color
  fi
done

python3 scripts/static_checks.py
