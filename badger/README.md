# badger — mini-swe-agent for Efficient Coder (Terminal-Bench 2.0)

This fork of [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) adds a thin layer in
`badger/` that runs it as a Harbor **external agent** on Terminal-Bench 2.0, for the ML+X Efficient
Coder challenge (rules: [`competition/`](competition/)).

Everything upstream (`src/minisweagent/`) stays as it was, so `git fetch upstream && git merge
upstream/main` keeps working. Competition code lives in this directory.

## Layout

| Path | What it is |
|---|---|
| `badger_mini/harbor_agent.py` | `BadgerMiniAgent`, the Harbor agent. It runs mini-swe-agent's `DefaultAgent` in a worker thread, with a `HarborEnvironment` that sends each command to the task container through `environment.exec()` |
| `badger_mini/config/terminal_bench.yaml` | The prompts, step limit, command timeout and observation/format-error templates (adapted from upstream `mini.yaml`) |
| `.env.op` / `.env.op.example` | 1Password references for `LLM_BASE_URL` / `LLM_API_KEY`, plus `LLM_MODEL` |
| `.env` / `.env.example` | Non-secret tuning: `LLM_MAX_TOKENS`, sampling, `AGENT_*` |
| `scripts/` | `run_sample.sh`, `run_subset.sh`, `run_full.sh`, `check_endpoint.sh`, `score.sh` |
| `eval/` | `public_subset.txt` (placeholder list from the starter) and `experiment_subset.txt` (the 30-task A/B set from the research) |
| `docs/` | Starter guides (1Password, endpoint, Harbor, safety, troubleshooting) and `research/`, the analysis of the baseline's 89-task run |

## How it maps to the baseline's failures (`docs/research/`)

| Baseline weakness | What mini-swe-agent + this layer does |
|---|---|
| W1/W5: cut-off thinking gets run as a command and kept in history | Uses native tool calls, so nothing is parsed out of prose. A reply with no tool call (for example `finish_reason=length`) raises `FormatError`: the reply is **dropped from history** and replaced by a short "take one small step" message |
| W5: `TASK_COMPLETE` matched anywhere in the text | The task only finishes when a command's output starts with `COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` |
| W4: 60 s limit throws away all output | 180 s default. The timeout is enforced *inside* the container, so partial output survives, and the prompt says to background long jobs |
| W6: a crash skips grading | `run()` catches agent crashes (for example a context overflow) and returns normally, so the verifier still runs |
| W3/W9: no early output; solving in its head | Prompt lines: "use the machine", "write the output file as soon as you have a plausible answer" |
| W2: full history re-sent every turn | **Not addressed yet.** mini-swe-agent also keeps the full linear history. This is the next lever (fix 2 in the research) |

## Setup (done once; repeat on a new machine)

```bash
cd badger_mini_agent
uv venv .venv --python 3.12 --prompt badger_mini_agent
source .venv/bin/activate                 # prompt shows (badger_mini_agent)
uv pip install -e . -e ./badger           # editable mini-swe-agent fork + this layer (pulls harbor)
cp badger/.env.example badger/.env
cp badger/.env.op.example badger/.env.op  # then set your 1Password item name
```

You also need Docker Desktop running, the `op` CLI connected to the 1Password app, and the campus
VPN (GlobalProtect) for the UW endpoint.

## Run (from the repo root, venv active, Docker up, VPN on)

```bash
op run --env-file=badger/.env.op -- ./badger/scripts/check_endpoint.sh                  # no Docker needed
harbor run -d terminal-bench-sample@2.0 -a oracle                                        # Docker/Harbor check, no model
op run --env-file=badger/.env.op -- ./badger/scripts/run_sample.sh fix-git               # one task
op run --env-file=badger/.env.op -- ./badger/scripts/run_sample.sh                       # 10 sample tasks
op run --env-file=badger/.env.op -- ./badger/scripts/run_subset.sh eval/experiment_subset.txt
op run --env-file=badger/.env.op -- ./badger/scripts/run_full.sh --job-name full-mini-v1 # all 89
./badger/scripts/score.sh badger/jobs/<job-name>                                         # tb_score, tokens, leaderboard
```

To call Harbor directly (for example for the organizers' re-run):

```bash
cd badger && op run --env-file=.env.op -- harbor run -d terminal-bench@2.0 \
  --agent badger_mini.harbor_agent:BadgerMiniAgent -n 3
```

Concurrency is set with `N_CONCURRENT=<n>` before `op run`. Extra Harbor flags pass through the scripts.
Results go to `badger/jobs/<job>/<task>__<id>/`: `result.json` (reward, tokens) and
`agent/mini-swe-agent.trajectory.json` (the full mini-swe-agent transcript). To browse them, run
`mini-extra inspector <trajectory.json>` or `harbor view badger/jobs`.

## Gotchas

- **Credentials:** `.env.op` holds only `op://` references. `.env` is loaded with `override=False`, so
  values from `op run` always win. Even so, keep `LLM_BASE_URL` and `LLM_API_KEY` out of `.env`.
- **Model id** must match `GET $LLM_BASE_URL/models` exactly. `check_endpoint.sh` prints the list.
  Submissions must use an approved checkpoint; `qwen3.8-27b` is not one.
- **Tool calling** has to be enabled on the server (vLLM `--enable-auto-tool-choice`). If
  `check_endpoint.sh` shows no tool call, switch to text-based actions: copy the config, set
  `model_class: litellm_textbased`, and use upstream `mini_textbased.yaml`'s format templates.
- **The research docs** refer to the old baseline code (`starter/agent/…`) in `../badger_agent`.
  Their line links don't resolve here.
- **Competition rules:** no task-specific branching, approved open-weight models only, and endpoint
  changes go through env files, never code.
