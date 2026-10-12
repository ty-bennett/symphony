#!/usr/bin/env bash
# One extract.py process per GPU; shards are independent (no torchrun/DDP). Re-run to resume.
set -euo pipefail
cd "$(dirname "$0")"
N=$(python -c "from common import CFG; print(CFG['extract']['num_shards'])")
mkdir -p out/logs
for i in $(seq 0 $((N - 1))); do
  CUDA_VISIBLE_DEVICES=$i python extract.py --shard "$i" --num-shards "$N" > "out/logs/extract_$i.log" 2>&1 &
done
fail=0
for job in $(jobs -p); do wait "$job" || fail=1; done
tail -n 2 out/logs/extract_*.log
[ "$fail" -eq 0 ] || { echo "some shards failed; see out/logs/ and re-run to resume"; exit 1; }
