#!/bin/bash
# Wait for the in-flight classification and rediscovery to finish, then classify
# whatever the rediscovery added.
#
# The classification run in flight started against the pre-rediscovery corpus,
# so it will not cover the ~3,500 DOIs discovery has since found. Rather than
# restart it and discard two hours of work, this waits and then runs against the
# refreshed corpus. The per-paper cache means only the genuinely new papers cost
# anything; everything already classified at the current prompt version is read
# back for free.

set -u
REPO="/Users/bdichter/dev/find_reuse"
CLASSIFY_PID="${1:?usage: $0 <classify_pid> <rediscover_pid>}"
REDISCOVER_PID="${2:?usage: $0 <classify_pid> <rediscover_pid>}"
LOG="$REPO/logs/classify_gap.log"
REFRESHED="$REPO/output/all_dandiset_papers_refreshed.json"

cd "$REPO" || exit 1
mkdir -p logs

say() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

# Keep the machine awake for our own run too. The caffeinate processes watching
# the two upstream jobs exit when those jobs do.
caffeinate -i -m -w $$ >/dev/null 2>&1 &

say "waiting for classification ($CLASSIFY_PID) and rediscovery ($REDISCOVER_PID)"
while kill -0 "$CLASSIFY_PID" 2>/dev/null || kill -0 "$REDISCOVER_PID" 2>/dev/null; do
    sleep 30
done
say "both upstream jobs finished"

if [ ! -f "$REFRESHED" ]; then
    say "ABORT: $REFRESHED does not exist; rediscovery did not complete its fetch"
    exit 2
fi

before=$(find "$REPO/.fulltext_classification_cache" -name '*.json' | wc -l | tr -d ' ')
say "cache holds $before results before the gap run"

say "classifying against the refreshed corpus"
python3 -W ignore -m src.shared.run_fulltext_classification \
    --results-file "$REFRESHED" \
    --limit 20000 --workers 12 --max-tokens 12000 >> "$LOG" 2>&1
status=$?
say "gap classification exited with status $status"

after=$(find "$REPO/.fulltext_classification_cache" -name '*.json' | wc -l | tr -d ' ')
say "cache holds $after results after ($((after - before)) newly classified)"
say "done"
