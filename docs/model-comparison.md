# Choosing a local model — measured comparison

Which local LLM can drive these agents reliably? Three models that fit a 24 GB GPU were run
through the same tasks with the project's own agents, prompts, tools and output schemas; only
the model changed. The harness and the practice site are in [`eval/`](../eval/), so every
number here can be reproduced ([Re-running it](#re-running-it)).

**Measured 2026-10-22** on the current code, unless a section says otherwise. Raw results:
`eval/results/result_<model>.json`.

## TL;DR

| Model | Architecture | Tasks passed (of 25) |
|---|---|---|
| **Qwen3.8-27B** | dense | **25** |
| Qwen3.6-35B-A3B | MoE, ~3B active | 16 ¹ |
| Gemma-4-26B-A4B | MoE, ~4B active | 12 |

**Use Qwen3.8-27B.** It's the slowest per token but the only one that never broke the
Navigator, so it finished all 25 tasks in **281 s**, against 951 s and 1,016 s for the MoE
models. Those are 2.5–3× faster per token and good at reading and filling pages. As Navigators,
though, they:
- passed labels or whole snapshot lines instead of element refs
- clicked twice in one step
- reported clicks that never happened
- hung by writing their answer as text instead of the report

Gemma's failures are the model's, not the quantization's ([below](#is-it-the-quantization)).

¹ One of the 16 is a navigation where the click happened but the report said `no_action`. In
the real graph that report ends the tab, so 15 is the stricter reading.

---

## Setup

- **Hardware:** AMD Radeon 7900 XTX (24 GB).
- **Server:** llama.cpp `llama-server` build b9544, 4 slots, 131,072-token context.
- **Agents:** the project's Explorer, Navigator and Form Filler, unchanged: same prompts, same
  tools, same output schemas. The profile is `config/profile.example.yaml`.
- **Model settings:** temperature 0, thinking off (`chat_template_kwargs.enable_thinking=false`).
  Request timeout **90 s with no retries**, so a stuck request shows up as one hang instead of
  three silent retries.
- **Browser:** a throwaway headless Brave on port 9333 through Playwright MCP 0.0.83. No real
  application was touched.

| Model | File |
|---|---|
| Qwen3.8-27B | `Qwen3.8-27B-UD-Q5_K_M.gguf` |
| Qwen3.6-35B-A3B | `Qwen3.6-35B-A3B-UD-Q4_K_M.gguf` (35B total, about 3B active) |
| Gemma-4-26B-A4B | `gemma-4-26B-A4B-it-UD-Q5_K_XL.gguf` |

### The practice site

Three local pages (`eval/site/`), built to reproduce what broke on real postings:

| Page | What it has |
|---|---|
| `job.html` | A job posting with **Apply**, plus decoy buttons: Save Job, Share, Sign In, Search for Jobs |
| `apply.html` | The page shell loads at once; **the form appears 7 s later.** A real Workday form measured 6.9–7.0 s, and the model must not act on the empty shell. Five text fields, a custom **"Select One"** Country dropdown, a privacy-policy checkbox, and **Save and Continue**, behind a fixed footer |
| `done.html` | The next step, so a successful Save and Continue is visible |

### The five tasks (5 runs each)

| Task | Goal |
|---|---|
| Explore the job page | `page_type == job_description` |
| Navigate the job page | the page moved to `apply.html` with **exactly one** successful click |
| Explore the loaded form | `page_type == application_form` and `has_fields` |
| Fill the form | first/last name, e-mail, phone, city and Country = Canada all right; still on the form |
| Navigate the filled form | the page moved to `done.html` with exactly one successful click |

**A task passes only if** (`score()` in `eval/bakeoff.py`):
- **its goal was met, read from the page itself** with `browser_evaluate` (URL, field values),
  not taken from the model's report
- **the agent delivered its structured report**
- **no request hung** (errored, or took over 60 s)

A missing report or a hang crashes the node in the real graph, even when the page happened to
move.

**The privacy checkbox isn't scored.** Ticking it is the behaviour we want: you review the
form before submitting anyway. Its state is still recorded in each result's `detail`.

---

## Results

Pass / 5 and median seconds per task.

| Task | Qwen3.8-27B | Qwen3.6-35B-A3B | Gemma-4-26B-A4B |
|---|---|---|---|
| Explore job page | **5** · 8.0 s | **5** · 2.8 s | **5** · 3.4 s |
| Navigate job page | **5** · 6.3 s | 1 · 104.6 s | 2 · 14.3 s |
| Explore form | **5** · 6.0 s | **5** · 1.7 s | **5** · 1.9 s |
| Fill form | **5** · 25.5 s | **5** · 14.1 s | 0 · 101.7 s ² |
| Navigate filled form | **5** · 8.4 s | 0 · 97.2 s | 0 · 8.1 s |
| **Total** | **25/25** · 281 s | 16/25 · 1,016 s | 12/25 · 951 s |

² All 6 fields were right in every run; Gemma hung each time before returning its report.

Where the failures come from:

| | Qwen3.8-27B | Qwen3.6-35B-A3B | Gemma-4-26B-A4B |
|---|---|---|---|
| Valid element targets ³ | **50/50** | 72/87 | 46/83 |
| Reports delivered | **25/25** | 16/25 | 16/25 |
| Hung requests | **0** | 9 | 9 |
| Model calls | 82 | 153 | 132 |

³ Every `target` the agents passed to a tool (click, type, select, and each field of
`fill_form`), checked against the bare-ref pattern `^(f\d+)?e\d+$`.

### Speed

One uncached request (about 15,500 prompt tokens, 150 output tokens), measured with
llama.cpp's own `timings` (2026-10-16 to 2026-10-19):

| Model | Reading (prompt) | Writing (output) |
|---|---|---|
| Qwen3.8-27B | 792 tok/s | 27.2 tok/s |
| Qwen3.6-35B-A3B | 2,570 / 2,619 tok/s | 82.6 / 82.5 tok/s |
| Gemma-4-26B-A4B Q5_K_XL | 2,492 / 2,549 tok/s | 70.1 / 70.1 tok/s |

Fast tokens didn't make fast tasks. Each hang cost the full 90 s timeout, and a malformed click
costs a model call to recover from.

---

## What went wrong, per model

### Qwen3.6-35B-A3B

- **The Navigator hung in 9 of its 10 runs** (90 s each). It usually clicked the right button
  first, then never delivered its report. On the filled form the page reached `done.html` in
  5/5 runs, and the task still failed 5/5 for that reason.
- **Click targets were often a whole snapshot line:** `button "Apply" [ref=f20e16]`,
  `button "Save and Continue" [ref=f11e25]`. 15 of its 27 Navigator targets were malformed; in
  the filler, 60/60 were valid.
- **Clicked twice in one step** on the job page in 3 of 5 runs; once it went straight through
  to `done.html`.
- **Its one passing navigation reported `no_action`** after a click that worked ¹.
- **The fastest Explorer and filler of the three:** medians of 1.7–2.8 s and 14.1 s.

### Gemma-4-26B-A4B

- **Click targets are malformed most of the time:** 37 of 41 Navigator targets. It sent the
  label, `[ref="f5e25"]`, or the ref in brackets, where the tool needs the bare ref (`e25`):

  ```text
  Apply
  Save and Continue
  [ref="f5e25"]
  [ref=f14e25]
  ```

  Each fails at once:
  - `Error: "Apply" does not match any elements.`
  - `Unknown engine "ref" while parsing selector ref=e59`
- **It reports `clicked` when nothing was clicked.** In one run it made 16 bad clicks in a row
  on the job page, then reported `clicked Apply`; the page hadn't moved.
- **It hangs.** Instead of the required tool call it writes its answer as text, then repeats a
  token. The filler filled all 6 fields correctly in every run and hung each time before its
  report. Replaying a hung request on 2026-10-19 with a 1,500-token cap ended at
  `finish_reason=length` after 22.5 s:

  ```text
  ```json\n{\n  "action": "no_action",\n  "target": null\n}\n```\n\n**Reasoning:** I am on the "My Experience" step ...
  ... _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _
  ```

  Changing the temperature or adding a repetition penalty didn't fix it reliably. This matches
  reports of Gemma 4 malforming tool calls and looping in llama.cpp, vLLM and Ollama.
- **Strong where no clicking is involved:** the Explorer was correct in 10/10 runs, in 1.7–6.2 s.

### Qwen3.8-27B

- **50/50 valid targets, one click per navigation step, every report delivered, no hangs.**
- **2–4× slower per page** than the MoE models. On real Workday pages that's what pushed long
  forms past the per-tab cap, and the cure is fewer model calls, not a different model: on one
  "My Experience" page, cutting the filler from 96 LLM calls to 8 took it from 585 s to 126 s
  on this model, with all 42 checked fields still correct (an experiment, not in the shipped
  code).

### Is it the quantization?

No. On 2026-10-19 Gemma was run at Q4_K_XL and at Q5_K_XL, one step more precise, on the same
server build with the same tasks. Scored with today's rule:

| | Q4_K_XL | Q5_K_XL |
|---|---|---|
| Tasks passed | 11/25 | 11/25 |
| Valid targets | 32/68 (47%) | 32/124 (26%) |
| Hung requests | 8 | 9 |

The same malformed targets came back at both precisions. Twice the "Save and Continue" step made
28 bad clicks in a row until the agent hit its step limit (`GraphRecursionError: Recursion limit
of 60 reached`). Today's Q5_K_XL run, on the current prompts, scored 12/25. The failures come
from how this model calls tools.

### Does thinking mode help Gemma?

No, on the one live run made with it (2026-10-19, Gemma-4 Q5_K_XL, a real Workday application
from the job page, thinking on for all agents). Asked directly, the model wrote 559 characters
of reasoning and 152 output tokens, against 2 tokens with it off, so thinking really was on.

| | Thinking off (six runs on the same application, same day) | Thinking on (one run) |
|---|---|---|
| Malformed click targets | 7 of 8 clicks | 1 of 4 clicks (the label `Continue Application`) |
| Clicks in one navigator step | 1, after several failed tries | **3**: "Continue Application", then "Save and Continue" twice, the second on a page the filler hadn't filled yet |
| How it ended | `no_action` from a snapshot taken mid-load; the graph stopped after 27–50 s | a model request hung (2 × 120 s), and the graph crashed with `OpenAITimeoutError` after 305 s |

Thinking fixed most of the click targets, but it still clicked three times in one step, still
hung, and every step got slower. One run is not a statistic; the 5 × 5 test with thinking on
would settle it.

---

## Real postings: why the fast model looked broken

Before this comparison, Gemma-4 ran on a real Workday posting (2026-10-19). Its run kept ending
early, for two reasons the practice site then reproduced:

1. **Malformed click targets.** Seven of its eight clicks failed, while the Navigator still
   reported `clicked`. The graph went back to the Explorer on the same page, over and over.
2. **It looked at the page before the page had loaded.** After the click that finally worked,
   Gemma took its next snapshot **under a second later**. Workday's form needs about 7 s
   (measured three times: 6.9–7.0 s). The snapshot showed only the header bar, so the Navigator
   reported `no_action`, and that ends the graph. Qwen3.8's calls take about 5 s, which had
   always hidden this.

A fixed 2–3 s pause isn't enough on Workday. What worked was waiting for the page to settle
after each successful click: Playwright's "network idle", capped at 12 s, returned after about
9–10 s with the full form present.

## If you use a fast MoE model anyway

These code guards address its failures:
- **Normalize click targets:** strip `ref=` and brackets; resolve a label to its ref from the
  snapshot.
- **Decide "clicked" from the tool results**, not from the model's report.
- **Allow one click per navigator step.**
- **Wait for the page to settle after a click.**
- **Cap the output** (`max_tokens` around 1,024), so a loop ends in seconds instead of at the
  timeout.

Only the target fix and the wait were tried in a run. The other three come from the observed
failures and weren't measured.

## Limits of this comparison

- One practice site and 5 runs per task. It measures reliability on this project's agents, not
  general quality, and it doesn't cover the Answer Writer.
- Different quantizations: Q5_K_M for Qwen3.8, Q4_K_M for Qwen3.6, Q5_K_XL for Gemma.
- Temperature 0 and thinking off for all three. Some models are documented with other sampling
  settings.
- Not tested: GLM-4.7-Flash, Mistral Small 3.2 24B.

## Re-running it

The harness runs against whichever model `llama-server` has loaded: it reads the model id from
`/v1/models` once, at the start, so don't swap models mid-run. It refuses to run unless
`BROWSER_URL=http://localhost:9333`, so your own browser is never used.

```bash
python -m http.server 8765 --directory eval/site &
brave-browser --headless=new --remote-debugging-port=9333 --user-data-dir=/tmp/bakeoff-profile &
PROFILE_FILE=profile.example.yaml LANGSMITH_TRACING=false BROWSER_URL=http://localhost:9333 \
  python eval/bakeoff.py 5 <label>
```

It prints one line per task and run, then a summary, and writes
`eval/results/result_<label>.json`. One model takes 5–20 minutes.
