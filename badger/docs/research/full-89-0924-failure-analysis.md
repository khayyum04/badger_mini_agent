# Full 89-task run: failure analysis (`jobs/full-89-0924`)

This covers the first full Terminal-Bench 2.0 run of the baseline agent (89 tasks, one trial
each). It explains why the agent fails, in general terms rather than task by task, and what those
failures cost.

- **Fixes:** the ranked list of what to build lives in a separate doc,
  **[full-89-0924-fix-priorities.md](full-89-0924-fix-priorities.md)**.
- **Sources:** every number was recomputed from `jobs/full-89-0924/*/` (`result.json`,
  `verifier/ctrf.json`, `verifier/test-stdout.txt`, `trial.log`, `exception.txt`). `jobs/` is
  gitignored, so trial IDs below point to local directories only.
- **Code references** are to `starter/agent/` at `21d5c09`. No code was changed.

## Contents

- [Summary](#summary)
- [Run facts and caveats](#run-facts-and-caveats)
  - [How overruns were identified](#how-overruns-were-identified)
- [Outcomes](#outcomes)
- [Where the tokens go](#where-the-tokens-go)
- [Weaknesses, ranked](#weaknesses-ranked)
  - [W1: Reasoning overruns are run as commands](#w1-reasoning-overruns-are-run-as-commands)
    - [Fresh overruns vs repeats](#fresh-overruns-vs-repeats)
  - [W2: Full history is re-sent every turn, with no budget](#w2-full-history-is-re-sent-every-turn-with-no-budget)
  - [W3: No time awareness and no early output file](#w3-no-time-awareness-and-no-early-output-file)
  - [W4: Long commands hit a 60 s limit and lose their output](#w4-long-commands-hit-a-60-s-limit-and-lose-their-output)
  - [W5: The action parser is fragile](#w5-the-action-parser-is-fragile)
  - [W6: A crash means the task is never graded](#w6-a-crash-means-the-task-is-never-graded)
  - [W7: Stalls and exact repeats](#w7-stalls-and-exact-repeats)
  - [W8: Output quality and unstated requirements](#w8-output-quality-and-unstated-requirements)
  - [W9: Working it out in its head](#w9-working-it-out-in-its-head)
- [Fixes at a glance](#fixes-at-a-glance)
- [What is beyond the model](#what-is-beyond-the-model)
- [Limits of this analysis](#limits-of-this-analysis)
- [Appendix A: per-trial results](#appendix-a-per-trial-results)
- [Appendix B: turn strings](#appendix-b-turn-strings)
- [Appendix C: method](#appendix-c-method)

---

## Summary

| Metric | Value |
|---|---|
| Tasks passed | 28 / 89 (TB score **0.315**) |
| Pass rate on tasks that actually ran | 28 / 68 = **41%** (21 never started) |
| Total tokens | **93.2M** (87.5M input, 5.7M output) |
| Leaderboard score (`TB − 0.01 × tokens/1M`) | 0.315 − 0.932 = **−0.62** |

1. **Tokens are the biggest term in the score, not a tie-breaker.** The README expects 1–3M tokens
   per run; this run used 93M. Failed trials burned 89% of them and earned nothing. The root
   `CLAUDE.md` calls token cost "real but small", which doesn't hold for this agent.
2. **The #1 weakness is how "reasoning overruns" are handled** ([W1](#w1-reasoning-overruns-are-run-as-commands)).
   An overrun is a turn where the model thinks for its whole 8,192-token budget and never
   answers. The harness then runs the cut-off thinking as if it were a command, and keeps it in the
   history. Overruns are:
   - 21% of turns
   - 72% of output tokens
   - 60% of input tokens
   - the main cause of **24 of 39** agent-caused failures

   Only 150 of the 508 are **fresh**, meaning the model started thinking too long (the cause). The
   other 358 are **repeats** driven by the harness keeping cut-off thinking in the history (the
   symptom). See [Fresh overruns vs repeats](#fresh-overruns-vs-repeats).
3. **Nothing stops a failing trial from getting expensive**
   ([W2](#w2-full-history-is-re-sent-every-turn-with-no-budget)). A failing trial's median cost is
   1.38M tokens, against 173K for a passing one.
4. **The agent often never writes its answer file**
   ([W3](#w3-no-time-awareness-and-no-early-output-file)). In 15 failures the output file was never
   created, and some of those had the answer in hand.
5. **Declaring "done" too early is a small problem here.** Only 4 genuine misses.
6. **Neither QEMU task could be scored.** Their test scripts failed on a Debian mirror 404 before
   running any tests.

**What to do:** a small symptom fix first, then cause experiments, with the rest in parallel. See
the [plan of work](full-89-0924-fix-priorities.md#plan-of-work).

> **Caveat:** this run used **`qwen3.8-27b`**, which is **not** the approved `Qwen3.6-27B-FP8`
> (see `starter/.env.op.example`). The harness bugs don't depend on the model. The rates will change
> on the approved one.

## Run facts and caveats

| Fact | Value |
|---|---|
| Job | `full-89-0924`: 89 tasks, 1 trial each, 3 in parallel, about 7 h on 2026-09-24 |
| Model | `qwen3.8-27b` behind a litellm gateway, with a 250K-token context (from the context-overflow error text) |
| `LLM_MAX_TOKENS` | 8192 (from the same error). `LLM_TEMPERATURE` wasn't recorded; the code default is 0.2 |
| Agent settings | baseline: 100 turns max, 60 s per command, 6,000-character output cap |
| Time budget per task | set by the task and never passed to the agent: 750, 900, 1,200, 1,800 or 3,600 s seen |

- **Single sample per task.** The overall statistics (2,420 turns, 68 transcripts) are solid.
  Anything said about one specific task is an anecdote.
- **Token splits and "replays" are estimates.** Replays re-price the recorded histories under a
  different context policy. They don't model how the agent would have behaved differently.

### How overruns were identified

`finish_reason` isn't logged, so overruns were inferred. The pattern is clear:

- **Normal replies start with a newline.** 1,705 of 1,778 short replies (under 2K characters) do.
  That's what an answer looks like once the thinking block has been stripped off.
- **Long replies don't.** 508 of 521 replies of 10K+ characters don't start with a newline. 467 of
  those stop mid-sentence, and their lengths cluster at 20–34K characters, which is about 8,192
  tokens.

That is exactly what [`llm.py` L137–148](../../agent/llm.py#L137-L148) produces when the answer
comes back empty: it substitutes the cut-off thinking. **Overrun turn** in this doc means a reply of
10K+ characters without a leading newline.

## Outcomes

| Category | Trials | Details |
|---|---:|---|
| Never started (infrastructure) | 21 | 18 had `docker compose` crash (exit −11) at the same second; 3 took over 600 s to start ([A.3](#a3-infrastructure-failures)) |
| Verifier broken | 1 (+1) | `qemu-alpine-ssh` was solved (ssh verified 3/3 times), but the test script couldn't install `curl` (Debian 404). `qemu-startup` hit the same failure |
| Crash: context overflow | 6 | Went past 250K tokens. Harbor then **skipped the verifier**, so none were graded |
| Timeout | 27 | 24 scored 0; `regex-log` passed; 2 have no verifier result |
| Turn cap (100) | 2 | `compile-compcert`, `install-windows-3.11`. Both still had time left |
| Said done, but wrong | 6 | 4 genuine, 1 verifier outage, 1 false "done" caused by an overrun |
| **Passed** | **28** | 27 said `TASK_COMPLETE`, plus `regex-log` |

When the agent says it's done, it's usually right: 27 of 33 `TASK_COMPLETE`s passed. The losses
come overwhelmingly from trials that never get to that point.

## Where the tokens go

**By outcome**

| Outcome | Trials | Tokens | Share | Per trial |
|---|---:|---:|---:|---:|
| Timeout | 27 | 50.5M | 54% | 1.87M |
| Context overflow | 6 | 25.9M | 28% | 4.31M |
| Passed | 27 | 8.6M | 9% | 0.32M |
| Said done, but wrong | 6 | 4.3M | 5% | 0.72M |
| Turn cap | 2 | 3.9M | 4% | 1.97M |

**Input tokens by what was re-sent**

| Message type | Input tokens | Share |
|---|---:|---:|
| **Cut-off thinking (overrun turns)** | **52.4M** | **60%** |
| Command output | 22.8M | 26% |
| Normal replies | 9.9M | 11% |
| System prompt + instruction | 2.1M | 2% |

**Replays under different context policies**

| Context policy | Input tokens (actual: 87.5M) |
|---|---:|
| Drop overrun turns from the history | 35.1M |
| … and keep only the last 5 outputs in full (older ones cut to 200 characters) | 18.6M |
| … and trim replies older than 5 turns to 600 characters | 15.4M |

**What a blunt per-trial token cap would do** to today's agent:

| Cap | Tokens | Passes lost | Score |
|---:|---:|---:|---:|
| none | 93.2M | 0 | −0.62 |
| 1.5M | 56.0M | 2 | −0.26 |
| 600K | 29.9M | 4 | −0.03 |
| 250K | 14.4M | 12 | +0.04 |

Right now, giving up earlier scores better than trying harder. The real fix is to make each turn
cheap, so that a cap only ever stops trials that are failing anyway.

## Weaknesses, ranked

Each of the 39 agent-caused failures has one **primary** cause, taken from reading its transcript.
The per-trial breakdown is in [Appendix A](#appendix-a-per-trial-results). "Recoverable" means the
task would plausibly pass once that weakness is fixed.

| # | Weakness | Primary cause of | Contributes to | Token cost | Recoverable (likely / maybe) |
|---|---|---:|---:|---|---:|
| [W1](#w1-reasoning-overruns-are-run-as-commands) | Reasoning overruns run as commands | **24** | 6 | ~57M | 1 / 8 |
| [W2](#w2-full-history-is-re-sent-every-turn-with-no-budget) | Full history re-sent, no budget | multiplies the rest | all | ~33M | — |
| [W3](#w3-no-time-awareness-and-no-early-output-file) | No time awareness, no early output | 8 | 5 | small | 0 / 7 |
| [W4](#w4-long-commands-hit-a-60-s-limit-and-lose-their-output) | 60 s command limit loses output | 2 | 5 | ~2.4M | 0 / 2 |
| [W5](#w5-the-action-parser-is-fragile) | Fragile action parser | 1 (counted in W1) | 6 | inside W1 | — |
| [W6](#w6-a-crash-means-the-task-is-never-graded) | A crash means no grading | 6 (counted in W1) | — | — | — |
| [W7](#w7-stalls-and-exact-repeats) | Stalls and exact repeats | 0 | 7 | ~2M | — |
| [W8](#w8-output-quality-and-unstated-requirements) | Output quality, unstated requirements | 5 | 1 | — | 0 / 2 |
| [W9](#w9-working-it-out-in-its-head) | Working it out in its head | triggers W1 | many | — | — |

### W1: Reasoning overruns are run as commands

**What happens.** When the thinking uses up the whole 8,192-token budget, the server returns an
empty answer, and three things go wrong:

- **`llm.py` swaps in the unfinished thinking** as the reply
  ([L137–148](../../agent/llm.py#L137-L148)).
- **`agent.py` keeps it in the history**, where it is re-sent on every later turn
  ([L159](../../agent/agent.py#L159)).
- **`tools.py` runs the first code-fence-like text it finds in it**
  ([L89–96](../../agent/tools.py#L89-L96)).

**Evidence**

| Measure | Value |
|---|---|
| Overrun turns | 508 of 2,420 (**21%**) |
| Output tokens | 4.16M of 5.74M (**72%**) |
| Input tokens re-sending them | 52.4M (**60%**) |
| Chance of another overrun, after a normal turn | 5% |
| Chance of another overrun, after an overrun | **73%** |
| Commands run out of overrun text that failed | 405 of 413 (**98%**). For normal replies: 12% |
| Time cost of one overrun | about 60–80 s (at 100–140 output tokens/s) |
| Timeouts that were mostly spent generating (≥80% of budget) | 23 of 27 |
| Present in | 25/26 failed timeouts, 6/6 overflows, 11/27 passes |

- **The executed "commands" are garbage.** They are hex dumps, C fragments and regexes the model was
  quoting to itself. For example, `qemu-startup` ran `0: 84 00 14 00 …`.
- **The model notices but can't escape.** It says things like "The system is executing my entire
  response as bash" (`feal-differential-cryptanalysis`, msg 28). Whatever the harness sends back,
  the next turn recovers only 23–30% of the time.
- **Longer context, more overruns.** With the previous turn normal, the overrun rate is 5% below
  25K tokens of context, 21% at 50–75K and 43% at 100–125K. It still rises (11% → 55%) within the
  heavily affected trials alone. This is a correlation, not proof of cause.
- **Downstream damage:**
  - all **6 context overflows** (25–30 overruns × 8K tokens fill the window)
  - **1 false "done"**: in `llm-inference-batching-scheduler` the thinking contained "(or
    TASK_COMPLETE)", which the parser took as the finish signal
  - **235 exact repeat replies**, because an identical nudge at low temperature gives the same
    output

#### Fresh overruns vs repeats

Overruns split into two very different groups:

| | Fresh (previous turn normal) | Repeat (previous turn an overrun) |
|---|---:|---:|
| Count | **150** | **358** |
| Median share of lines repeated within the reply | 5% | 58% |
| Replies with more than 15% repeated lines | 59 (39%) | 255 (71%) |
| Model says it is looping ("going in circles", "overthinking") | 35 (23%) | 242 (68%) |

- **Overruns are a tail, not the model's habit.** In the 21 trials with no overruns, turns average
  about 460 output tokens.
- **Repeats are mostly loops caused by the history.** The model rereads its own cut-off thinking
  and circles. That's the symptom, and
  [Fix 1](full-89-0924-fix-priorities.md#fix-1-handle-reasoning-overruns) removes it.
- **Fresh overruns are the cause.**
  - About 60% look like genuine long derivations: hand-tracing a cipher, decoding hex, designing an
    algorithm in its head ([W9](#w9-working-it-out-in-its-head)).
  - About 40% are already looping.
  - The [cause track](full-89-0924-fix-priorities.md#cause-track-fewer-overruns-at-the-source) targets them.
- **Not yet known:** what *triggers* a fresh overrun, whether a large output, an error or a design
  decision. That's step [C0](full-89-0924-fix-priorities.md#c0-classify-what-triggers-fresh-overruns).

**Cost.** It is the primary cause of 23 failures plus the false "done". About 15 of those tasks are
hard anyway. The plausible recoveries are `raman-fitting`, `video-processing`, `polyglot-c-py`,
`prove-plus-comm`, `qemu-startup` (once its verifier works), `password-recovery`, `tune-mjcf`,
`custom-memory-heap-crash` and `llm-inference-batching-scheduler`.

### W2: Full history is re-sent every turn, with no budget

**What happens.** Every call re-sends the system prompt, the instruction and every earlier message
([`agent.py` L131–184](../../agent/agent.py#L131-L184)). The agent has no token budget, doesn't
track elapsed time, and never compacts the history.

**Evidence**

- **Command output is 26% of input.** Outputs are usually small (median 346 characters), but the 8%
  over 4K characters make up 44% of that. Only 103 hit the 6,000-character cap
  ([`tools.py` L99–105](../../agent/tools.py#L99-L105)).
- **Old replies are another 11%** of input.
- **Failing trials cost about 8× passing ones** (median 1.38M vs 173K tokens), and 18 failing
  trials used more than 1.5M each.

**Cost.** It isn't the main cause of any single failure. It multiplies the cost of every other
weakness, and it is why the score is negative.

### W3: No time awareness and no early output file

**What happens.** The agent can't see its time budget, and nothing prompts it to write its output
early. It keeps exploring or polishing until it is killed. **15 of the 32 graded failures never
created the output file the tests check.**

**Evidence**

- `chess-best-move`: its own engine printed `Checkmate: True` for two moves (msg 55). The agent never
  wrote `/app/move.txt`.
- `train-fasttext`: it reached 0.6237 accuracy locally at turn 85 (the target was 0.62) but never
  saved that model. The model it left in place scored 0.5977.
- `torch-pipeline-parallelism`: it had a working version by about turn 38, kept adding tests, and
  never declared done.
- `overfull-hbox` (750 s budget): it spent about 15 turns debugging its own Perl search tool.

**Cost.** Primary cause of 8 failures; 7 are plausibly recoverable.

### W4: Long commands hit a 60 s limit and lose their output

**What happens.** A command that runs past 60 s returns only
`[command did not complete: …]`, and all its output is lost
([`tools.py` L131–134](../../agent/tools.py#L131-L134)). The process keeps running (for example,
still holding the `apt` lock). So the model backgrounds its jobs and polls with `sleep`, which
costs a full LLM call per poll.

**Evidence**

- **31 timeouts** across 18 trials.
- **151 turns that begin with `sleep`**: 56 in `train-fasttext`, 29 in `compile-compcert`, 20 in
  `extract-moves-from-video`. Together about 2.4M tokens.
- **Both turn-cap failures ran out of turns this way.** `compile-compcert` finished building at turn
  96. `install-windows-3.11` was nearly done at turn 100.
- **`pkill -f <pattern>` killed the agent's own shell** 5 times in 3 trials.

### W5: The action parser is fragile

**What happens.** In [`CODE_BLOCK_RE`](../../agent/tools.py#L41), the `bash` tag is optional, and
the parser takes the first match.

- **Prose runs as bash.** After a ` ```python ` or ` ```c ` block, the closing fence matches as an
  *opening* one, and the prose after it gets run: **165 times**, all inside overrun text.
- **Untagged blocks are almost never real commands.** Of 251 run, 249 failed.
- **`TASK_COMPLETE` counts anywhere in the text**
  ([L94](../../agent/tools.py#L94)), even inside cut-off thinking.
- **Only the first block runs.** 22 replies had more than one.

For comparison, `bash`-tagged blocks in normal replies succeed 87% of the time. The dashboard's
copy of the same regex must change at the same time (`scripts/build_dashboard.py`).

### W6: A crash means the task is never graded

**What happens.** Errors from `llm.chat()` aren't caught. When `run()` raises anything other than a
timeout, Harbor **skips the verifier**. All 6 overflow trials have `verifier: null`.
`video-processing` had a runnable answer script about 25 turns before it overflowed, and was never
graded.

### W7: Stalls and exact repeats

**What happens.** The history keeps the failed reply, and the retry message is always the same, so
the model copies its own last reply.

- **238 retry prompts ("nudges").** 145 came after short no-command replies like "Let's start by
  exploring the environment."; 93 after overruns.
- **32 of 68 trials stall on their very first turn.** `headless-terminal` repeated the same line
  **24 times in a row** before running anything.
- **73 short replies are missing the leading newline**, meaning the answer was empty after brief
  thinking. One possibility is a native tool call that the server strips out and the harness
  ignores (`message.tool_calls`). This is unverified.

### W8: Output quality and unstated requirements

Five genuine misses. Most of them aren't harness problems.

| Trial | What went wrong |
|---|---|
| `configure-git-webserver` | Tests log in as `git@localhost` with password `password`. The instruction never says so |
| `build-pov-ray` | Built and rendered correctly, but from a different source archive than the tests expect |
| `adaptive-rejection-sampler` | Tests call `ars(f, domain, n)`; the agent chose `ars(n, f, lb, ub)` (7/9 tests) |
| `query-optimize` | Correct answer, but 1.26 s against a 0.98 s limit. Stopped with ~450 s left |
| `filter-js-from-html` | Only hand-made tests; 8 XSS vectors got through and 5/12 clean files were modified |
| `polyglot-c-py` (contributing) | Left its own `cmain` build output next to the answer |

### W9: Working it out in its head

This isn't a separate failure bucket. It's what usually *starts* an overrun: the model tries to
solve the core problem inside its thinking instead of running a small script. Examples: tracing
cipher rounds, decoding hex dumps, reading chess pieces pixel by pixel. See
[Appendix B](#appendix-b-turn-strings) for the first overrun in each affected trial.

Cause fixes aimed at this: [C3](full-89-0924-fix-priorities.md#c3-prompt-lines-that-change-how-it-thinks)
(prompt lines) and [C5](full-89-0924-fix-priorities.md#c5-steer-large-outputs-toward-scripts) (steer large outputs
toward scripts).

## Fixes at a glance

The full list, with what to build, the evidence, the risks and the
[plan of work](full-89-0924-fix-priorities.md#plan-of-work), is in **[full-89-0924-fix-priorities.md](full-89-0924-fix-priorities.md)**. It has two
tracks.

**Harness fixes (symptom track)**, ranked by expected score gain:

| # | Fix | Addresses | Rough score gain |
|---:|---|---|---:|
| 0 | [Log `finish_reason` and timing; switch to the approved model](full-89-0924-fix-priorities.md#fix-0-instrumentation-and-the-approved-model) | all | needed to trust the rest |
| 1 | [Handle reasoning overruns](full-89-0924-fix-priorities.md#fix-1-handle-reasoning-overruns) | W1, W5, W6, W7 | **+0.43 to +0.62** |
| 2 | [Trim the context by fixed rules](full-89-0924-fix-priorities.md#fix-2-trim-the-context-by-fixed-rules) | W2, W1 | +0.15 to +0.22 |
| 3 | [Harden the parser](full-89-0924-fix-priorities.md#fix-3-harden-the-parser) | W5 | +0.01 to +0.02 |
| 4 | [Crash safety and per-task budgets](full-89-0924-fix-priorities.md#fix-4-crash-safety-and-per-task-budgets) | W6, W2 | +0.01 to +0.05 |
| 5 | [Write a best-effort output early](full-89-0924-fix-priorities.md#fix-5-write-a-best-effort-output-early) | W3 | +0.02 to +0.045 |
| 6 | [Long-running commands](full-89-0924-fix-priorities.md#fix-6-long-running-commands) | W4 | +0.03 to +0.05 |
| 7 | [Stall breaker](full-89-0924-fix-priorities.md#fix-7-stall-breaker) | W7 | +0.02 to +0.03 |
| 8–10 | [Hygiene prompt, completion checklist, more turns](full-89-0924-fix-priorities.md#ranking-at-a-glance) | W8, W4 | small |

**Cause track** (fewer fresh overruns). Judged by the fresh-overrun rate, not by a score estimate:

| # | Cause fix | Targets |
|---|---|---|
| [C0](full-89-0924-fix-priorities.md#c0-classify-what-triggers-fresh-overruns) | Classify what triggers fresh overruns (no code) | which of the fixes below to build |
| [C1](full-89-0924-fix-priorities.md#c1-probe-the-gateway) | Probe the gateway for budget, thinking and sampling options | whether C2/C4 are possible |
| [C2](full-89-0924-fix-priorities.md#c2-warmer-sampling) | Warmer sampling | fresh overruns that loop (~40%) |
| [C3](full-89-0924-fix-priorities.md#c3-prompt-lines-that-change-how-it-thinks) | "Use the machine" and "you have more turns" | [W9](#w9-working-it-out-in-its-head) |
| [C4](full-89-0924-fix-priorities.md#c4-bigger-thinking-budget-on-retry-only) | Bigger thinking budget, on retry only | genuine long derivations (~60%) |
| [C5](full-89-0924-fix-priorities.md#c5-steer-large-outputs-toward-scripts) | Steer large outputs toward scripts | hand-decoding dumps |
| [C6](full-89-0924-fix-priorities.md#c6-context-trimming) | Context trimming (same as Fix 2) | overruns rising with context size |

## What is beyond the model

**19 of the 39 agent-caused failures probably fail even with a healthy harness.** Together they
burned **53.2M tokens (0.53 points)**, so the realistic win on them is **failing cheaply**.

| Why | Tasks |
|---|---|
| Heavy reasoning or synthesis | `gpt2-codegolf`, `write-compressor`, `regex-chess`, `circuit-fibsqrt`, `polyglot-rust-c`, `winning-avg-corewars`, `schemelike-metacircular-eval`, `feal-differential-cryptanalysis`, `path-tracing`, `path-tracing-reverse`, `make-mips-interpreter`, `make-doom-for-mips`, `fix-ocaml-gc` |
| Long build that doesn't fit the budget | `caffe-cifar-10` |
| Domain knowledge | `protein-assembly` |
| Requirement not stated in the task | `configure-git-webserver`, `build-pov-ray` |
| Adversarial quality | `break-filter-js-from-html`, `filter-js-from-html` |

**The other 20 are plausibly recoverable** (29.2M tokens between them). Expect 6–10 of them to
convert once the fixes are in.

## Limits of this analysis

- **Model mismatch.** The rates may differ on `Qwen3.6-27B-FP8`. The harness bugs will not.
- **Overruns are inferred, not logged.** 41 of the 508 end at a sentence boundary, and 13 long
  replies that start with a newline weren't counted. Even if all of those were misclassified, the
  conclusions would hold. [Fix 0](full-89-0924-fix-priorities.md#fix-0-instrumentation-and-the-approved-model)
  settles it.
- **Replays are static.** They ignore behavior changes, such as the agent re-reading a file after
  trimming. For the same reason they can't price a change in the model's behavior, so cause fixes
  have no score estimate.
- **Triggers of fresh overruns weren't analyzed.** "Working it out in its head" comes from how
  overruns *start*, not from what preceded them. See
  [C0](full-89-0924-fix-priorities.md#c0-classify-what-triggers-fresh-overruns).
- **One sample per task.** Per-task recoverability is a judgment call. Whether `chess-best-move`'s
  moves, `train-fasttext`'s accuracy or `video-processing`'s script were actually right is
  unverified.
- **Endpoint load varies.** The endpoint is shared, so throughput, and with it the timeouts, differ
  from run to run.

---

## Appendix A: per-trial results

### A.1 Failures

The 40 trials that ran and did not pass: 39 caused by the agent, plus `qemu-alpine-ssh` (verifier broken). **Recoverable:** Y = likely, M = maybe, N = unlikely.

| Trial | Outcome | Tests | Turns (overruns) | Tokens | Primary | Also | Recoverable | What happened |
|---|---|---|---|---:|---|---|:-:|---|
| `build-pov-ray` | done, wrong | 2/3 | 63 (0) | 1.86M | [W8](#w8-output-quality-and-unstated-requirements) | — | N | Built and rendered correctly, but from the Generic-Unix zip; tests expect the official archive. |
| `configure-git-webserver` | done, wrong | 0/1 | 17 (0) | 0.07M | [W8](#w8-output-quality-and-unstated-requirements) | — | N | Tests log in as `git` / `password`, which the instruction never states. |
| `filter-js-from-html` | done, wrong | 0/2 | 23 (7) | 0.99M | [W8](#w8-output-quality-and-unstated-requirements) | [W1](#w1-reasoning-overruns-are-run-as-commands) | N | Only hand-made tests. 8 XSS vectors got through; 5/12 clean files modified. |
| `llm-inference-batching-scheduler` | done, wrong | 1/6 | 16 (7) | 0.48M | [W5](#w5-the-action-parser-is-fragile) | [W1](#w1-reasoning-overruns-are-run-as-commands) | M | Overrun text said "(or TASK_COMPLETE)" and was parsed as done. No output written. |
| `qemu-alpine-ssh` | done, wrong | — | 57 (1) | 0.79M | infra (verifier) | — | Y | Agent verified the ssh login 3/3. The test script's `apt-get install curl` got a 404, so pytest never ran. |
| `query-optimize` | done, wrong | 5/6 | 17 (1) | 0.15M | [W8](#w8-output-quality-and-unstated-requirements) | — | M | Stopped at the first correct query: 1.26 s vs a 0.98 s limit, with ~450 s unused. |
| `compile-compcert` | turn cap | 2/3 | 100 (0) | 1.26M | [W4](#w4-long-commands-hit-a-60-s-limit-and-lose-their-output) | [W2](#w2-full-history-is-re-sent-every-turn-with-no-budget) | M | 29 `sleep` polls. Build succeeded at turn 96; runtime lib missing at the 100-turn cap. |
| `install-windows-3.11` | turn cap | 2/4 | 100 (0) | 2.68M | [W4](#w4-long-commands-hit-a-60-s-limit-and-lose-their-output) | [W2](#w2-full-history-is-re-sent-every-turn-with-no-budget) | M | Competent run cut off by the 100-turn cap at 1,409 s (16 one-grep turns). |
| `adaptive-rejection-sampler` | timeout | 7/9 | 47 (2) | 1.61M | [W8](#w8-output-quality-and-unstated-requirements) | [W7](#w7-stalls-and-exact-repeats), [W4](#w4-long-commands-hit-a-60-s-limit-and-lose-their-output), [W3](#w3-no-time-awareness-and-no-early-output-file) | M | Tests call `ars(f, domain, n)`; agent chose `ars(n, f, lb, ub)`. 8 stalls in the first 18 turns; apt timeouts. |
| `break-filter-js-from-html` | timeout | 0/1 | 29 (18) | 1.50M | [W1](#w1-reasoning-overruns-are-run-as-commands) | [W2](#w2-full-history-is-re-sent-every-turn-with-no-budget), [W5](#w5-the-action-parser-is-fragile) | N | 18/29 turns overran, 17 prose executions. `/app/out.html` never written. |
| `caffe-cifar-10` | timeout | 0/6 | 30 (9) | 1.49M | [W1](#w1-reasoning-overruns-are-run-as-commands) | [W4](#w4-long-commands-hit-a-60-s-limit-and-lose-their-output), [W3](#w3-no-time-awareness-and-no-early-output-file) | N | Overruns during a long from-source build. Nothing built in 1,200 s. |
| `chess-best-move` | timeout | 0/1 | 28 (7) | 0.74M | [W3](#w3-no-time-awareness-and-no-early-output-file) | [W1](#w1-reasoning-overruns-are-run-as-commands) | M | Own engine verified two mates (msg 55). `/app/move.txt` never written. |
| `custom-memory-heap-crash` | timeout | 5/6 | 45 (23) | 2.98M | [W1](#w1-reasoning-overruns-are-run-as-commands) | [W2](#w2-full-history-is-re-sent-every-turn-with-no-budget) | M | Mostly normal until turn 24, then 21 overruns in a row. 5/6 tests. |
| `extract-moves-from-video` | timeout | 0/2 | 65 (1) | 1.15M | [W3](#w3-no-time-awareness-and-no-early-output-file) | [W4](#w4-long-commands-hit-a-60-s-limit-and-lose-their-output) | M | 20 `sleep` polls on OCR. `/app/solution.txt` never written. |
| `feal-differential-cryptanalysis` | timeout | — | 30 (27) | 3.00M | [W1](#w1-reasoning-overruns-are-run-as-commands) | [W5](#w5-the-action-parser-is-fragile) | N | 27/30 turns overran, 24 prose executions. Verifier also crashed (Docker). |
| `gcode-to-text` | timeout | 0/2 | 54 (4) | 1.56M | [W3](#w3-no-time-awareness-and-no-early-output-file) | [W1](#w1-reasoning-overruns-are-run-as-commands), [W2](#w2-full-history-is-re-sent-every-turn-with-no-budget) | M | 54 turns of rendering attempts. `/app/out.txt` never written. |
| `gpt2-codegolf` | timeout | 0/1 | 29 (11) | 1.05M | [W1](#w1-reasoning-overruns-are-run-as-commands) | [W7](#w7-stalls-and-exact-repeats) | N | 11 overruns and 9 stalls in a row. `/app/gpt2.c` never written. |
| `mailman` | timeout | 2/3 | 91 (19) | 4.85M | [W3](#w3-no-time-awareness-and-no-early-output-file) | [W1](#w1-reasoning-overruns-are-run-as-commands) | M | 91 turns of postfix debugging over 1,800 s; overruns from turn 49. 2/3 tests. |
| `make-doom-for-mips` | timeout | 0/3 | 57 (5) | 2.24M | [W3](#w3-no-time-awareness-and-no-early-output-file) | [W1](#w1-reasoning-overruns-are-run-as-commands) | N | Large port still mid-build at 900 s. |
| `make-mips-interpreter` | timeout | 0/3 | 66 (21) | 4.99M | [W1](#w1-reasoning-overruns-are-run-as-commands) | [W7](#w7-stalls-and-exact-repeats) | N | Overrun streak in the final third. |
| `overfull-hbox` | timeout | 3/4 | 38 (1) | 0.82M | [W3](#w3-no-time-awareness-and-no-early-output-file) | [W9](#w9-working-it-out-in-its-head) | M | Built and debugged its own Perl search tool instead of iterating. 3/4 tests in 750 s. |
| `password-recovery` | timeout | 0/2 | 31 (9) | 0.95M | [W1](#w1-reasoning-overruns-are-run-as-commands) | [W3](#w3-no-time-awareness-and-no-early-output-file) | M | 9 overruns, ending in a 6-turn streak. Recovery file never written. |
| `path-tracing` | timeout | 0/5 | 63 (16) | 4.30M | [W1](#w1-reasoning-overruns-are-run-as-commands) | [W7](#w7-stalls-and-exact-repeats), [W3](#w3-no-time-awareness-and-no-early-output-file) | N | Ends in 10 overrun stalls in a row. `/app/image.c` never written. |
| `path-tracing-reverse` | timeout | 0/3 | 60 (18) | 5.63M | [W1](#w1-reasoning-overruns-are-run-as-commands) | [W3](#w3-no-time-awareness-and-no-early-output-file) | N | Hand-traced disassembly while thinking. `/app/mystery.c` never written. |
| `polyglot-c-py` | timeout | 0/1 | 24 (11) | 0.89M | [W1](#w1-reasoning-overruns-are-run-as-commands) | [W8](#w8-output-quality-and-unstated-requirements) | M | 11/24 turns overran. Left its own `cmain` build output next to the answer. |
| `polyglot-rust-c` | timeout | 0/1 | 17 (16) | 1.09M | [W1](#w1-reasoning-overruns-are-run-as-commands) | — | N | 16 of 17 turns overran. Nothing written. |
| `protein-assembly` | timeout | 0/1 | 47 (16) | 2.59M | [W1](#w1-reasoning-overruns-are-run-as-commands) | [W7](#w7-stalls-and-exact-repeats) | N | Overrun/stall spiral at the end. `/app/gblock.txt` never written. |
| `prove-plus-comm` | timeout | 2/4 | 16 (11) | 0.57M | [W1](#w1-reasoning-overruns-are-run-as-commands) | — | M | Coq proof; the last 11 turns all overran. `.vo` never compiled. |
| `qemu-startup` | timeout | — | 18 (11) | 0.63M | [W1](#w1-reasoning-overruns-are-run-as-commands) | — | M | Hand-decoded ISO bytes while thinking; 11 overruns in a row. Verifier also broken (Debian 404). |
| `raman-fitting` | timeout | 0/3 | 19 (11) | 0.60M | [W1](#w1-reasoning-overruns-are-run-as-commands) | [W7](#w7-stalls-and-exact-repeats) | Y | Simple two-peak fit; 11 overrun stalls in a row from turn 9. `results.json` never written. |
| `torch-pipeline-parallelism` | timeout | — | 43 (3) | 0.94M | [W3](#w3-no-time-awareness-and-no-early-output-file) | [W4](#w4-long-commands-hit-a-60-s-limit-and-lose-their-output) | M | Working version by ~turn 38; kept adding tests, never finished. Verifier then timed out. |
| `train-fasttext` | timeout | 1/2 | 89 (0) | 1.08M | [W3](#w3-no-time-awareness-and-no-early-output-file) | [W4](#w4-long-commands-hit-a-60-s-limit-and-lose-their-output) | M | Reached 0.6237 locally (turn 85) but never saved it. Submitted model scored 0.5977. |
| `tune-mjcf` | timeout | 1/4 | 30 (13) | 0.92M | [W1](#w1-reasoning-overruns-are-run-as-commands) | [W5](#w5-the-action-parser-is-fragile) | M | 13 prose executions in a row. `/app/model.xml` never written. |
| `write-compressor` | timeout | 0/3 | 17 (14) | 0.89M | [W1](#w1-reasoning-overruns-are-run-as-commands) | [W5](#w5-the-action-parser-is-fragile) | N | 14 of 17 turns overran, 14 prose executions. |
| `circuit-fibsqrt` | overflow | — | 40 (30) | 3.77M | [W1](#w1-reasoning-overruns-are-run-as-commands) | [W6](#w6-a-crash-means-the-task-is-never-graded), [W7](#w7-stalls-and-exact-repeats) | N | 30/40 turns overran, 28 nudges in a row. Overflowed; never graded. |
| `fix-ocaml-gc` | overflow | — | 50 (28) | 4.04M | [W1](#w1-reasoning-overruns-are-run-as-commands) | [W6](#w6-a-crash-means-the-task-is-never-graded), [W5](#w5-the-action-parser-is-fragile) | N | 28/50 turns overran. Overflowed; never graded. |
| `regex-chess` | overflow | — | 40 (29) | 4.23M | [W1](#w1-reasoning-overruns-are-run-as-commands) | [W6](#w6-a-crash-means-the-task-is-never-graded) | N | 29/40 turns overran. Overflowed; never graded. |
| `schemelike-metacircular-eval` | overflow | — | 54 (26) | 5.52M | [W1](#w1-reasoning-overruns-are-run-as-commands) | [W6](#w6-a-crash-means-the-task-is-never-graded), [W5](#w5-the-action-parser-is-fragile) | N | 26/54 turns overran. Overflowed; never graded. |
| `video-processing` | overflow | — | 56 (25) | 4.31M | [W1](#w1-reasoning-overruns-are-run-as-commands) | [W6](#w6-a-crash-means-the-task-is-never-graded) | M | A runnable analyzer existed ~25 turns before the overflow. Never graded. |
| `winning-avg-corewars` | overflow | — | 42 (29) | 4.01M | [W1](#w1-reasoning-overruns-are-run-as-commands) | [W6](#w6-a-crash-means-the-task-is-never-graded) | N | 29/42 turns overran. Overflowed; never graded. |

### A.2 Passes

`regex-log` hit the time limit but had already written its answer.

| Trial | Turns (overruns) | Tokens | Agent time |
|---|---|---:|---:|
| `multi-source-data-merger` | 6 (0) | 0.02M | 52 s |
| `pypi-server` | 10 (0) | 0.02M | 50 s |
| `modernize-scientific-stack` | 8 (0) | 0.03M | 45 s |
| `log-summary-date-ranges` | 11 (0) | 0.04M | 51 s |
| `kv-store-grpc` | 18 (0) | 0.06M | 79 s |
| `db-wal-recovery` | 14 (0) | 0.08M | 89 s |
| `fix-git` | 15 (0) | 0.08M | 48 s |
| `code-from-image` | 21 (0) | 0.09M | 181 s |
| `distribution-search` | 6 (2) | 0.10M | 241 s |
| `crack-7z-hash` | 20 (0) | 0.11M | 202 s |
| `git-multibranch` | 22 (0) | 0.12M | 162 s |
| `pytorch-model-cli` | 23 (0) | 0.14M | 192 s |
| `count-dataset-tokens` | 23 (0) | 0.14M | 192 s |
| `fix-code-vulnerability` | 24 (0) | 0.17M | 41 s |
| `merge-diff-arc-agi-task` | 29 (0) | 0.17M | 330 s |
| `largest-eigenval` | 28 (0) | 0.23M | 469 s |
| `extract-elf` | 16 (2) | 0.25M | 339 s |
| `cancel-async-tasks` | 18 (1) | 0.26M | 486 s |
| `bn-fit-modify` | 31 (1) | 0.30M | 380 s |
| `mcmc-sampling-stan` | 48 (0) | 0.36M | 1034 s |
| `cobol-modernization` | 27 (1) | 0.43M | 495 s |
| `torch-tensor-parallelism` | 25 (2) | 0.44M | 515 s |
| `headless-terminal` | 49 (1) | 0.45M | 316 s |
| `financial-document-processor` | 34 (2) | 0.56M | 637 s |
| `feal-linear-cryptanalysis` | 20 (5) | 0.80M | 489 s |
| `regex-log` (timed out) | 26 (2) | 1.48M | 893 s |
| `portfolio-optimization` | 42 (6) | 1.51M | 1023 s |
| `reshard-c4-data` | 48 (3) | 1.59M | 576 s |

### A.3 Infrastructure failures

**`docker compose up` crashed (exit −11) for all 18 at 12:39:18 UTC:** `build-cython-ext`, `build-pmars`, `constraints-scheduling`, `dna-assembly`, `dna-insert`, `git-leak-recovery`, `large-scale-text-editing`, `model-extraction-relu-logits`, `nginx-request-logging`, `openssl-selfsigned-cert`, `pytorch-model-recovery`, `rstan-to-pystan`, `sam-cell-seg`, `sanitize-git-repo`, `sparql-university`, `sqlite-db-truncate`, `sqlite-with-gcov`, `vulnerable-secret`.

**Environment start took longer than 600 s (3):** `hf-model-inference`, `mteb-leaderboard`, `mteb-retrieve`.

**Verifier broken by the Debian mirror 404:** `qemu-alpine-ssh` (listed in A.1) and `qemu-startup` (also a timeout).

## Appendix B: turn strings

One character per assistant turn. A trial that gets stuck shows up as an unbroken run of `X`, `N`
or `P`.

| Symbol | Meaning |
|---|---|
| `.` | ran a command, exit 0 |
| `x` | ran a command, non-zero exit |
| `T` | command hit the 60 s limit |
| `n` | no command (nudged) |
| `D` | `TASK_COMPLETE` |
| `F` | overrun; a command was taken from it, exit 0 |
| `X` | overrun; a command was taken from it, non-zero exit |
| `N` | overrun with no command (nudged) |
| `P` | overrun; prose after a non-bash fence was run as bash |

```
adaptive-rejection-sampler       fail timeout  Pnnn.n.nTnx.Px.n.n.......xxxxx.T..x...x....x...
bn-fit-modify                    pass          nnn.x......xx.xx.x......Xn....D
break-filter-js-from-html        fail timeout  nnn..P.P.n..PP.NPPPPPPPPPPPPP
build-pov-ray                    fail          ........x..............xx......................x..............D
caffe-cifar-10                   fail timeout  ....Xxx.Xxx.Xx.XxXxxxxxXxXxXxX
cancel-async-tasks               pass          P....x.n....x.x..D
chess-best-move                  fail timeout  nxxn.........X.X...X.XX.XX..
circuit-fibsqrt                  —    overflow nnnnn...P..XNNNNNNNNNNNNNNNNNNNNNNNNNNNN
cobol-modernization              pass          n..x..............X.......D
code-from-image                  pass          xx.......x..........D
compile-compcert                 fail          nx..xT..xxxxxxx.xxx.................................................x.......xxxxxx.x....x..xxx..x...
configure-git-webserver          fail          ...TxTx.....x...D
count-dataset-tokens             pass          nn..T.................D
crack-7z-hash                    pass          .x......Tx....x..x.D
custom-memory-heap-crash         fail timeout  ......xP.............P..XXXXXXXXXXXXXXXXXXXXX
db-wal-recovery                  pass          nx...........D
distribution-search              pass          N.N..D
extract-elf                      pass          x...N.x...P....D
extract-moves-from-video         fail timeout  ....T..xx.............F..x.....nT..........x....Tx....T..........
feal-differential-cryptanalysi   —    timeout  .P.PP.PXPPPPPPPPNNPPPPPPPPPPPP
feal-linear-cryptanalysis        pass          n..NNXPP...x.......D
filter-js-from-html              fail          P..PP.xP...X..P..N....D
financial-document-processor     pass          nx.............T..T.......X.nP...D
fix-code-vulnerability           pass          .......................D
fix-git                          pass          .............xD
fix-ocaml-gc                     —    overflow nnn..................P.NPPPPPPPPPPPPPPPPPPPPPPPPPP
gcode-to-text                    fail timeout  x..x..........x...................Nn..............X.NF
git-multibranch                  pass          ........x.x..........D
gpt2-codegolf                    fail timeout  Xnn..x.x.nnnnnnnnn.XXXXXXXXXX
headless-terminal                pass          nnnnnnnnnnnnnnnnnnnnnnnn..Pn....x.x...x...x.....D
install-windows-3.11             fail          ...................xx.....xx.........Txxxx......x....................n........................x..T..
kv-store-grpc                    pass          nnnn.........x...D
largest-eigenval                 pass          n.nnn.n...x.x..x..........xD
llm-inference-batching-schedul   fail          .....X.N.XNN..ND
log-summary-date-ranges          pass          ..........D
mailman                          fail timeout  n..........x................x...................N.x...P..P.Xx..x....FP.F.N.XP.X.PxF.FXFX.FX
make-doom-for-mips               fail timeout  n...................x...........P....Pxx......P.P.P...x..
make-mips-interpreter            fail timeout  nn...........x..................P..P..PP...PNPNnNN.....XXXXXXXXXNX
mcmc-sampling-stan               pass          ..........x...T................................D
merge-diff-arc-agi-task          pass          .x..T....x........x.........D
modernize-scientific-stack       pass          n......D
multi-source-data-merger         pass          .....D
overfull-hbox                    fail timeout  n.........Pnx....x.nTx..x...x.xx..xx..
password-recovery                fail timeout  ........x...x.Xx..x.X..X.XXXXXX
path-tracing-reverse             fail timeout  nx...............X......X..XX.N.X.x.x..xxXx.XXNx.x.xXXXXXXXX
path-tracing                     fail timeout  nnn.x......n......................X..X.......XnX..X.XNNNNNNNNNN
polyglot-c-py                    fail timeout  Xx..x..PXXx.....XX.XXXXX
polyglot-rust-c                  fail timeout  X.XXXXXXXXXXXXXXX
portfolio-optimization           pass          nnn.....P.....T.x..PPPPP...x.............D
protein-assembly                 fail timeout  ......NX.N..T.................PN.nN.N.NNNNNNNNN
prove-plus-comm                  fail timeout  ....xXXXXXXXXXXX
pypi-server                      pass          .........D
pytorch-model-cli                pass          nn...x................D
qemu-alpine-ssh                  fail          .....x.......xx..xx..xx.xx....x.......x.x....x.....N....D
qemu-startup                     fail timeout  .......XXXXXXXXXXX
query-optimize                   fail          nnn.....TP......D
raman-fitting                    fail timeout  n..x....NNNNNNNNNNN
regex-chess                      —    overflow Nnnnnnn..XP.PXXXXXXXXXXXXXXXXXXXXnXXnXXX
regex-log                        pass timeout  XnxX.nnnnnnnnnnnnxx.......
reshard-c4-data                  pass          ....X..Px............xP........................D
schemelike-metacircular-eval     —    overflow nnn........P.P........PXXPPPPPPPPPPPPPPP.....PXXXx..XX
torch-pipeline-parallelism       —    timeout  Pnx......x...Tx.P....................NT....
torch-tensor-parallelism         pass          Px..........P...........D
train-fasttext                   fail timeout  nnnn.x.x.T.........T.xxxxxxT.....................xxxxxxx..........xx.....................
tune-mjcf                        fail timeout  n.....x.xxx.xx.xxPPPPPPPPPPPPP
video-processing                 —    overflow nnnnnx....................N.NP.X.X.XX.XXXXXXXXXXXXXXXXXX
winning-avg-corewars             —    overflow ....XX......X...XXXXXXXXXXXXXXXXXXXXXXXXXX
write-compressor                 fail timeout  nn.PPPPPPPPPPPPPP
```

**Where each stuck run began.** This is the first overrun in each trial with 5 or more overruns.
Nearly all of them start with the model working the problem out in its head
([W9](#w9-working-it-out-in-its-head)).

| Trial | Turn | Opening words |
|---|---|---|
| `break-filter-js-from-html` | 6 / 29 | The filter removes the following: 1. '<script>' tags 2. '<frame>', '<iframe>', '<object>',… |
| `caffe-cifar-10` | 5 / 30 | The network is working (apt update succeeded). So I can install packages. Let's install th… |
| `chess-best-move` | 14 / 28 | Let me analyze the pieces I've identified so far. Let me look at the shapes carefully. Bl… |
| `circuit-fibsqrt` | 9 / 40 | Now I understand the simulator. Let me analyze it carefully. The simulator: - Reads gates… |
| `custom-memory-heap-crash` | 8 / 45 | Confirmed: the program runs normally, but segfaults on exit (during static destructors). T… |
| `feal-differential-cryptanalysis` | 2 / 30 | Let me analyze the FEAL implementation carefully. The encryption function: '''python def … |
| `feal-linear-cryptanalysis` | 4 / 20 | Let me analyze the cipher carefully. The encryption function: '''c uint64_t encrypt(uint6… |
| `filter-js-from-html` | 1 / 23 | Let me think about this task. I need to create a Python file '/app/filter.py' that removes… |
| `fix-ocaml-gc` | 22 / 50 | Now I understand the structure. Let me understand the run-length compression scheme. The k… |
| `gpt2-codegolf` | 1 / 29 | Let me think about this task. I need to write a dependency-free C file that does the follo… |
| `llm-inference-batching-scheduler` | 6 / 16 | Let's analyze the data distribution and understand the cost model more deeply. Key insigh… |
| `mailman` | 49 / 91 | I'm working through the configuration setup for Mailman and Postfix. The key decision is w… |
| `make-doom-for-mips` | 33 / 57 | So 'I_ZoneBase' calls 'malloc(6MB)' for the zone. This is a big allocation. The VM has no … |
| `make-mips-interpreter` | 33 / 66 | Now I understand the structure. Let me look at the key parts: 1. The binary is a MIPS ELF… |
| `password-recovery` | 15 / 31 | This isn't a complete ext4 filesystem image — it's a 4MB file containing fragments. The st… |
| `path-tracing-reverse` | 18 / 60 | Now I understand sphere_intersect. Let me analyze: ''' sphere_intersect(struct hit *hit, … |
| `path-tracing` | 35 / 63 | Now I understand the trace function: - sphere_intersect(ray, sphere) → (hit, t, normal) - … |
| `polyglot-c-py` | 1 / 24 | The user is asking for a polyglot file '/app/polyglot/main.py.c' that works as both a Pyth… |
| `polyglot-rust-c` | 1 / 17 | I need to create a polyglot file that compiles as both Rust and C++ and prints the Nth Fib… |
| `portfolio-optimization` | 9 / 42 | Now I understand the task. Let me analyze: 1. 'portfolio_baseline.py' - Python baseline w… |
| `protein-assembly` | 7 / 47 | I've got all the PDB sequences now. I'm working through the requirements: the gBlock needs… |
| `prove-plus-comm` | 6 / 16 | The base case failed. After 'simpl', the goal is 'm = m + 0'. 'rewrite <- Nat.add_0_r' rew… |
| `qemu-startup` | 8 / 18 | Interesting! The record length is 0x84 = 132. But the name field... let me look at the hex… |
| `raman-fitting` | 9 / 19 | The data is sorted in ascending order of x. The peaks are at x=19139.5 (y=79400) and x=332… |
| `regex-chess` | 1 / 40 | Let me understand the task. I need to write a JSON file '/app/re.json' that is a list of [… |
| `schemelike-metacircular-eval` | 12 / 54 | Now I understand the task. I need to write 'eval.scm' — a metacircular evaluator written i… |
| `tune-mjcf` | 18 / 30 | Let's check the structure of the model. The cable plugin creates 41 bodies (B_0 ... B_40),… |
| `video-processing` | 27 / 56 | Now I have a clean signal. Let me analyze the feet position (feet95) over time: - Frames … |
| `winning-avg-corewars` | 5 / 42 | Let me analyze the opponents: 1. **stone.red** - Classic stone bomber. Drops DAT every 4 … |
| `write-compressor` | 4 / 17 | Let me analyze this decompressor carefully. This is an arithmetic coding decompressor. Le… |

## Appendix C: method

- **Per-trial fields** come from `result.json`: reward, exception, tokens, turns and the transcript
  (`agent_result.metadata.messages`). Test counts come from `verifier/ctrf.json`.
- **Nudges** are user messages containing the opening words of `NUDGE_MESSAGE`.
- **Output truncations** are observations containing `characters omitted`.
- **Command timeouts** are observations containing `[command did not complete`.
- **The executed command** is `CODE_BLOCK_RE.search(reply)`, exactly as the agent parses it.
  - **Prose-exec** means an odd number of fences came before the match.
  - **Untagged** means the opening fence has no `bash`, `sh` or `shell` tag.
- **Overrun turn:** a reply of 10,000+ characters that doesn't start with a newline
  ([details](#how-overruns-were-identified)).
- **Overrun onset rate:** the chance of an overrun when the previous turn was not one, grouped by
  context size.
- **Fresh vs repeat overrun:** fresh means the previous assistant turn was not an overrun (or there
  was none); repeat means it was.
- **Repeated-line share:** within one overrun reply, the share of lines longer than 40 characters
  that duplicate an earlier line in the same reply.
- **Loop phrases:** the reply matches `going in circles`, `stuck in a loop`, `overthinking` or
  `keep (making|repeating|going)`.
- **Input-token split:** each message is charged its length × the number of later calls, then
  scaled by the trial's own characters-per-token ratio (median 3.06).
- **Generation-bound timeout:** `n_output_tokens / 120` s is at least 80% of the time budget.
- **Polling turn:** the executed command starts with `sleep N`.
- The scratch scripts (ad-hoc Python over `jobs/full-89-0924/`) are not committed. Every figure can
  be reproduced from these definitions.
