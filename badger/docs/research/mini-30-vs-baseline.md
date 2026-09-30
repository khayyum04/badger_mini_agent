# mini-swe-agent vs the baseline on the 30-task experiment subset

This compares the first mini-swe-agent run with the baseline's full 89-task run
([full-89-0924-failure-analysis.md](full-89-0924-failure-analysis.md)) on the **same 30 tasks**, the
[experiment subset](full-89-0924-fix-priorities.md#experiment-subset). For each weakness (W1–W9) and
cause fix (C0–C6) from the research, it says whether mini-swe-agent addressed it, with numbers.

- **Sources:** `badger/jobs/2026-09-27__12-44-55/` (mini-swe-agent) and
  `../badger_agent/jobs/full-89-0924/` (baseline). `jobs/` is gitignored, so these are local only.
- **Method:** the research's own definitions, applied to both runs. See [Method](#method).

## Contents

- [Run facts](#run-facts)
- [Summary](#summary)
- [Terms used](#terms-used)
- [Symptoms (W1–W9)](#symptoms-w1w9)
- [Cause fixes (C0–C6)](#cause-fixes-c0c6)
- [Bottom line and next steps](#bottom-line-and-next-steps)
- [Caveats](#caveats)
- [Method](#method)

## Run facts

| | mini-swe-agent | Baseline |
|---|---|---|
| Job | `2026-09-27__12-44-55` | `full-89-0924` (30 of its 89 tasks) |
| Agent | `badger_mini.harbor_agent:BadgerMiniAgent` (commit `08367552`) | `agent.agent:BaselineAgent` (`21d5c09`) |
| Model | `qwen3.8-27b` (llm-gw01 gateway; **not approved**) | `qwen3.8-27b` |
| `max_tokens` | 16,384 | 8,192 |
| Sampling | temperature 0.6, top_p 0.95 | temperature 0.2 (code default) |
| Actions | native `bash` tool calls | regex over free text |
| Command timeout | 180 s, enforced in the container (partial output kept) | 60 s (output lost) |
| Turn cap | 100 | 100 |
| Concurrency | 2 | 3 |
| Wall clock | 4 h 28 min for the 30 tasks | ~7 h for all 89 |

## Summary

| Same 30 tasks | mini-swe-agent | Baseline |
|---|---:|---:|
| Passed | **15 / 30** (TB 0.50) | 10 / 30 (0.33) |
| Hit the time limit | 3 | 15 |
| Crashed and never graded | 0 | 6 |
| Total agent time | **7.4 h** | 10.2 h |
| Total tokens | 77.2M | 67.9M |
| Leaderboard score on these 30 | −0.27 | −0.35 |

- **Fixed:** running cut-off thinking as commands (W1), the fragile parser (W5), ungraded crashes (W6),
  stalls and copy-paste loops (W7). Fresh overruns (W9, the cause) are **8× rarer**.
- **Not fixed:** token cost (W2), time awareness (W3), output quality (W8). Retries after an overrun
  still usually fail (C4 not done).
- **Tokens went up**, so the higher task score is mostly eaten by the token penalty.

Passes gained over the baseline (8): `break-filter-js-from-html`, `custom-memory-heap-crash`,
`feal-differential-cryptanalysis`, `mailman`, `path-tracing`, `prove-plus-comm`, `video-processing`,
`winning-avg-corewars`.

Passes lost (3): `feal-linear-cryptanalysis` (5 overruns in a row), `torch-tensor-parallelism` (wrong
answer), `financial-document-processor` (time limit).

## Terms used

- **Turn:** one round of the loop. The model reads everything so far, thinks, then asks to run one
  command. The command runs in the task's container, the output is shown to the model, repeat.
- **Thinking:** Qwen is a reasoning model. Before answering, it writes private notes to itself
  (scratch paper), then gives its answer.
- **Thinking budget (`max_tokens`):** the cap on how much the model may write in one turn, notes
  included.
- **Overrun:** the model fills the whole budget with notes and never answers. It thought so long it
  forgot to act.
- **History (context):** everything said so far in a task. The model has no memory between turns, so
  the whole history is sent again every turn.
- **Tokens:** word-pieces. Every million tokens sent to or from the model costs 0.01 leaderboard
  points.
- **Fresh vs repeat overrun:** a *fresh* overrun follows a normal turn, so the model started it (the
  cause). A *repeat* overrun follows another overrun, usually because the harness fed the mess back
  in (the symptom).

## Symptoms (W1–W9)

| # | Issue | Baseline → mini-swe-agent | Status |
|---|---|---|---|
| W1 | Cut-off thinking run as a command and kept in history | Executed: **343 → 0**. Overrun turns **39% → 3%**. Repeats **327 → 23** | ✅ Addressed |
| W1 | …but a retry after an overrun usually overruns again | P(overrun after overrun) **80% → 72%**. 5 tasks stop with RepeatedFormatError | ⚠️ Not fixed |
| W2 | Full history re-sent every turn, no budget | Tokens **67.9M → 77.2M**. Median fail **3.4M → 1.4M**, median pass **500K → 744K**. 12 tasks > 1.5M. 1 overflow | ❌ Not addressed |
| W3 | No time awareness; output file not written early | ~9 graded failures missing their output file in both runs. 3 timeouts | ❌ No measurable change |
| W4 | 60 s command limit loses output | Command timeouts **9 → 4**, all keep output. `sleep` polls **5 → 30**. 1 `pkill -f` | 🟡 Partly |
| W5 | Fragile parser | Prose run as bash **0**. Finish only on the submit marker | ✅ Addressed |
| W6 | A crash means no grading | Ungraded crashes **6 → 0** | ✅ Addressed |
| W7 | Stalls and exact repeats | No-command replies **67 → 3**. First-turn stalls **23 → 2**. Repeat commands **216 → 1** | ✅ Addressed |
| W8 | Output quality, unstated requirements | 3 wrong submissions, including the same `polyglot-c-py` leftover-file failure | ❌ Not addressed |
| W9 | Working it out in its head | Fresh overruns **9.1 → 1.1 per 100 turns** | ✅ Mostly addressed |

### W1: Unfinished thinking got run as a command. ✅ Fixed

**The problem.** When the baseline's model overran, the baseline took the unfinished notes and treated
them as the answer. It searched them for anything that looked like code and ran it. Picture a student
who runs out of time mid-scribble, and the teacher grades the scribbles as the final answer. The notes
also stayed in the history, so every later turn the model re-read its own confused scribbles and got
confused again.

**What mini-swe-agent does.** A reply without a tool call raises a `FormatError`. The reply is thrown
away completely, and the model gets a short note instead: "you ran out of budget, take one small
step". The scribbles never run and never go back to the model.

**Numbers:**

- Scribbles run as commands: **343 → 0**
- Turns lost to overruns: **427 of 1,102 (39%) → 37 of 1,263 (3%)**
- Repeat overruns: **327 → 23**. Removing the mess from the history mostly broke the vicious circle.
- Overruns still cost output tokens: 0.61M of 2.18M output tokens (28%). They are never re-sent as
  input.

**Not fixed.** Once an overrun happens, the next try usually overruns too: 80% of the time before,
**72% now**. The retry is identical to the first attempt (same budget, same settings), so it tends to
fail the same way. After 5 misses in a row, mini-swe-agent ends the task with RepeatedFormatError.
That happened on 5 tasks: `circuit-fibsqrt`, `feal-linear-cryptanalysis`, `polyglot-rust-c`,
`regex-chess` and `write-compressor`. `feal-linear-cryptanalysis` passed with the baseline. Each of
these failed cheaply (74K–282K tokens), where the baseline burned millions on the same pattern.

### W2: The whole history is sent again every turn. ❌ Not fixed

**The problem.** Every turn re-sends every earlier command and output. Turn 80 carries all 79 earlier
turns. It's like re-reading the whole book from page 1 before writing each new sentence. Cost grows
with every turn.

**What mini-swe-agent does.** The same thing. Its design keeps one linear history.

**Numbers:**

- Total tokens: **67.9M → 77.2M**, up, not down.
- Median failed task: **3.4M → 1.4M**. Failures no longer spiral.
- Median passed task: **500K → 744K**. The agent now works longer and more successfully on hard
  tasks, and every extra turn re-sends everything.
- Largest: `schemelike-metacircular-eval` 10.9M, `custom-memory-heap-crash` 8.8M,
  `make-mips-interpreter` 8.5M, `path-tracing-reverse` 7.2M (overflowed), `path-tracing` 6.3M.

**Why it matters.** TB rose 0.33 → 0.50, but the token penalty ate most of it: the leaderboard score
on these 30 is still negative (−0.35 → −0.27). This is the most valuable next fix
([Fix 2](full-89-0924-fix-priorities.md#fix-2-trim-the-context-by-fixed-rules)).

### W3: No sense of time, and the answer file isn't written early. ❌ No visible change

**The problem.** The agent never knows how much time is left. It keeps exploring and polishing until
it's cut off, sometimes without ever saving its answer (baseline `chess-best-move` found the move but
never wrote it to the file the tests check).

**What we did.** A prompt line only: "write the output file as soon as you have a plausible answer".
The agent still can't see a clock.

**Numbers.** About 9 graded failures in each run still had no output file when the tests ran (a rough
search of the test logs). 3 tasks ran out of time: `financial-document-processor`, `raman-fitting`,
`tune-mjcf`.

### W4: Long commands hit a time limit and lost their output. 🟡 Partly fixed

**The problem.** The baseline killed any command after 60 s and threw away everything it had printed.
For a 5-minute build, the model saw only "did not complete" and learned nothing.

**What the adapter does.** Commands get 180 s, enforced by `timeout` inside the container, so a killed
command still shows everything it printed. The prompt says to run long jobs in the background with a
log file.

**Numbers:**

- Commands that hit the limit: **9 → 4**, all with partial output.
- **New habit:** turns spent on `sleep` rose **5 → 30** (15 in `fix-ocaml-gc`). Each one is a full
  turn with the whole history re-sent.
- The model used `pkill -f` once despite the prompt telling it not to.

### W5: A sloppy command reader. ✅ Fixed

**The problem.** The baseline found commands by regex-searching free text and often grabbed the wrong
thing: prose, C code, a regex the model was quoting. It also ended the task whenever `TASK_COMPLETE`
appeared anywhere, even inside thinking.

**What mini-swe-agent does.** Native tool calls: the model fills in a structured "run this bash
command" form instead of the agent guessing. The task ends only when a command's output starts with
`COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`.

**Numbers.** Prose run as a command: **0**. The 3 wrong submissions are the model being wrong, not
the reader misreading.

### W6: A crash meant the task was never graded. ✅ Fixed

**The problem.** If the baseline crashed (usually because the history outgrew the model's context
window), Harbor skipped the tests. Whatever was built in the container was never checked.

**What the adapter does.** It catches the crash and returns normally, so Harbor still tests the
container. Endpoint configuration errors (wrong model id, bad key) still fail loudly.

**Numbers.** Ungraded crashes: **6 → 0**. `path-tracing-reverse` still overflowed but was graded (it
failed). `fix-ocaml-gc` was not graded because the job was cancelled during its tests, not because of
a crash.

### W7: Stalls and copy-paste loops. ✅ Fixed

**The problem.** The baseline's model often replied with just talk ("let's start by exploring the
environment") and no command. The same reminder went back every time, so the model repeated itself
word for word. One baseline task repeated the same line 24 times before doing anything.

**Numbers:**

- Replies with no command: **67 → 3**
- Tasks stuck on their first turn: **23 → 2**
- The same command repeated back to back: **216 → 1**

### W8: Wrong answers and unstated requirements. ❌ Not fixed

**The problem.** The agent does the work but misses a detail the tests check.

**Numbers.** 3 wrong submissions:

- **`polyglot-c-py`** failed exactly as the research describes: it left a build file (`cmain`) next
  to the answer, and the test requires only `main.py.c`. The "build in /tmp" prompt line didn't stop
  it.
- **`torch-tensor-parallelism`** failed 2 of 3 tests (a spawned worker process errored). The
  baseline passed it.
- **`protein-assembly`** wrote an invalid `gblock.txt`.

A "check before you submit" step would help
([Fix 9](full-89-0924-fix-priorities.md#fix-9-completion-checklist)).

### W9: Solving problems in its head. ✅ Mostly fixed

**The problem.** This is what usually starts an overrun. Instead of writing a small script to decode
hex or trace a cipher, the model works it out in its private notes, runs out of budget, and never
acts. It is the research's root cause, the fresh overrun.

**Numbers.** Fresh overruns: **100 → 14**, or **9.1 → 1.1 per 100 turns**, about 8× fewer. 12 of 30
tasks had at least one overrun.

**Caveat.** We can't say which change did it, because several changed at once: tool calls instead of
free text, `max_tokens` 8K → 16K, warmer sampling (0.2 → 0.6), new prompt lines ("use the machine",
"one small step"), and a history free of cut-off thinking.

## Cause fixes (C0–C6)

These target *why* the model overruns, not the damage afterwards
([cause track](full-89-0924-fix-priorities.md#cause-track-fewer-overruns-at-the-source)).

| Fix | What it means | Status |
|---|---|---|
| Fix 0: instrumentation | Record why each reply ended and how many tokens it used | ✅ Every model response (finish reason, usage, timestamp) is in `agent/mini-swe-agent.trajectory.json` |
| C0: classify triggers | Study what came right before each fresh overrun | ❌ Not done |
| C1: probe the gateway | Check which settings the UW server supports | 🟡 Tool calls and `finish_reason` confirmed. A thinking budget and `enable_thinking: false` not tested |
| C2: warmer sampling | Less rigid word choice, so the model loops less | ✅ temperature 0.6, top_p 0.95 |
| C3: prompt lines | "Write a script instead of working it out in your head" | ✅ In `terminal_bench.yaml` |
| C4: bigger budget on retry only | Normal turns stay small; a turn that overran retries once with more room | ❌ The opposite: 16K on every turn, which the research [advised against](full-89-0924-fix-priorities.md#not-recommended). Retries get no extra room, which likely explains the 72% repeat rate |
| C5: steer large outputs to scripts | Suggest searching big outputs with a script | 🟡 Outputs over 10K characters are cut with a "redirect to a file" hint. No special hex/disassembly handling |
| C6: context trimming | Same as Fix 2 | ❌ Not done |
| Fix 10: turn cap | 100 turns per task | Unchanged. 3 tasks still hit it: `fix-ocaml-gc`, `make-mips-interpreter`, `schemelike-metacircular-eval` |

## Bottom line and next steps

mini-swe-agent removed almost all the self-inflicted failures: no running scribbles, no copy-paste
loops, no silent no-command replies, no ungraded crashes. The model also starts overruns about 8×
less often. That's why passes rose **10 → 15 of 30** and total agent time fell about **28%**.

Next, in order:

1. **Trim the history (W2, Fix 2 / C6).** Tokens keep the leaderboard score negative.
2. **Change something on the retry after an overrun (C4).** More budget, or thinking turned off, so
   the agent doesn't give up after 5 misses.
3. **Check answers before submitting (W8, Fix 8/9).** Re-read the requirements, remove stray files.

Keep one on/off setting per change and A/B each one on this subset, as the
[plan](full-89-0924-fix-priorities.md#how-to-measure) says.

## Caveats

- **Not the approved model.** Both runs used `qwen3.8-27b`. Rates may differ on `Qwen3.6-27B-FP8`.
- **This subset is overrun-heavy by design.** 20 of the 30 were picked for having the most overruns,
  so baseline rates here (39% overrun turns) are worse than its 89-task average (21%).
- **One sample per task.** Trust changes that show up across many tasks, not single-task flips.
- **Timing is not like-for-like.** Many baseline times are the task's time limit (it ran until
  killed), and the shared endpoint's load varies between runs.
- **`fix-ocaml-gc` counts as a 0.** Its run was cancelled during grading.

## Method

Run on each trial's `result.json` plus the transcript: `agent/mini-swe-agent.trajectory.json` for
mini-swe-agent, `agent_result.metadata.messages` for the baseline.

- **Overrun (baseline):** an assistant reply of 10,000+ characters that doesn't start with a newline
  (the research's definition).
- **Overrun (mini-swe-agent):** a `FormatError` whose stored response has `finish_reason == "length"`.
  Logged, not inferred.
- **Stall:** a turn with no command (baseline: a reply with no code block and no `TASK_COMPLETE`;
  mini-swe-agent: a `FormatError` with any other finish reason).
- **Fresh / repeat:** whether the previous model call was an overrun.
- **Executed overrun text:** baseline overrun replies where `CODE_BLOCK_RE` matched.
- **Command timeout:** baseline observations containing `[command did not complete`; mini-swe-agent
  observations whose `exception_info` says the command was killed after the timeout.
- **Sleep poll:** an executed command starting with `sleep`. **Repeat command:** the same command as
  the previous one in that trial.
- **Tokens:** `n_input_tokens + n_output_tokens` from `result.json`.
- **Missing output file:** a failed, graded trial whose `verifier/test-stdout.txt` matches
  `does not exist|FileNotFoundError|No such file|not found at|Missing`. This is a rough proxy.
