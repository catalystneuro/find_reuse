#!/bin/bash
# Re-run DANDI discovery once the OpenAlex daily quota has reset.
#
# The earlier run was capped at 100 per source and, today, lost the OpenAlex leg
# entirely to a 429. Europe PMC alone went from 100 to 222 once uncapped, and 18
# papers that only OpenAlex had ever found dropped out of the results. This run
# closes that gap.
#
# Waits until the target time, then confirms OpenAlex actually answers before
# spending the run: a search that starts while still throttled would silently
# produce another partial result, which is the failure this is meant to fix.

set -u
REPO="/Users/bdichter/dev/find_reuse"
FIRE_AT="${1:-20:10}"
LOG="$REPO/logs/scheduled_openalex_search.log"
OUT="$REPO/output/results_dandi_openalex.json"
MAX_WAIT_ROUNDS=12          # 12 x 5 min = 1 hour of grace past the target

cd "$REPO" || exit 1
mkdir -p logs

say() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

say "scheduled run armed, firing at $FIRE_AT"

# Sleep until the target wall-clock time.
target_epoch=$(date -j -f "%Y-%m-%d %H:%M:%S" "$(date '+%Y-%m-%d') $FIRE_AT:00" "+%s" 2>/dev/null)
now_epoch=$(date "+%s")
if [ -n "$target_epoch" ] && [ "$target_epoch" -gt "$now_epoch" ]; then
    delay=$((target_epoch - now_epoch))
    say "sleeping ${delay}s until $FIRE_AT"
    sleep "$delay"
fi

# Confirm the quota really has reset. Starting while throttled would produce a
# second silently-truncated candidate set, which is worse than not running.
round=0
while [ "$round" -lt "$MAX_WAIT_ROUNDS" ]; do
    code=$(curl -s -o /dev/null -w "%{http_code}" \
        "https://api.openalex.org/works?per_page=1&mailto=ben.dichter@catalystneuro.com")
    if [ "$code" = "200" ]; then
        say "OpenAlex responding (HTTP 200), starting search"
        break
    fi
    round=$((round + 1))
    say "OpenAlex still returning HTTP $code, retry $round/$MAX_WAIT_ROUNDS in 5 min"
    sleep 300
done

if [ "$code" != "200" ]; then
    say "ABORT: OpenAlex never recovered after $MAX_WAIT_ROUNDS attempts; not running"
    exit 2
fi

say "running discovery: all three sources, cap 1000, DANDI only"
python3 -W ignore -m src.direct_pipeline.find_reuse --discover \
    --max-results 1000 --archives "DANDI Archive" --deduplicate \
    -o "$OUT" --verbose >> "$LOG" 2>&1
status=$?
say "discovery exited with status $status"

if [ "$status" -eq 0 ]; then
    python3 - <<'PY' >> "$LOG" 2>&1
import json
from pathlib import Path
new = json.loads(Path('output/results_dandi_openalex.json').read_text())
prev = json.loads(Path('output/results_dandi_20260731.json').read_text())
def triples(d):
    return {(r.get('doi'), a, ds)
            for r in d['results']
            for a, i in (r.get('archives') or {}).items()
            for ds in i.get('dataset_ids', [])}
n, p = triples(new), triples(prev)
stats = new['query_metadata']['search_stats']
print('search stats:', {k: v.get('DANDI Archive') for k, v in stats.items()})
print(f"papers with refs: {len(prev['results'])} -> {len(new['results'])}")
print(f"triples: {len(p)} -> {len(n)} | new {len(n - p)} | lost {len(p - n)}")
PY
    say "comparison written; classify with: python3 -m src.shared.run_fulltext_classification --mode direct --results-file $OUT --limit 2000"
fi

say "done"
