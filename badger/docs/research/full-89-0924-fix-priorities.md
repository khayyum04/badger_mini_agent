# Prioritized fixes (from the full 89-task run)

This is what to build next and in what order. The evidence for every fix is in
**[full-89-0924-failure-analysis.md](full-89-0924-failure-analysis.md)**, referred to below as
*the analysis*.

**Where we start:** 28/89 passed (TB 0.315) on 93.2M tokens, a leaderboard score of **−0.62**. Most
of that is the token penalty ([Where the tokens go](full-89-0924-failure-analysis.md#where-the-tokens-go)).

**Ground rules:**

- **Task-agnostic.** No fix reads task names or keys on one task's content.
- **Estimates are rough.** They come from replaying this run: one trial per task, on the
  non-approved `qwen3.8-27b`.
- **Effort:** S is under a day, M is 1–3 days.

## Contents

- [Two tracks: symptoms and causes](#two-tracks-symptoms-and-causes)
- [Plan of work](#plan-of-work)
- [Ranking at a glance](#ranking-at-a-glance)
- [Harness fixes (symptom track)](#harness-fixes-symptom-track)
  - [Fix 0: Instrumentation and the approved model](#fix-0-instrumentation-and-the-approved-model)
  - [Fix 1: Handle reasoning overruns](#fix-1-handle-reasoning-overruns)
  - [Fix 2: Trim the context by fixed rules](#fix-2-trim-the-context-by-fixed-rules)
  - [Fix 3: Harden the parser](#fix-3-harden-the-parser)
  - [Fix 4: Crash safety and per-task budgets](#fix-4-crash-safety-and-per-task-budgets)
  - [Fix 5: Write a best-effort output early](#fix-5-write-a-best-effort-output-early)
  - [Fix 6: Long-running commands](#fix-6-long-running-commands)
  - [Fix 7: Stall breaker](#fix-7-stall-breaker)
  - [Fix 8: Hygiene prompt additions](#fix-8-hygiene-prompt-additions)
  - [Fix 9: Completion checklist](#fix-9-completion-checklist)
  - [Fix 10: Raise the turn cap](#fix-10-raise-the-turn-cap)
- [Cause track: fewer overruns at the source](#cause-track-fewer-overruns-at-the-source)
  - [C0: Classify what triggers fresh overruns](#c0-classify-what-triggers-fresh-overruns)
  - [C1: Probe the gateway](#c1-probe-the-gateway)
  - [C2: Warmer sampling](#c2-warmer-sampling)
  - [C3: Prompt lines that change how it thinks](#c3-prompt-lines-that-change-how-it-thinks)
  - [C4: Bigger thinking budget, on retry only](#c4-bigger-thinking-budget-on-retry-only)
  - [C5: Steer large outputs toward scripts](#c5-steer-large-outputs-toward-scripts)
  - [C6: Context trimming](#c6-context-trimming)
- [Separate: fix the measurement](#separate-fix-the-measurement)
- [Not recommended](#not-recommended)
- [How to measure](#how-to-measure)
  - [Experiment subset](#experiment-subset)

## Two tracks: symptoms and causes

An **overrun** is a turn where the model spends its whole 8,192-token budget thinking and never
answers ([W1](full-89-0924-failure-analysis.md#w1-reasoning-overruns-are-run-as-commands)). The 508
overruns in this run are two different problems:

| Kind | Count | What it is | Fixed by |
|---|---:|---|---|
| **Fresh** (previous turn was normal) | 150 | The model *started* thinking too long. This is the cause | the [cause track](#cause-track-fewer-overruns-at-the-source) |
| **Repeat** (previous turn was an overrun) | 358 | Driven by the harness keeping cut-off thinking in the history. This is the symptom | [Fix 1](#fix-1-handle-reasoning-overruns) |

Details are in [Fresh overruns vs repeats](full-89-0924-failure-analysis.md#fresh-overruns-vs-repeats).

**How the tracks relate:**

- **Symptom fixes remove most of the volume and tokens.** But they can't stop a task from getting
  stuck on its first long think.
- **Cause fixes reduce fresh overruns.** But they can't be measured cleanly while repeats still
  pollute the context.
- So the plan does a **small symptom base first**, then runs cause experiments, with the rest in
  parallel.

## Plan of work

| When | What | Code? |
|---|---|---|
| **Now** | [C0](#c0-classify-what-triggers-fresh-overruns) trigger analysis on existing data. [C1](#c1-probe-the-gateway) gateway probe | none |
| **Step 1: minimal clean base** | [Fix 0](#fix-0-instrumentation-and-the-approved-model), including the switch to the approved model; [Fix 1](#fix-1-handle-reasoning-overruns) with [Fix 7](#fix-7-stall-breaker) folded in; [Fix 3](#fix-3-harden-the-parser). Then **one full 89-task run** as the new baseline | small |
| **Step 2: cause experiments** | [C2](#c2-warmer-sampling), [C3](#c3-prompt-lines-that-change-how-it-thinks), [C4](#c4-bigger-thinking-budget-on-retry-only), [C5](#c5-steer-large-outputs-toward-scripts), each as a **separate A/B arm** on the [experiment subset](#experiment-subset) | mostly config and prompt |
| **In parallel with step 2** | [Fix 2](#fix-2-trim-the-context-by-fixed-rules) (also [C6](#c6-context-trimming)), [Fix 5](#fix-5-write-a-best-effort-output-early), [Fix 6](#fix-6-long-running-commands) | yes |
| **Last** | [Fix 4](#fix-4-crash-safety-and-per-task-budgets) budget caps, [Fix 10](#fix-10-raise-the-turn-cap), [Fix 8](#fix-8-hygiene-prompt-additions) and [Fix 9](#fix-9-completion-checklist) as A/B tests, then a full run for the reported score | small |

**Rules for the plan:**

- **"Clean" means Step 1 only.** Don't wait for every harness fix before starting on causes.
- **Change one thing per measurement,** not one phase at a time.
- **No hard token caps during Step 2.** Caps end trials early, which hides overruns and makes the
  fresh-overrun rate look better than it is.
- **Keep the fresh-overrun rate on the scoreboard after Step 1.** Once the token penalty drops, the
  score will jump, and it will be tempting to stop. The fresh overruns will still be there.

## Ranking at a glance

**Harness fixes**, ranked by expected score gain per unit of effort:

| # | Fix | Weakness | Tasks | Tokens (68 tasks) | Score Δ | Effort |
|---:|---|---|---:|---:|---:|---|
| 0 | [Instrumentation + approved model](#fix-0-instrumentation-and-the-approved-model) | all | — | — | needed to trust the rest | S |
| 1 | [**Handle reasoning overruns**](#fix-1-handle-reasoning-overruns) | [W1](full-89-0924-failure-analysis.md#w1-reasoning-overruns-are-run-as-commands), W5–W7 | +3 to +6 | **−40M to −55M** | **+0.43 to +0.62** | S–M |
| 2 | [**Trim the context by fixed rules**](#fix-2-trim-the-context-by-fixed-rules) | [W2](full-89-0924-failure-analysis.md#w2-full-history-is-re-sent-every-turn-with-no-budget), W1 | +0 to +2 | **−15M to −20M** more | +0.15 to +0.22 | S–M |
| 3 | [Harden the parser](#fix-3-harden-the-parser) | [W5](full-89-0924-failure-analysis.md#w5-the-action-parser-is-fragile) | +1 (overlaps fix 1) | small | +0.01 to +0.02 | S |
| 4 | [Crash safety + per-task budgets](#fix-4-crash-safety-and-per-task-budgets) | [W6](full-89-0924-failure-analysis.md#w6-a-crash-means-the-task-is-never-graded), W2 | +0 to +1 | caps the worst runs | +0.01 to +0.05 | S |
| 5 | [Write a best-effort output early](#fix-5-write-a-best-effort-output-early) | [W3](full-89-0924-failure-analysis.md#w3-no-time-awareness-and-no-early-output-file) | +2 to +4 | ≈ 0 | +0.02 to +0.045 | S |
| 6 | [Long-running commands](#fix-6-long-running-commands) | [W4](full-89-0924-failure-analysis.md#w4-long-commands-hit-a-60-s-limit-and-lose-their-output) | +1 to +2 | −2M to −3M | +0.03 to +0.05 | S–M |
| 7 | [Stall breaker](#fix-7-stall-breaker) | [W7](full-89-0924-failure-analysis.md#w7-stalls-and-exact-repeats) | +0 to +1 | −2M | +0.02 to +0.03 | S (with fix 1) |
| 8 | [Hygiene prompt additions](#fix-8-hygiene-prompt-additions) | [W8](full-89-0924-failure-analysis.md#w8-output-quality-and-unstated-requirements) | +0 to +2 | +0.2M | +0 to +0.02 | S |
| 9 | [Completion checklist](#fix-9-completion-checklist) | W8 | +0 to +1 | ≈ 0 | +0 to +0.01 | S |
| 10 | [Raise the turn cap](#fix-10-raise-the-turn-cap), **after fix 2** | W4 | +0 to +2 | adds cost | +0 to +0.02 | trivial |

**Cause-track items** have no score estimate. Replaying transcripts can price removing junk, but it
can't price a change in the model's behavior. That's why the cause fixes would look tiny in the
table above, not because they don't matter. They're judged by the
[fresh-overrun rate](#how-to-measure).

| # | Cause fix | Targets | Cost | Effort |
|---|---|---|---|---|
| [C0](#c0-classify-what-triggers-fresh-overruns) | Classify what triggers fresh overruns | tells us which fixes below are worth it | analysis only | S |
| [C1](#c1-probe-the-gateway) | Probe the gateway for budget, thinking and sampling options | decides whether C2/C4 are possible | one test request | S |
| [C2](#c2-warmer-sampling) | Warmer sampling (model-card settings) | the ~40% of fresh overruns that are loops | `.env` + 1 argument | S |
| [C3](#c3-prompt-lines-that-change-how-it-thinks) | "Use the machine" and "you have more turns" prompt lines | solving problems in its head ([W9](full-89-0924-failure-analysis.md#w9-working-it-out-in-its-head)) | ~100 tokens per turn | S |
| [C4](#c4-bigger-thinking-budget-on-retry-only) | Bigger thinking budget, on retry only | the ~60% of fresh overruns that are genuine derivations | time, on overrun turns only | S |
| [C5](#c5-steer-large-outputs-toward-scripts) | Steer large or binary outputs toward scripts | hand-decoding dumps and disassembly | small | S–M |
| [C6](#c6-context-trimming) | Context trimming (same as Fix 2) | overrun rate rising with context size | — | — |

**Rough projection with fixes 0–7 and a clean re-run** (before any cause-track gains):

| | Estimate |
|---|---|
| TB score | ≈ 0.49–0.53 |
| Tokens (89 tasks) | 15–30M |
| Leaderboard | **≈ +0.20 to +0.38** (now −0.62) |

---

## Harness fixes (symptom track)

### Fix 0: Instrumentation and the approved model

**Build.** Record these in `context.metadata` every turn:

- `finish_reason`
- the length of `reasoning_content`, and whether the answer was empty
- whether `message.tool_calls` was set
- per-turn `usage`
- a timestamp
- the resolved model id

Change `llm.chat()`'s return value ([`llm.py` L131–155](../../agent/llm.py#L131-L155)) and the
metadata update ([`agent.py` L153–157](../../agent/agent.py#L153-L157)). Keep
`scripts/build_dashboard.py` working.

**Switch to `Qwen3.6-27B-FP8`** in the same step (`starter/docs/uw_madison_endpoint.md`). Every
later measurement should be on the approved model.

**Why.**

- Overruns are currently *inferred*
  ([How overruns were identified](full-89-0924-failure-analysis.md#how-overruns-were-identified)).
  Logging turns that into fact.
- It is the only way to count *fresh* overruns and measure the cause track.
- It settles the tool-call question in [W7](full-89-0924-failure-analysis.md#w7-stalls-and-exact-repeats).
- It gives real per-turn latency.

**Risks.** Slightly larger metadata.

### Fix 1: Handle reasoning overruns

This is **symptom handling**: it stops a cut-off turn from poisoning the ones after it. The retry
message is the one place it also nudges behavior.

**Build** (in `llm.py` and `agent.py`):

1. **Detect overruns.** When `finish_reason == "length"` and the answer is empty, the turn is an
   overrun.
2. **Never run or keep the cut-off thinking.** Remove the fallback at
   [`llm.py` L148](../../agent/llm.py#L148), so it is never parsed and never appended to the
   history ([`agent.py` L159](../../agent/agent.py#L159)). Log it in metadata instead.
3. **Retry with a targeted message**, for example: *"You ran out of thinking budget before giving a
   command. Don't work it out in your head; take one small step and run a short script that tests
   one idea. Reply with one short bash block."*
4. **Change decoding on the retry**, so it doesn't reproduce the same output. Use a higher
   temperature ([C2](#c2-warmer-sampling)) and, if tested, a bigger budget
   ([C4](#c4-bigger-thinking-budget-on-retry-only)).
5. **Cap the retries.** After about 2 overruns in a row, switch to short replies plus
   [Fix 5](#fix-5-write-a-best-effort-output-early)'s "make sure the output file exists" reminder.
6. **Optional:** keep a short, fence-free excerpt (about 800 characters) of the lost thinking as a
   note. A/B test it.

**Why** ([W1](full-89-0924-failure-analysis.md#w1-reasoning-overruns-are-run-as-commands)):

- Overruns are the main cause of 24 of 39 agent failures.
- They are 60% of input tokens and 72% of output tokens.
- 98% of the commands run out of them fail.
- Once one happens, the next turn overruns too 73% of the time.
- They caused all 6 context-overflow crashes and the false "done" in
  `llm-inference-batching-scheduler`.

**Estimate.** −40M to −55M tokens and +3 to +6 tasks.

**Risks.**

- The retry can overrun too. That's why the retries are capped.
- The model may redo analysis it lost.
- A higher temperature adds variance on tasks we pass today; watch the regression group in the
  [experiment subset](#experiment-subset).
- If `finish_reason` turns out to be unreliable, detect overruns instead as "empty answer and
  reasoning at least 90% of the max token count".

### Fix 2: Trim the context by fixed rules

**Build** (in `agent.py`, with a small helper). Keep the full transcript in metadata, but build each
request from a trimmed view:

- **Always include** the system prompt and the instruction.
- **The last ~5 reply/output pairs** go in full.
- **Older outputs** become ~200-character stubs that keep the exit code and the error line.
- **Older replies** are trimmed to their command plus one line.
- **Nudges and failed replies** are left out.
- **Cap the total size of stubs**, dropping the oldest first.
- **Cap each output at ~3K characters** (head, tail and error lines), and write the full output to
  a file in the container so it can be re-read.

That's about 50–100 lines, with no extra model calls.

**Why.** It is both a token fix
([W2](full-89-0924-failure-analysis.md#w2-full-history-is-re-sent-every-turn-with-no-budget)) and a
cause fix ([C6](#c6-context-trimming)):

- **About −20M input tokens.** Replaying this run, input goes from 35.1M (after fix 1) to about
  15.6M ([replays](full-89-0924-failure-analysis.md#where-the-tokens-go)).
- **Probably fewer overruns.** The overrun rate climbs from 5% to 43% as context grows.

**Why not model-written summaries yet.**

- **Little left to save.** A summary could only replace the older stubs, about 6.6M of the 15.6M
  left. That's perhaps 4–5M tokens net (+0.04).
- **It breaks prefix caching.**
- **Forgetting wasn't a cause.** None of the 39 failures came from forgotten context.

If forgetting shows up after this, first try a notes file the model keeps for itself (a prompt
change).

**Risks.**

- **The model may re-read files** it no longer has in context. Include file paths in the stubs.
- **Rewriting old messages breaks the prefix cache.** Shorten each message only once, when it
  leaves the window.
- **Do it after fix 1.** Otherwise the recent window re-sends cut-off thinking.

### Fix 3: Harden the parser

**Build** (in [`tools.py`](../../agent/tools.py#L41-L96), mirrored in `scripts/build_dashboard.py`):

- **Pair fences in order**, so a closing fence can't be taken for an opening one.
- **Accept only ```` ```bash ````, `sh` or `shell` blocks.**
- **Accept `TASK_COMPLETE` only on the last line**, only when there's no command block, and never
  from an overrun.
- **Map `message.tool_calls`** to a command.

**Why** ([W5](full-89-0924-failure-analysis.md#w5-the-action-parser-is-fragile)):

- 165 runs of prose as bash.
- 249 of 251 untagged blocks failed.
- The false "done" came from matching `TASK_COMPLETE` anywhere in the text.
- Prose run as bash could redirect onto a file or run `rm`.

**Risks.**

- **Some models legitimately send untagged blocks**; re-check when switching models.
- **Allow trailing punctuation after `TASK_COMPLETE`.**

### Fix 4: Crash safety and per-task budgets

**Build** (in [`agent.py`](../../agent/agent.py#L141-L184)):

- **Catch errors from `llm.chat()`.**
  - On a context-overflow error: trim harder and retry once, otherwise return normally so the
    verifier still grades the work.
  - On a transient error: retry with backoff.
- **Track elapsed time and cumulative tokens.**
- **Soft token budget** (for example 600K): trigger [Fix 5](#fix-5-write-a-best-effort-output-early)'s
  reminder.
- **Hard budget** (for example 1.5M): end the run cleanly. **Leave it off during the cause
  experiments.**

**Why** ([W6](full-89-0924-failure-analysis.md#w6-a-crash-means-the-task-is-never-graded)):

- The 6 overflow trials were never graded. `video-processing` had a runnable script.
- At today's cost a cap is worth a lot
  ([cap table](full-89-0924-failure-analysis.md#where-the-tokens-go)). After fixes 1–2 it becomes a
  safety net.

**Risks.**

- **A hard cap can kill a slow run that would have passed** (today, 2 passes used more than 1.5M).
- **Retry only known-transient errors.**

### Fix 5: Write a best-effort output early

**Build:**

1. **Prompt:** *"As soon as you have any plausible answer, write the required output file. Keep it
   valid and overwrite it when you improve it."*
2. **Show elapsed time** (`[elapsed: 12m]`) in outputs every few turns.
3. **One reminder** at about 10 minutes and at the soft token budget: *"Make sure the output file
   exists now, and keep improving."*

**Why** ([W3](full-89-0924-failure-analysis.md#w3-no-time-awareness-and-no-early-output-file)):

- 15 graded failures never wrote their output file.
- At least 3 had the answer in hand (`chess-best-move`, `train-fasttext`,
  `torch-pipeline-parallelism`).
- Grading is pass/fail, so a wrong file costs nothing compared with a missing one.
- The time budget (750–3,600 s) is never passed to the agent, so this is a checkpoint, not a
  deadline.

**Risks.**

- **Long tasks may stop early.** The reminder must say "keep improving".
- **Draft files can break "only these files" checks** (`polyglot-c-py`). Pair this with
  [Fix 8](#fix-8-hygiene-prompt-additions).

### Fix 6: Long-running commands

**Build:**

- **Raise `AGENT_COMMAND_TIMEOUT_SEC`** to 180–300 s (an `.env` change).
- **Keep the output when a command times out.** In
  [`run_shell`](../../agent/tools.py#L108-L143), send output to a file so a timeout still returns
  the partial output, and say whether the process is still running.
- **Prompt:** start builds and training runs in the background with a log file, then wait with one
  blocking command (for example `timeout 240 tail --pid=<PID> -f /dev/null; tail -20 log`)
  instead of `sleep` polls.
- **Prompt:** kill processes by PID, never with `pkill -f`.

**Why** ([W4](full-89-0924-failure-analysis.md#w4-long-commands-hit-a-60-s-limit-and-lose-their-output)):

- 31 commands hit the 60 s limit and lost their output.
- 151 `sleep`-poll turns cost about 2.4M tokens.
- Both turn-cap failures ran out of turns this way.
- 5 `pkill -f` calls killed the agent's own shell.

**Risks.** A command that really hangs now wastes up to 5 minutes instead of 1. Prompt the model to
start servers in the background.

### Fix 7: Stall breaker

**Build** (inside fix 1's retry path):

- **When a reply has no command, don't keep it.** Retry with an escalating nudge that includes an
  example (```` ```bash\nls -la /app\n``` ````), and change decoding on the retry.
- **Optional environment probe.** Before the first turn, run one fixed, harmless command
  (`pwd; ls -la; head -3 /etc/os-release`) and include its output.

**Why** ([W7](full-89-0924-failure-analysis.md#w7-stalls-and-exact-repeats)):

- 145 turns produced no command.
- 32 of 68 trials stalled on their very first turn.
- `headless-terminal` repeated the same line 24 times in a row.
- There were 235 exact repeat replies.

**Risks.** The probe adds a few hundred tokens per task. Keep it short.

### Fix 8: Hygiene prompt additions

**Build.** Add to [`prompts.py`](../../agent/prompts.py#L31-L70):

- *"Build and test in /tmp, not in the output directory."*
- *"For performance targets, benchmark at least two alternatives."*
- *"Match the calling conventions shown in the instruction's examples."*

The thinking-related lines are in [C3](#c3-prompt-lines-that-change-how-it-thinks) instead.

**Why.** Each line targets a recurring miss in
[W8](full-89-0924-failure-analysis.md#w8-output-quality-and-unstated-requirements):

| Miss | Task |
|---|---|
| Leftover build output next to the answer | `polyglot-c-py` |
| Unexpected function signature | `adaptive-rejection-sampler` |
| Stopped at the first correct answer | `query-optimize` |

**Risks.** A small token cost on every turn, and the effect is uncertain. A/B test it.

### Fix 9: Completion checklist

**Build.** Before `TASK_COMPLETE`, a short prompt checklist:

- re-read the instruction's literal requirements
- list the output directory for stray files
- if the task says "as fast as possible", confirm alternatives were benchmarked

No extra model call.

**Why.** Only 4 genuine "said done but wrong" trials exist
([Outcomes](full-89-0924-failure-analysis.md#outcomes)), so this is a small lever.

**Risks.** A few more turns per task.

### Fix 10: Raise the turn cap

**Build.** Raise `AGENT_MAX_TURNS` to about 150, **only after fix 2** makes turns cheap.

**Why.** The cap stopped 2 of 68 trials, and both still had time left
([W4](full-89-0924-failure-analysis.md#w4-long-commands-hit-a-60-s-limit-and-lose-their-output)).

**Risks.** Before fix 2, extra turns are just extra tokens.

---

## Cause track: fewer overruns at the source

**Goal:** fewer *fresh* overruns (150 in this run, 6.2 per 100 turns), without hurting pass rate.

**What we know about the cause**
([Fresh overruns vs repeats](full-89-0924-failure-analysis.md#fresh-overruns-vs-repeats)):

- **Overruns are a tail, not the model's habit.** Normal turns average about 460 output tokens.
- **About 60% of fresh overruns look like genuine long derivations.** The model hand-traces a
  cipher, decodes hex or designs an algorithm in its head.
- **About 40% are already looping,** with more than 15% of their lines repeated.
- **They almost always start with the model working it out in its head**
  ([W9](full-89-0924-failure-analysis.md#w9-working-it-out-in-its-head)).
- **They get more likely as the context grows.**

**What we don't know yet:** what *triggers* them, which C0 answers.

### C0: Classify what triggers fresh overruns

**Do** (analysis only, on existing data). For each of the 150 fresh overruns, record what came
right before it:

- a large output, such as a hex dump, a disassembly or a big source file
- an error
- a design decision
- the very first turn

Also record the context size at that point, and whether the overrun looks like a derivation or a
loop.

**Why.** It tells us which of C2–C5 to build first. Right now "works it out in its head" is a good
hypothesis, not a finding.

**Output.** A short table added to the analysis, next to
[Fresh overruns vs repeats](full-89-0924-failure-analysis.md#fresh-overruns-vs-repeats).

### C1: Probe the gateway

**Do.** Send one test request with each of these, and check that it takes effect:

- a thinking budget, or a reasoning-effort setting
- turning thinking off for a call (for example `chat_template_kwargs={"enable_thinking": false}`)
- `top_p`

**Why.** Whether C2 and C4 are even possible depends on the litellm gateway passing these through.

### C2: Warmer sampling

**Build.**

- Set `LLM_TEMPERATURE` to the model card's thinking-mode setting (as I recall, about 0.6).
- Add `top_p` (about 0.95) as one argument in [`llm.py`](../../agent/llm.py#L131-L136).
- Check both values for the exact checkpoint you use.

**Why.**

- The run likely used 0.2 (the code default), which is near-greedy. Near-greedy decoding is known
  to cause repetition in thinking models.
- 39% of fresh overruns already loop.
- There were 235 exact repeat replies.

**Risks.** More variance on easy tasks; watch the regression group.

### C3: Prompt lines that change how it thinks

**Build.** Add to the STRATEGY section of [`prompts.py`](../../agent/prompts.py#L36-L45):

- *"Use the machine: when you need to compute, decode, simulate or search, write a short script
  and run it instead of working it out in your head."*
- *"You have many turns. It's fine to take a partial step now and keep reasoning after you see the
  output."*
- *"One small step per turn."*

**Why.**

- Almost every fresh overrun opens with "Let me analyze … carefully" and then works by hand
  ([Appendix B](full-89-0924-failure-analysis.md#appendix-b-turn-strings)).
- Nothing currently tells the model that it doesn't have to solve everything in one turn.

**Risks.** Prompts are the least reliable lever against a model's habits. That's why this is
measured on its own.

### C4: Bigger thinking budget, on retry only

**Build.** Keep the normal cap at 8K. When a turn overruns, retry once with 16–24K (inside
[Fix 1](#fix-1-handle-reasoning-overruns)'s retry). If it overruns again, fall back to short
replies.

**Why.**

- `max_tokens` is a **ceiling, not a cost.** Normal turns (~460 tokens) are unaffected.
- The ~60% of fresh overruns that are genuine derivations get one chance to finish.
- Loops still get cut off early.

**Measure.** On overrun-heavy tasks, how many retries finish at 16K and at 24K, and how often that
answer runs successfully. If most finish, raise the base cap. If they keep hitting the new ceiling,
the problem is looping, and C2 and C3 matter more.

**Risks.** Each large retry costs about 2–3 minutes of a 900 s budget. Only do this after fix 1, so
the longer thinking never enters the history.

### C5: Steer large outputs toward scripts

**Build.** When an output is large, or looks like hex, binary or disassembly, add a one-line hint
to the observation, for example: *"Large output (N lines) saved to /tmp/out_12.txt. Consider a
script to search or parse it instead of reading it by hand."* This can reuse
[Fix 2](#fix-2-trim-the-context-by-fixed-rules)'s output-to-file mechanism.

**Why.** Walls of hex and disassembly invite analysis by hand. For example, `qemu-startup`
hand-decoded ISO bytes, and `path-tracing-reverse` traced registers. C0 will show how often large
outputs come right before an overrun.

**Risks.** A few tokens per large output, and the hint could be ignored.

### C6: Context trimming

This is the same change as [Fix 2](#fix-2-trim-the-context-by-fixed-rules). It is listed here
because it is also a cause fix: fresh overruns rise from 5% of turns below 25K tokens of context to
43% at 100–125K
([W1](full-89-0924-failure-analysis.md#w1-reasoning-overruns-are-run-as-commands)). Measure the
fresh-overrun rate before and after, to see whether that relationship is causal.

---

## Separate: fix the measurement

These aren't agent changes, but the reported score depends on them:

- **Re-run the 21 infrastructure failures**
  ([A.3](full-89-0924-failure-analysis.md#a3-infrastructure-failures)) with lower concurrency and
  pre-pulled images. Also use `--environment-build-timeout-multiplier` and `--retry-include`.
  - Expect about 8 more passes.
  - At today's cost they'd also add 12–17M tokens, so **report a full run only after Step 1 and
    Fix 2**.
- **Check the QEMU verifiers.** Both QEMU tasks' test scripts failed on a Debian mirror 404
  ([Outcomes](full-89-0924-failure-analysis.md#outcomes)). Re-run them. If the 404 persists, raise
  it with the organizers, because no team could score those tasks.

## Not recommended

| Idea | Why not |
|---|---|
| Raise `LLM_MAX_TOKENS` for every turn | Loops then burn 2–4× longer before failing. Use the retry-only version ([C4](#c4-bigger-thinking-budget-on-retry-only)), after fix 1 |
| Lower `LLM_MAX_TOKENS` | More overruns |
| Change the agent time limit for a reported run | Organizers re-run the top 5 on default settings (`RULES.md`) |
| A low token cap as the main strategy | It raises the score by giving up on work. Use it only as a backstop, after the cause experiments |
| Build a completion guard early | Only 4 genuine "said done but wrong" trials; it addresses 1–2 tasks ([Fix 9](#fix-9-completion-checklist)) |
| Model-written summaries now | Worth about +0.04 after fix 2, and it breaks prefix caching ([Fix 2](#fix-2-trim-the-context-by-fixed-rules)) |
| Wait for every harness fix before starting on causes | "Clean" only needs Step 1 ([Plan of work](#plan-of-work)) |

## How to measure

| Metric | Why | This run |
|---|---|---|
| **Fresh overruns per 100 turns** | The cause-track headline | 6.2 (150 fresh) |
| Chance of an overrun right after an overrun | Fix 1 should take this near 0 | 73% |
| Pass rate on the regression group | Must not drop | 10 / 10 |
| Pass rate on the target group | Should rise | 0 / 20 |
| Total tokens, per passing and per failing trial | The score's biggest term | 93.2M; median 173K per pass, 1.38M per failure |
| Retries that end with a working command | Tests Fix 1 and C4 | — |

- **One on/off setting per fix** (an environment variable), so each fix can be A/B tested and turned
  off without reverting code.
- **Experiments run on the subset. Full 89-task runs are for milestones.**
- Runs are noisy single samples. **Trust changes that show up across many tasks,** not single-task
  flips.

### Experiment subset

Each full 89-task run takes about 7 hours on a shared endpoint, so A/B arms run on this fixed set of
30 tasks. The data for each task is in
[Appendix A](full-89-0924-failure-analysis.md#appendix-a-per-trial-results).

**Target group (20).** The failing tasks with the most overruns; `raman-fitting` replaces
`gpt2-codegolf` because it is recoverable. `qemu-startup` is excluded because its verifier is
broken.

`circuit-fibsqrt`, `regex-chess`, `winning-avg-corewars`, `fix-ocaml-gc`,
`feal-differential-cryptanalysis`, `schemelike-metacircular-eval`, `video-processing`,
`custom-memory-heap-crash`, `make-mips-interpreter`, `mailman`, `break-filter-js-from-html`,
`path-tracing-reverse`, `path-tracing`, `polyglot-rust-c`, `protein-assembly`, `write-compressor`,
`tune-mjcf`, `polyglot-c-py`, `prove-plus-comm`, `raman-fitting`.

**Regression group (10).** Passing tasks that still had overruns. These are the most likely to
regress when overrun handling or sampling changes.

`portfolio-optimization`, `feal-linear-cryptanalysis`, `reshard-c4-data`, `distribution-search`,
`extract-elf`, `financial-document-processor`, `regex-log`, `torch-tensor-parallelism`,
`bn-fit-modify`, `cancel-async-tasks`.
