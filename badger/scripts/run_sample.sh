#!/usr/bin/env bash
# 10-task Terminal-Bench sample (terminal-bench-sample@2.0), or one task from it.
#   op run --env-file=badger/.env.op -- ./badger/scripts/run_sample.sh            # all 10
#   op run --env-file=badger/.env.op -- ./badger/scripts/run_sample.sh fix-git    # one task
# Extra harbor flags pass through after the task name.
source "$(dirname "$0")/_common.sh"
TASK="${1:-}"; [ $# -gt 0 ] && shift
harbor run -d terminal-bench-sample@2.0 --agent "$AGENT" ${TASK:+-i "$TASK"} \
  -n "${N_CONCURRENT:-1}" --jobs-dir jobs "$@"
