# Spec: experiment logging and run comparison

**Status:** agreed design, not implemented.

**Related:**

- [full-89-0924-fix-priorities.md](../docs/research/full-89-0924-fix-priorities.md). This
  spec implements "Fix 0" and the measurement workflow described in "How to measure".
- [full-89-0924-failure-analysis.md](../docs/research/full-89-0924-failure-analysis.md).
  Its Appendix C defines the metrics used here.

## Contents

- [Problem](#problem)
- [Technical plan](#technical-plan)
- [Alternatives](#alternatives)
- [Detailed implementation](#detailed-implementation)
  - [1. Agent telemetry](#1-agent-telemetry)
  - [2. Task lists](#2-task-lists)
  - [3. Run recipe and launcher](#3-run-recipe-and-launcher)
  - [4. The experiments CLI](#4-the-experiments-cli)
    - [4.1 summarize](#41-summarize)
    - [4.2 compare](#42-compare)
    - [4.3 Ledger: record, verdict, render](#43-ledger-record-verdict-render)
    - [4.4 dashboard](#44-dashboard)
  - [5. AI analysis prompt](#5-ai-analysis-prompt)
  - [6. Docs and config updates](#6-docs-and-config-updates)
  - [7. Tests and acceptance checks](#7-tests-and-acceptance-checks)
- [Out of scope](#out-of-scope)
- [Decisions made in this spec](#decisions-made-in-this-spec)

---

## Problem

The team's workflow is: make one change to the agent, run a set of tasks, then ask an AI to say
whether the change helped. Today that fails in three ways.

- **No record of what a run tested.** A job directory doesn't say which commit, model, settings
  or task list produced it.
- **No per-turn facts.** Whether a turn ran out of thinking budget (an "overrun") has to be guessed
  from reply length, because `finish_reason` isn't logged.
- **No fixed metric definitions.** Every analysis re-derives numbers from about 33MB of transcripts
  with throwaway scripts. Two analyses of two runs can't be compared, and there's no history of
  what was tried.

## Technical plan

Record facts at the source, compute metrics once with committed code, and give humans and AI small
structured files to read.

```
agent (per turn)           launcher (per run)          experiments.py (after run)
─────────────────          ──────────────────          ──────────────────────────
turn_log + config   ──►    experiment.json      ──►    summary.json  (one run)
in result.json             (the run's recipe)          comparison.md (run vs baseline)
                                                       ledger.jsonl → ledger.md (all runs, committed)
                                                       dashboard.html (numbers only)
```

1. **Agent telemetry.** Each turn, the agent appends a small record to `context.metadata["turn_log"]`
   and snapshots its settings in `context.metadata["config"]`. This is logging only: the agent's
   behavior must not change.
2. **Run recipe.** A new launcher, `run_experiment.sh`, records what a run is: id, description,
   baseline, declared changes, commit, settings and task list. It saves this as
   `experiment.json` in the job directory.
3. **One CLI with fixed definitions**, `starter/scripts/experiments.py`:
   - `summarize` a job
   - `compare` two jobs
   - keep the committed **ledger**: `record`, `verdict`, `render`
   - build the **dashboard**
4. **An AI prompt** that tells the analyst to read the small files first and open transcripts only
   for tasks that flipped.

All metrics are judged against a **noise level**: how many tasks flip between two identical runs,
measured once by running the baseline twice.

## Alternatives

| Option | Why not |
|---|---|
| Keep asking the AI to analyze raw transcripts each time | Definitions drift between analyses, each one is expensive, and nothing is comparable over time |
| Harbor's built-in viewer (`harbor view jobs`) | One job at a time. No cross-run comparison, no recipe, no ledger |
| External tracking (MLflow, Weights & Biases) | A new dependency and service, and a risk of shipping transcripts or keys off the machine. Overkill for about 20 runs |
| Compute summaries inside the agent at the end of `run()` | `run()` can be killed by the timeout, and run-level metrics need every trial |
| Separate scripts (`summarize_job.py`, `compare_runs.py`, …) | The same definitions would have to be duplicated. One module keeps them in one place |
| Log the full thinking text per turn | Size, and possibly sensitive content. Lengths and token counts are enough |

## Detailed implementation

### 1. Agent telemetry

**Invariant: telemetry must not change agent behavior.** The messages the model sees, the commands
it runs and the stopping conditions must be exactly what they are today. That includes the
`reasoning_content` fallback, which Fix 1 changes separately. This keeps baselines comparable.

#### 1.1 `starter/agent/llm.py`

`LLMClient.chat()` returns a `ChatResult` dataclass instead of `(text, usage)`:

| Field | Type | Meaning |
|---|---|---|
| `text` | `str` | Exactly what `chat()` returns today, **including** the fallback to `reasoning_content` |
| `usage` | `dict` | Unchanged: `prompt_tokens`, `completion_tokens` |
| `finish_reason` | `str \| None` | `response.choices[0].finish_reason` |
| `content_chars` | `int` | `len(message.content or "")`, measured **before** the fallback |
| `reasoning_chars` | `int` | `len(getattr(message, "reasoning_content", None) or "")` |
| `reasoning_tokens` | `int \| None` | `usage.completion_tokens_details.reasoning_tokens` if the server returns it, else `None` |
| `fallback_used` | `bool` | `True` when `content` was empty and `reasoning_content` was substituted |
| `has_tool_calls` | `bool` | `bool(getattr(message, "tool_calls", None))` |

Also add `max_tokens` as a public attribute (it already exists as `self.max_tokens`) so the agent
can use it in the overrun check.

#### 1.2 `starter/agent/tools.py`

- **`run_shell()` returns `(observation, ExecInfo)`.** `observation` must be **byte-identical** to
  today's string.
- **`ExecInfo` fields:**
  - `exit_code: int | None`: `None` when the command didn't complete
  - `timed_out: bool`: `True` when the `environment.exec` call raised (today's
    `[command did not complete: …]` path)
  - `output_chars: int`: length of stdout plus stderr, before truncation
  - `truncated: bool`: `True` if either stream exceeded `MAX_OBSERVATION_CHARS`
- **New helper `classify_action_source(text: str, action: Action) -> str | None`.** It returns
  `None` unless `action.kind == "shell"`. Otherwise it finds the match of `CODE_BLOCK_RE` that
  `parse_action` used, and returns:
  - `"after_closing_fence"` if an odd number of `` ``` `` sequences come before the match
  - otherwise `"tagged"` if the opening fence is followed by `bash`, `sh` or `shell`
  - otherwise `"untagged"`
- **`parse_action` itself is unchanged.**

#### 1.3 `starter/agent/agent.py`

- **Record a start time.** At the start of `run()`: `t0 = time.monotonic()`. All telemetry times are
  seconds since `t0`, rounded to 3 decimals.
- **Metadata layout.** `context.metadata` becomes the dict below, and it must still be reassigned
  every turn, as today (Harbor reads it on timeout):

  ```python
  {
      "turns": turns,
      "finished": finished,
      "messages": messages,
      "telemetry_schema": 1,
      "config": config,        # built once at the start of run()
      "turn_log": turn_log,    # list, appended every turn and mutated in place
  }
  ```

- **`config`** is built once, from the live objects and `os.environ`:

  ```python
  {
      "agent_version": self.version(),
      "model": llm.model,
      "llm": {"max_tokens": llm.max_tokens, "temperature": llm.temperature},
      "agent_env": {k: v for k, v in os.environ.items() if k.startswith("AGENT_")},
      "max_turns": MAX_TURNS,
      "command_timeout_sec": COMMAND_TIMEOUT_SEC,
  }
  ```

  **Never include `LLM_BASE_URL`, `LLM_API_KEY` or any other environment variable.** This is an
  allowlist, not a denylist.

- **One `turn_log` entry per LLM call** (per turn), appended right after `llm.chat()` returns:

  | Field | Type | Value |
  |---|---|---|
  | `turn` | int | Same value as `turns` for this iteration (1-based) |
  | `t_llm_start`, `t_llm_end` | float | Seconds since `t0` around the `llm.chat()` call |
  | `prompt_tokens`, `completion_tokens` | int \| null | From `usage` |
  | `reasoning_tokens` | int \| null | From `ChatResult` |
  | `finish_reason` | str \| null | From `ChatResult` |
  | `content_chars`, `reasoning_chars` | int | From `ChatResult` |
  | `fallback_used`, `has_tool_calls` | bool | From `ChatResult` |
  | `overrun` | bool | See the definition below |
  | `context_chars` | int | `sum(len(m["content"]) for m in messages)` just before the call |
  | `action_kind` | `"shell"` \| `"done"` \| `"none"` | From `parse_action` |
  | `action_source` | str \| null | From `classify_action_source` |
  | `exec` | object \| null | `null` unless a command ran; then `{t_start, t_end, exit_code, timed_out, output_chars, truncated}` is filled in after `run_shell` returns |

- **Overrun definition** (telemetry v1). Put it in one helper, `is_overrun(result, max_tokens)`:

  ```
  overrun = content_chars == 0 and (
      finish_reason == "length"
      or (finish_reason is None
          and completion_tokens is not None
          and completion_tokens >= 0.9 * max_tokens)
  )
  ```

  Fresh vs repeat is **not** stored per turn. `summarize` derives it (§4.1), so the definition lives
  in one place.

- **Order within a turn:**
  1. build `context_chars`
  2. time and call `llm.chat()`
  3. update token counts and `context.metadata` (as today)
  4. append the `turn_log` entry
  5. parse the action
  6. if it's a shell command, time `run_shell`, then fill `entry["exec"]`

  Because `turn_log` is mutated in place, a mid-turn timeout still leaves a consistent partial
  entry.

- `scripts/build_dashboard.py` reads only `turns`, `finished` and `messages`, so it needs no change.

### 2. Task lists

New directory `starter/eval/task_lists/`. One file per list, named `<list-id>.txt`.

- One task per line: `<task-name> [group]`. The group is optional and is either `target` or
  `regression`.
- `#` starts a comment. Blank lines are ignored.
- The reserved list id **`all`** means the whole dataset: there's no file, and no `-i` flags are
  passed.

Create **`starter/eval/task_lists/subset-30.txt`** with these 30 tasks:

- **`target` (20):**
  - `circuit-fibsqrt`
  - `regex-chess`
  - `winning-avg-corewars`
  - `fix-ocaml-gc`
  - `feal-differential-cryptanalysis`
  - `schemelike-metacircular-eval`
  - `video-processing`
  - `custom-memory-heap-crash`
  - `make-mips-interpreter`
  - `mailman`
  - `break-filter-js-from-html`
  - `path-tracing-reverse`
  - `path-tracing`
  - `polyglot-rust-c`
  - `protein-assembly`
  - `write-compressor`
  - `tune-mjcf`
  - `polyglot-c-py`
  - `prove-plus-comm`
  - `raman-fitting`
- **`regression` (10):**
  - `portfolio-optimization`
  - `feal-linear-cryptanalysis`
  - `reshard-c4-data`
  - `distribution-search`
  - `extract-elf`
  - `financial-document-processor`
  - `regex-log`
  - `torch-tensor-parallelism`
  - `bn-fit-modify`
  - `cancel-async-tasks`

The reasons for this selection are in the fix-priorities doc ("Experiment subset").
`starter/eval/public_subset.txt` and `run_subset.sh` are unchanged.

### 3. Run recipe and launcher

#### 3.1 `experiment.json` (the recipe)

Written into the job directory. Schema version 1:

```json
{
  "recipe_schema": 1,
  "run_id": "c2-temp06",
  "kind": "experiment",
  "description": "Warmer sampling: temperature 0.6",
  "baseline_run_id": "step1-base",
  "changes": ["LLM_TEMPERATURE"],
  "created_at": "2026-10-02T14:03:11Z",
  "git": {"commit": "a1b2c3d…", "dirty": false, "diff_stat": ""},
  "dataset": "terminal-bench@2.0",
  "task_list_id": "subset-30",
  "tasks": ["circuit-fibsqrt", "…"],
  "settings": {
    "LLM_MODEL": "…", "LLM_MAX_TOKENS": "8192", "LLM_TEMPERATURE": "0.6",
    "AGENT_MAX_TURNS": "100", "AGENT_COMMAND_TIMEOUT_SEC": "60"
  },
  "harbor": {"n_concurrent": 3, "max_retries": 2, "extra_args": []}
}
```

- **`kind`** is one of:
  - `baseline`: the reference other runs compare against
  - `experiment`: one change against a baseline
  - `noise`: an exact repeat of a baseline, used to measure the noise level
  - `milestone`: a full run for reporting
- **`changes`** lists the setting keys (or `"code"`) that the run *intends* to differ from its
  baseline. It is required for `experiment`, and empty for `noise`.
- **`settings`** is an allowlist. It contains `LLM_MODEL`, `LLM_MAX_TOKENS`, `LLM_TEMPERATURE`
  (plus any `LLM_TOP_P` if that setting is added later) and every `AGENT_*` variable. Values are
  resolved the same way the agent resolves them: load `starter/.env` with `override=True` on top of
  the current environment. **It never contains `LLM_BASE_URL` or `LLM_API_KEY`.**

#### 3.2 `starter/scripts/run_experiment.sh`

```bash
./scripts/run_experiment.sh --id c2-temp06 --kind experiment \
    --desc "Warmer sampling: temperature 0.6" --baseline step1-base \
    --tasks subset-30 --changes LLM_TEMPERATURE [-- <extra harbor args>]
```

Steps:

1. **Set up.** `cd` to `starter/` (like the existing scripts) and `set -euo pipefail`.
2. **Validate the arguments:**
   - `--id` matches `[a-z0-9][a-z0-9._-]*`
   - `jobs/<id>` doesn't exist
   - `--id` isn't already in the ledger
   - `--baseline` is required unless `--kind baseline`
   - `--changes` is required for `--kind experiment`
3. **Build the recipe.** Run `python scripts/experiments.py recipe …` with the same arguments. It
   writes `jobs/<id>.experiment.json` (next to the future job directory), including the git state
   (`git rev-parse HEAD`, `git status --porcelain` for `dirty`, and `git diff --stat` for
   `diff_stat`).
4. **Run Harbor.** Build `-i` flags from the task list (none for `all`) and run:

   ```bash
   harbor run -d terminal-bench@2.0 --agent-import-path agent.agent:BaselineAgent \
     --job-name <id> --jobs-dir jobs -n "${N_CONCURRENT:-3}" \
     --max-retries 2 --retry-include RuntimeError --retry-include EnvironmentStartTimeoutError \
     <-i flags> <extra harbor args>
   ```

   Retries cover **infrastructure failures only**. Never retry agent failures, which would turn a
   single run into best-of-N.
5. **Save the recipe, even if Harbor fails.** Use `trap` to move `jobs/<id>.experiment.json` to
   `jobs/<id>/experiment.json`. If the job directory doesn't exist, leave the file in place and
   print a warning.
6. **Post-process.** Run:
   - `python scripts/experiments.py summarize jobs/<id>`
   - if `--baseline` was given: `python scripts/experiments.py compare <baseline-id> <id>`
   - `python scripts/experiments.py record jobs/<id>`
   - `python scripts/experiments.py render`

   Print the paths of `summary.json` and `comparison.md`.

For the hosted endpoint, wrap the launcher the same way as the other scripts:
`op run --env-file=starter/.env.op -- ./starter/scripts/run_experiment.sh …`.

### 4. The experiments CLI

`starter/scripts/experiments.py` uses only the standard library, plus `python-dotenv`, which is
already a dependency, for `recipe`. Subcommands:

| Subcommand | Arguments | Output |
|---|---|---|
| `recipe` | same flags as the launcher | `jobs/<id>.experiment.json` |
| `summarize` | `JOB_DIR [--task-list ID]` | `JOB_DIR/summary.json`, plus a short stdout table |
| `compare` | `BASE CAND [--noise N] [-o PATH]` | `CAND_JOB_DIR/comparison.md` (default), also printed |
| `record` | `JOB_DIR` | adds or updates a row in `starter/experiments/ledger.jsonl` |
| `verdict` | `RUN_ID {keep,drop,unclear} [--note TEXT]` | updates that ledger row |
| `render` | — | `starter/experiments/ledger.md` |
| `dashboard` | `[-o PATH]` | `starter/experiments/dashboard.html` (default) |

`BASE` and `CAND` accept either a run id, resolved through the ledger's `job_dir`, or a job
directory path.

All thresholds and definitions are module-level constants in one block at the top of the file,
under `DEFINITIONS_VERSION = 1`.

#### 4.1 summarize

**Input.** Every `JOB_DIR/*/result.json` (the job-level `JOB_DIR/result.json` is ignored). Groups
come from the recipe's `task_list_id`, from `--task-list`, or are absent. v1 **errors** if any task
appears more than once in a job (multiple attempts are out of scope).

**Outcome of each trial.** Apply these rules in order; the first match wins.

| # | Rule | Outcome |
|---:|---|---|
| 1 | `verifier_result.rewards.reward == 1.0` | `pass` |
| 2 | exception type is `RuntimeError` or `EnvironmentStartTimeoutError`, **and** `metadata.turns` is missing or 0 | `infra` |
| 3 | exception type is `BadRequestError` **and** its message contains `ContextWindowExceeded` | `overflow` |
| 4 | exception type is `AgentTimeoutError` | `timeout` |
| 5 | any other exception | `crash_other` |
| 6 | `metadata.finished` is true | `done_wrong` |
| 7 | `metadata.turns >= config.max_turns` (or 100 if there's no config) | `turn_cap` |
| 8 | anything else | `crash_other` |

Separately, set **`verifier_missing = verifier_result is null`**.

**Overrun detection, per trial.**

- **`finish_reason` method:** used when `metadata.turn_log` exists. An overrun is a
  `turn_log[i].overrun` that is true.
- **`heuristic_v1` method:** used for older runs. An overrun is an assistant message with
  `len(content) >= 10000` that does **not** start with `"\n"`.
- The run-level `overrun_detection` field is whichever method all trials used, or `"mixed"`.

**Turn-level definitions:**

- **Turns** are assistant turns, in order. Use the `turn_log` entries or, for older runs, the
  assistant messages.
- **Fresh overrun:** an overrun whose previous turn in the same trial was not an overrun. The first
  turn counts as fresh.
- **Repeat overrun:** an overrun whose previous turn was an overrun.
- **`repeat_rate`** = repeats ÷ (turns whose previous turn was an overrun). `null` if the
  denominator is 0.
- **`fresh_per_100_turns`** = 100 × fresh ÷ total turns.
- **Nudges:** turns with `action_kind == "none"`, or for older runs, user messages containing the
  first 40 characters of `NUDGE_MESSAGE`.
- **Command timeouts:** entries with `exec.timed_out == true`, or for older runs, user messages
  containing `[command did not complete`.

**Run-level definitions:**

- **`tb_score`** = mean reward over **all** trials in the job, with a missing reward counted as 0.
  This is the README's formula.
- **`pass_rate_ran`** = passes ÷ trials whose outcome isn't `infra`.
- **`leaderboard_score`** = `tb_score − 0.01 × tokens_total / 1e6`. Computed only when the job has
  exactly 89 trials; `null` otherwise.
- **Token totals** are `n_input_tokens` + `n_output_tokens` from `result.json`, with missing values
  counted as 0.
- **Medians:**
  - `median_per_pass`: over passing trials
  - `median_per_fail`: over trials that ran (not `infra`) and didn't pass
- **`output_tokens_per_sec`** = Σ `completion_tokens` ÷ Σ (`t_llm_end − t_llm_start`), over all
  `turn_log` entries that have both. `null`, with `method: "unavailable"`, when there's no
  telemetry.
- **`config_check`:** when a recipe exists, compare the recipe's `settings.LLM_MODEL` and every
  `settings.AGENT_*` value with each trial's `metadata.config`. Record any mismatch in `notes` and
  set `consistent: false`.

**`summary.json` schema:**

```json
{
  "definitions_version": 1,
  "run_id": "c2-temp06",
  "job_dir": "starter/jobs/c2-temp06",
  "task_list_id": "subset-30",
  "overrun_detection": "finish_reason",
  "counts": {
    "trials": 30, "ran": 30, "verifier_missing": 0,
    "outcomes": {"pass": 0, "done_wrong": 0, "timeout": 0, "turn_cap": 0,
                 "overflow": 0, "crash_other": 0, "infra": 0}
  },
  "score": {"pass_count": 0, "tb_score": 0.0, "pass_rate_ran": 0.0, "leaderboard_score": null},
  "tokens": {"input": 0, "output": 0, "total": 0, "median_per_pass": null, "median_per_fail": null},
  "turns": {"total": 0, "overruns": 0, "fresh": 0, "repeat": 0, "turns_after_overrun": 0,
            "fresh_per_100_turns": 0.0, "repeat_rate": null, "nudges": 0, "command_timeouts": 0},
  "speed": {"output_tokens_per_sec": null, "method": "telemetry"},
  "groups": {"target": {"tasks": 20, "ran": 20, "pass": 0},
             "regression": {"tasks": 10, "ran": 10, "pass": 0}},
  "config_check": {"consistent": true, "notes": []},
  "tasks": [
    {"task": "raman-fitting", "trial": "raman-fitting__73kHRWi", "group": "target",
     "outcome": "timeout", "reward": 0.0, "verifier_missing": false,
     "tests_passed": 0, "tests_total": 3, "turns": 19, "tokens": 597152,
     "overruns": 11, "fresh": 1, "repeat": 10, "nudges": 12, "agent_seconds": 900.0}
  ]
}
```

- **Test counts** come from `verifier/ctrf.json` (`results.summary.passed` and `.tests`). They are
  `null` if that file is missing.
- **`agent_seconds`** = `agent_execution.finished_at − started_at`.
- **Stdout:** print a short table with outcomes, pass counts per group, tokens, `fresh_per_100_turns`
  and `repeat_rate`.

#### 4.2 compare

**Guards.** Load both recipes and summaries (running `summarize` first if `summary.json` is
missing). Then check:

- **Undeclared differences:** every key in `settings`, plus `dataset`, `task_list_id` and
  `harbor.n_concurrent`, that differs between the two runs **and** isn't listed in the candidate's
  `changes`. Print these as **WARNING** lines at the top of the report.
- **Hard warnings** (still produce the report, but mark it `comparable: false`):
  - different `LLM_MODEL`
  - different task lists
  - different `definitions_version`
  - different `overrun_detection`
  - either run's `config_check.consistent` is false
- **Missing recipe:** if a run has no recipe, print "recipe unavailable; guards skipped".

**Noise level,** in order of precedence:

1. `--noise N`
2. otherwise, the most recent ledger row with `kind == "noise"` and the same `task_list_id`, using
   its `noise_flips`
3. otherwise, assume `NOISE_DEFAULT = 2` and label it "assumed; no noise run recorded"

**`comparison.md` sections:**

1. **Header:** both run ids, descriptions, commits, the declared changes, and
   `comparable: true/false`.
2. **Recipe differences:** a table of key, baseline value, candidate value, and declared (yes/no).
3. **Metrics:** a table of metric, baseline, candidate, Δ and hint, for:
   - `tb_score`, `pass_count`, target pass, regression pass
   - `tokens.total`, `median_per_pass`, `median_per_fail`
   - `fresh_per_100_turns`, `repeat_rate`, `nudges`, `command_timeouts`
   - `output_tokens_per_sec`
   - each outcome count
4. **Pass/fail flips,** over tasks present in both runs:
   - **Fixed** (fail → pass) and **Broken** (pass → fail), each task with its group and both trial
     directory paths
   - **Net** = fixed − broken
5. **Overrun changes by task:** the 5 largest increases and the 5 largest decreases in fresh
   overruns.
6. **Hints.** These are generated automatically and are **not decisions**:

| Hint | Rule |
|---|---|
| Fresh-overrun rate changed notably | abs Δ ≥ `FRESH_ABS = 1.0` per 100 turns **and** relative Δ ≥ `FRESH_REL = 0.20` |
| Tokens changed notably | relative Δ ≥ `TOKENS_REL = 0.10` |
| Endpoint speed differed | relative Δ in `output_tokens_per_sec` ≥ `SPEED_REL = 0.25` → "timeouts may not be comparable" |
| Pass/fail within noise | broken ≤ noise **and** abs(net) ≤ noise |
| Regression | any broken task in the `regression` group → always listed first |

`compare` also writes `comparison.json`, with the same content in machine-readable form, next to
`comparison.md`. When the candidate is a `noise` run, it sets `noise_flips = fixed + broken`, and
`record` stores that value.

#### 4.3 Ledger: record, verdict, render

**Location:** `starter/experiments/ledger.jsonl`, **committed to git**. Each line is one run:

```json
{"ledger_schema": 1, "run_id": "c2-temp06", "kind": "experiment", "date": "2026-10-02",
 "commit": "a1b2c3d", "dirty": false, "description": "Warmer sampling: temperature 0.6",
 "baseline_run_id": "step1-base", "changes": ["LLM_TEMPERATURE"], "task_list_id": "subset-30",
 "job_dir": "starter/jobs/c2-temp06",
 "headline": {"tb_score": 0.0, "pass_count": 0, "trials": 30, "target_pass": 0,
              "regression_pass": 0, "tokens_total": 0, "fresh_per_100_turns": 0.0,
              "repeat_rate": null, "nudges": 0, "output_tokens_per_sec": null},
 "comparable": true, "noise_flips": null, "verdict": "pending", "verdict_note": ""}
```

- **`record JOB_DIR`** builds the row from `experiment.json`, `summary.json` and (if present)
  `comparison.json`. It **replaces** an existing row with the same `run_id`, rewriting the whole
  file, which is small. The default verdict depends on the kind:

  | Kind | Default verdict |
  |---|---|
  | `experiment` | `pending` |
  | `baseline`, `noise`, `milestone` | `n/a` |

  For a job with no recipe (for example `full-89-0924`), `record` takes the run id from the
  directory name, sets `kind: "milestone"`, and leaves `commit` and `changes` null.
- **`verdict RUN_ID {keep,drop,unclear} --note TEXT`** updates `verdict` and `verdict_note`. It
  errors if the run id isn't in the ledger.
- **`render`** writes `starter/experiments/ledger.md` (committed). It contains:
  - a one-paragraph header saying the file is generated and how to regenerate it
  - one table, in ledger order (oldest first), with columns: run, date, kind, description,
    baseline, changes, target pass, regression pass, fresh / 100 turns, tokens, verdict, note
  - under the table, the current noise level for each task list

#### 4.4 dashboard

`dashboard` writes **one static, dependency-free HTML file**: inline CSS and inline SVG, no
external scripts or fonts, following `build_dashboard.py`'s approach. The default path is
`starter/experiments/dashboard.html`, which is **gitignored**. It contains **numbers only: never
transcript text, commands or model output**, so it's safe to share.

It reads the ledger and each row's `job_dir/summary.json`. Rows whose summary is missing are shown
with "summary unavailable".

**Sections:**

1. **Trends:** four small line charts in ledger order, one point per run, labeled with the run id:
   - `fresh_per_100_turns`
   - target pass
   - regression pass
   - `tokens_total`

   Baseline and noise runs get distinct markers. Only rows that share the chart's `task_list_id`
   are plotted, with a selector if there's more than one task list.
2. **Task grid:** rows are tasks, columns are runs. Each cell is pass, fail or not run, with the
   fresh-overrun count as a small number and a tooltip showing the outcome.
3. **Run cards:** one card per run, newest first. Each shows the recipe differences from its
   baseline, the headline metrics against the baseline, the verdict with its note, and the
   `comparable` flag.

### 5. AI analysis prompt

Create `starter/docs/research/prompts/compare-run.md`: a prompt to paste into an AI session at the
repo root after a run finishes. It must instruct the analyst to:

1. **Read, in this order:**
   - `starter/experiments/ledger.md`
   - the candidate's `experiment.json`
   - `comparison.md`
   - both runs' `summary.json`

   **Do not** open transcripts yet.
2. **State whether the comparison is valid.** Check `comparable`, the undeclared differences and
   the endpoint-speed hint. If it isn't valid, stop and say what to re-run.
3. **Judge the change against its purpose.**
   - Cause-track changes are judged mainly on `fresh_per_100_turns`.
   - Harness changes are judged on tokens and the repeat rate.
   - Pass/fail changes count only beyond the noise level.
   - Any broken `regression` task must be explained.
4. **Open transcripts only for flipped tasks,** and at most 5 of them. Use `result.json`'s
   `metadata.turn_log` first, then `messages`, to explain *why* each one flipped.
5. **Give a verdict:** keep, drop or unclear, in one short paragraph, plus a suggested next
   experiment. Then run `python starter/scripts/experiments.py verdict <run-id> <verdict> --note
   "<one line>"` and `render`.
6. **Never change agent code** during analysis, and never add task-specific logic (competition
   rule).

### 6. Docs and config updates

| File | Change |
|---|---|
| `.gitignore` (root) | add `starter/experiments/dashboard.html` |
| `starter/CLAUDE.md` | list `experiments/` (ledger and dashboard) and `eval/task_lists/`; add "run experiments with `run_experiment.sh`" to "How it fits in" |
| `starter/scripts/CLAUDE.md` | document `run_experiment.sh` and `experiments.py` (subcommands, outputs); add a gotcha that `experiments.py` owns the metric definitions (`DEFINITIONS_VERSION`) and that `classify_action_source`/`CODE_BLOCK_RE` changes affect telemetry |
| `starter/agent/CLAUDE.md` | document the `metadata` keys `telemetry_schema`, `config`, `turn_log`, and the "telemetry must not change behavior" invariant |
| `starter/experiments/ledger.md` | created by the first `render` |

### 7. Tests and acceptance checks

The repo has no test suite yet. Add `starter/tests/test_experiments.py` using **`unittest`** (no new
dependency), runnable as `python -m unittest discover starter/tests`. The tests build small
synthetic job directories in a temp dir. They must cover:

1. **Outcomes:** each of the 8 classification rules in §4.1.
2. **Overruns:** fresh/repeat counting and `repeat_rate` on a hand-built turn sequence (e.g.
   normal, overrun, overrun, normal, overrun gives fresh 2, repeat 1, a denominator of 2, and a
   rate of 0.5), for both detection methods.
3. **Parser classification:** `classify_action_source` for tagged, untagged and after-closing-fence
   text.
4. **Behavior unchanged:**
   - `run_shell`'s observation string is identical to the pre-change format for a fake
     `ExecResult`: normal output, empty output, a truncated stream, and the exception path.
   - `ChatResult.text` equals today's `chat()` text, including the fallback case.
5. **compare:**
   - flips and net
   - an undeclared-difference warning
   - `comparable: false` when the model differs
   - the noise precedence: the flag, then the ledger, then the default
6. **Ledger:** a `record`, then a second `record` of the same run id replacing it, then `verdict`,
   then `render`.

**Acceptance on real data.** `python starter/scripts/experiments.py summarize <path-to>/full-89-0924`
must reproduce these values using `heuristic_v1`:

| Field | Expected |
|---|---|
| `counts.trials` | 89 |
| `score.pass_count` | 28 |
| `score.tb_score` | 0.3146 (4 d.p.) |
| `tokens.input` / `output` / `total` | 87,505,605 / 5,743,535 / 93,249,140 |
| `score.leaderboard_score` | −0.6179 (4 d.p.) |
| outcomes | pass 28, infra 21, timeout 26, overflow 6, done_wrong 6, turn_cap 2, crash_other 0 |
| `turns.total` | 2,420 |
| `turns.overruns` / `fresh` / `repeat` | 508 / 150 / 358 |
| `turns.turns_after_overrun` / `repeat_rate` | 482 / 0.743 |
| `turns.fresh_per_100_turns` | 6.20 |
| `turns.nudges` / `command_timeouts` | 238 / 31 |

The failure-analysis doc quotes "73%" for the repeat rate, from a slightly different grouping.
From now on, `experiments.py`'s 0.743 is the canonical definition.

**Acceptance for telemetry.** Run one task (for example `fix-git`) with `run_experiment.sh --kind
baseline --tasks <a 1-task list>` and check:

- `result.json` has `telemetry_schema: 1`, a `config` with no `LLM_BASE_URL` or `LLM_API_KEY`, and
  one `turn_log` entry per turn with `exec` filled in for every shell turn
- `summary.json` reports `overrun_detection: "finish_reason"`
- `comparison.md` is produced when a second one-task run is compared against it

## Out of scope

- **Fix 1** (stop running and keeping cut-off thinking) and all other agent behavior changes.
  Those are measured *with* this tooling afterwards.
- **Changes to the per-job transcript dashboard** (`build_dashboard.py`), for example showing
  per-turn badges from `turn_log`. That's a possible follow-up.
- **Multiple attempts per task** (`harbor run -k`). v1 errors on duplicates.
- **Statistical tests beyond the noise level.** With one trial per task, the noise-run heuristic is
  the agreed approach.
- **Uploading results anywhere.** Everything stays local, except the ledger files.

## Decisions made in this spec

These were not settled in discussion and are fixed here so the spec can be implemented:

- **One CLI, not several scripts.** `experiments.py` holds all subcommands, to keep the definitions
  in one place.
- **Where things live:**
  - the recipe is written by the launcher (via `experiments.py recipe`), not by the agent, because
    the agent must not read host state such as git
  - the agent's `metadata.config` is the cross-check
  - `.env` is resolved the same way the agent does it (`override=True`)
- **Task lists** use an optional group column in `starter/eval/task_lists/<id>.txt`, and `all` is a
  reserved id.
- **Launcher defaults:** retries cover infrastructure exception types only, with
  `--max-retries 2`. Default concurrency is 3.
- **Overrun fallback rule:** when there's no `finish_reason`, a turn counts as an overrun if the
  answer is empty and `completion_tokens` is at least 90% of `max_tokens`.
- **Noise default:** 2 flips when no noise run is recorded, labeled as assumed.
- **What's committed:** the ledger (`.jsonl` and `.md`) is committed; the dashboard HTML is not.
