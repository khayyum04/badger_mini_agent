#!/usr/bin/env bash
# Submission-card numbers for a job dir:  ./badger/scripts/score.sh badger/jobs/<job-name>
set -euo pipefail
JOB="${1:?usage: score.sh <job-dir>}"
files=$(find "$JOB" -mindepth 2 -name result.json)
tb=$(echo "$files" | xargs jq -s '[.[] | .verifier_result.rewards.reward // 0] | add / length')
tok=$(echo "$files" | xargs jq -s '[.[] | (.agent_result.n_input_tokens // 0) + (.agent_result.n_output_tokens // 0)] | add')
n=$(echo "$files" | wc -l | xargs)
echo "tasks=$n tb_score=$tb total_tokens=$tok leaderboard_score=$(jq -n "$tb - 0.01 * $tok / 1000000")"
