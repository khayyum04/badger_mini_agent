#!/usr/bin/env bash
# Tasks from a list file (one name per line, # comments) on the full terminal-bench@2.0.
#   op run --env-file=badger/.env.op -- ./badger/scripts/run_subset.sh                          # eval/public_subset.txt
#   op run --env-file=badger/.env.op -- ./badger/scripts/run_subset.sh eval/experiment_subset.txt
# Extra harbor flags pass through after the file.
source "$(dirname "$0")/_common.sh"
SUBSET_FILE="${1:-eval/public_subset.txt}"; [ $# -gt 0 ] && shift
include_flags=()
while IFS= read -r line; do
  line="$(echo "${line%%#*}" | xargs)"
  [ -n "$line" ] && include_flags+=(-i "$line")
done < "$SUBSET_FILE"
[ ${#include_flags[@]} -gt 0 ] || { echo "error: no task names in $SUBSET_FILE" >&2; exit 1; }
harbor run -d terminal-bench@2.0 --agent "$AGENT" "${include_flags[@]}" \
  -n "${N_CONCURRENT:-2}" --jobs-dir jobs "$@"
