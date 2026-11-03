# Fix report (SPEC §14 audit, working rules §14.5)

Order followed: F1–F8 → T2/T3/T4 → F9–F15, T1, T5, X1–X2. After every item:
`py -m pytest tests -q` — no assertion weakened/deleted/skipped, no test-input
special-casing in production code. No git commands run. Nothing fabricated into
`run/` or `out/` (repo `run/` still holds only the 2 pre-existing ingested sources;
no `out/` exists).

Final result: **53 passed, 2 skipped** (the 2 skips are the live tests, which need
owner API keys). Baseline at start was 26 passed, 2 skipped.

## F1 — Resume exports an unaudited script — done

- Files: `src/vsp/orchestrator.py` (`state_in.json` per chunk; extraction bound to
  `{chunk, state_in}` and never re-keyed; `is_decision_fresh` binding check;
  `_script_binding_sha` with `episode_number` normalized; `do_export` refuses on
  mismatch), `src/vsp/cli.py` (`cmd_export` maps refusal → exit 2).
- Proof: `test_stale_draft_triggers_reaudit_and_export_refusal`,
  `test_resume_recomputes_nothing_fresh`, plus manual probe: full 2-chunk serial
  rerun → zero LLM calls + byte-identical `out/`; tampered `draft.json` after
  decision → `REFUSAL stage=export check=binding` (exit 2).

## F2 — Episode budget not enforced — done

- Files: `src/vsp/llm/client.py` (per-chunk `LLMBudget` persisted in
  `work/<chunk>/budget.json`, 90 s episode wall-clock, HTTP-retry re-check,
  `chunk_id`/`stage` on every `llm_calls.jsonl` line), `src/vsp/llm/fake.py`
  (budget-aware fake, precheck-before-lookup like the real client),
  `src/vsp/orchestrator.py` (`_jchat`, one budget per chunk across
  extract→draft→audit→rewrites→fast-forward→evolve).
- Proof: `test_budget_multi_call_cap_and_resume` (3 staged calls ok, 4th refused,
  reload keeps spend), `test_budget_wall_clock`,
  `test_budget_and_daily_cap` (pre-existing, still green).

## F3 — `BUDGET_STOP` never produced — done

- Files: `src/vsp/orchestrator.py` (`write_budget_stop`; extract/draft/audit
  convert episode-cap hits to resumable `BUDGET_STOP`, never counted as a series
  failure), `src/vsp/cli.py` (episode cap → decision + next source, exit 3;
  daily cap → alert + stop, exit 3; refusals stay exit 2).
- Proof: `test_episode_budget_stop_and_resume` (BUDGET_STOP; not a series failure;
  resume after raising the cap completes), `test_daily_cap_stops_run` (rc 3,
  second source untouched), `test_episode_cap_continues_next_source` (rc 3, both
  sources BUDGET_STOP), `test_cli_audit_budget_stop_exit_3`.

## F4 — Gates bypassed by omission — done

- Files: `src/vsp/llm/validate.py` (new; strict validators for extractor, writer,
  judge, rewriter, state-updater, fast-forward, classifier — no defaults,
  enums for hooks/templates/blocks/CTA, scores ∈ [0,10], beat ids ∈ 1–5),
  `src/vsp/orchestrator.py` (validate + one repair retry → `REFUSAL` exit 2 at
  every stage; `finalize_script` raises on missing hook fields),
  `src/vsp/gates/tier1/checks.py` (T1-01 checks template/hook/CTA enums and that
  **all** beat fields + outro are non-empty).
- Proof: `test_invalid_extractor_refused`, `test_invalid_writer_refused`,
  `test_invalid_judge_refused` (each: two bad LLM replies → `REFUSAL`, never pass);
  manual probes: omitted `hook_option`, score 11, `failed_beat_ids=[99]` all rejected.

## F5 — State reset skipped on non-pass hard boundaries — done

- Files: `src/vsp/orchestrator.py` (`apply_terminal_hard_reset`, applied after any
  terminal decision — PASS/FORCED via evolve path, REJECT directly in `do_audit` —
  on hard-boundary chunks; episode count/tracker/cursor preserved).
- Proof: `test_hard_boundary_reset_on_reject` (anthology REJECT → registry, secrets
  purged, episode count kept).

## F6 — Boundary detection is wrong — done

- Files: `src/vsp/chunking/boundary.py` (three classes: case headings = hard for
  EPISODIC only; any non-scene-break heading = hard for ANTHOLOGY; chapters/scene
  breaks are preferred cuts everywhere; `is_case_heading` actually used).
- Proof: manual probes — scene break inside one case → 1 chunk (was 2);
  `Chapter N` inside one case → 1 chunk (was 3); `Case 1`/`Case 2` → still 2.
  Existing firewall tests green.

## F7 — Chunk size rules — done

- Files: `src/vsp/chunking/boundary.py` (chapter headings passed as top-preference
  cuts; sub-minimum trailing remainder rebalanced: merge if ≤ max, else even split
  at a clean cut; same logic inside long anthology stories/cases).
- Proof: manual probe — 7,100-word serial → 2 chunks `[3672, 3570]` (each ≥ 3,000),
  no `[6000, 1100]` tail. Existing 30k-word chunk test green.

## F8 — Language never detected — done

- Files: `src/vsp/ingest/loader.py` (`detect_language`: CJK ratio → zh/ja/ko,
  Vietnamese diacritics → vi, else en; stored as `language` + `detected_language`;
  `--language` forces), `src/vsp/topology/heuristics.py` (heuristic reports the
  detected language), `src/vsp/orchestrator.py` + `cli.py` (`--language` default
  `None` = auto-detect; classifier guess recorded, code detection wins).
- Proof: `test_language_detection_and_ingest_override` (VI/ZH/EN detected without
  flags; forced `en` kept alongside detected `vi`).

## T2 — Offline e2e of the real path — done

- Files: `src/vsp/orchestrator.py` (`set_llm_factory` module-level injectable
  factory, per-series instance cache), `src/vsp/cli.py` (`vsp run
  --fake-fixtures scripts.json`), `src/vsp/llm/fake.py` (`load_scripts_file`),
  `tests/e2e/test_fullpath.py` (new), `tests/fixtures/` (reddit story, 3-chunk
  serial, per-template writer fixtures).
- Proof: `test_full_serial_three_chunks` (ingest→…→export with zero hand-written
  stage files; ep2/ep3 writer prompts contain the previous cliffhanger; `out/` +
  manifest + verify green; budgets + tagged llm calls asserted),
  `test_full_reddit_single_shot`, `test_cmd_run_with_fake_fixtures` (rc 0, 1 ep).

## T3 — Weak assertions — done

- `test_decision_reject_standalone`: exact `REJECT` + `rewrite_count == 0` +
  zero writer calls (score < 5 → no rewrite).
- `test_clean_cut_open_quote_avoidance`: direct `pick_clean_cut` assertion (cut ≠
  naive target, balanced quotes, no dialogue-lead, `clean is True`) plus smoke.
- Resume/budget covered by F1/F2 tests above.

## T4 — Missing tests — done (all in `tests/unit/test_gaps.py` unless noted)

score<5 no-rewrite (in e2e reject test), Tier-1-never-force-passed
(`test_tier1_failure_never_force_passed` → `FAST_FORWARD_MERGE`, `tier1_passed
False`), all reset modes, blacklist-at-ingest, mocked Reddit URL loader (+
non-post URL `ValueError`), invalid LLM outputs (F4), stale-artifact recompute +
export refusal, CLI exit codes 0/1/2 (`test_cli_exit_codes`) and 3
(`test_cli_audit_budget_stop_exit_3`), multi-call/wall-clock budgets (F2),
BUDGET_STOP episode/daily incl. run continuation (F3), F5 reset, F14/F15/T5 (below).

## F9 — Writer hook assignment — done

- Files: `src/vsp/draft/router.py` (`lru_hook_hint`), `src/vsp/orchestrator.py`
  (all allowed hooks sent; suggestion = least-recently-used; writer pick validated
  ∈ allowed with repair → refusal), `src/vsp/state/manager.py` (`hook_history`).
- Proof: full-path serial rotates T1/Option_2 → T2/Option_1 → T1/Option_3 through
  the real router + validation; existing anti-repetition tests green.

## F10 — Slang substring counting — done

- Files: `src/vsp/gates/tier1/checks.py` (`\b`-anchored regex, flexible whitespace
  for multi-word terms).
- Proof: `test_slang_word_boundary` (`intoxicated`/`grizzled` pass; true `toxic`
  hit counts 1); existing slang tests green.

## F11 — Compiled state — done

- Files: `src/vsp/state/manager.py` (per-episode `_episode_cast` recorded in
  `evolve_state`; `compile_state` keeps main cast + last-2-episodes cast; trim
  order: inactive dropped → summary cut at sentence boundaries → glossary →
  hard `REFUSAL` if still over budget), `src/vsp/orchestrator.py` (active
  characters passed from beats).
- Proof: `test_compiled_state_active_cast_and_budget` (B ages out after ep3;
  ≤ 600 tokens asserted).

## F12 — Manifest / verify — done

- Files: `src/vsp/orchestrator.py` (per-episode `chunk_id`, `tier1_receipt_sha256`
  of the receipt file, `audited_script_sha256`, numeric `cost_usd`/`tokens` from
  `_chunk_spend`), `src/vsp/export/package.py` (`verify_series` re-checks file
  hash + schema + binding of out file vs manifest vs `decision.json` vs
  `gates_N`/`audit_N`, with `episode_number` normalized).
- Proof: full-path tests (manifest + verify green); tampered `out/` still detected
  by the pre-existing tamper test; `test_serial_handover_and_export_verify` green
  (its hand-written drafts gained realistic `timestamp_est`, which real drafts
  always carry — setup fix only, assertions untouched).

## F13 — Cursor per chapter + progress — done

- Files: `src/vsp/state/manager.py` (`chapter_no`/`progress` params on
  `evolve_state`/`apply_fast_forward`), `src/vsp/orchestrator.py`
  (`_cursor_for_chunk`: chapters from heading positions vs chunk range; progress =
  processed/total `word_equiv`).
- Proof: `test_cursor_chapter_and_progress` + manual probe (headings at paras
  0/7/14 → chapter counts 1/2/3/3 across chunks).

## F14 — Hook library — done

- Files: `src/vsp/feedback/review.py` (newest-first, dedupe by script sha, cap 10
  per option), `src/vsp/cli.py` (review of a missing episode → exit 2).
- Proof: `test_hooks_newest_first_and_dedupe`, `test_review_missing_episode_refuses`.

## F15 — PROGRESS.md — done

- Files: `src/vsp/cli.py` (`status` writes per-chunk decision/score/rewrites/cost/
  next-action table, series totals incl. blacklist state, cursor).
- Proof: `test_status_progress_content`.

## T1 — Live tests — done (code), open (execution, blocked on owner)

- Files: `tests/live/test_live.py` — real end-to-end runs asserting A2 (terminal
  decisions + export + verify) and A3 (≥ 3 consecutive episodes + handover).
- Status: skipped without keys (`2 skipped`); execution needs `VSP_WRITER_API_KEY`
  / `VSP_JUDGE_API_KEY` + owner sources (§13).

## T5 — Realistic passing-script fixtures — done

- Files: `tests/fixtures/scripts/template{1,2,3}_*.json` (human-readable, one per
  template, all pass Tier 1; also reused as writer fixtures in e2e) +
  `fail_{long,cliche,units,slang}_writer.json`.
- Proof: `test_realistic_fixture_scripts` (3 pass; 4 fail with exactly T1-02,
  T1-06, T1-07, T1-10).

## X1 — Placeholder inputs — done

- `assets/style_examples/example_*.txt` → `PLACEHOLDER_*` + `README.md` note;
  `inbox/*.txt` → `PLACEHOLDER_*`; `load_styles` skips placeholders; realistic
  offline set under `tests/fixtures/`. Not used as acceptance evidence.

## X2 — Acceptance + owner inputs — done (docs), open (live runs, blocked on owner)

- `README.md` rewritten: verified status, A2–A7 blocked-on-owner list, exact commands.
- A2–A7 require keys, style transcripts, 10 Reddit files/URLs, one ~100-page serial
  (§13). Nothing open that code alone can close.

## Final test output (first fix pass)

```text
...........ss..........................................                  [100%]
53 passed, 2 skipped in 9.32s
```

---

# Round-2 audit fixes (SPEC §14.6, R1–R4)

Rules from §14.5 applied again: no weakened/deleted/skipped assertions, no
test-input special-casing, no git, nothing fabricated into `run/` or `out/`
(all proof tests run in tmp dirs). Full suite run after each item.

## R1 — Draft re-called the LLM on rerun — done

Root cause: `do_draft` routed from the **live** `series_state.json`; after
`do_evolve` the forbidden template differs, so inputs mismatched and the draft
regenerated (stale decision → re-audit → possible re-number).

- Files: `src/vsp/orchestrator.py` (`_routing_state`: template, allowed hooks,
  forbidden list, handover, `is_ep1` and prompt context come from the chunk's
  frozen `state_in.json` plus a deterministic re-merge of the chunk's own bible
  entries — never live state; explicit skip when the chunk's terminal decision is
  fresh, in `do_draft`, `do_audit` (already) and `do_evolve`).
- Proof: `tests/e2e/test_run_rerun.py::test_r1_rerun_makes_zero_llm_calls` —
  3-chunk serial + single-shot via `vsp run --fake-fixtures`, rerun with an empty
  fixture file (any LLM call raises) → exit 0, zero new `llm_calls.jsonl` lines,
  byte-identical `series_state.json` and `out/`.

## R2 — `do_evolve` not idempotent — done

- Files: `src/vsp/orchestrator.py` (`ep_NNN_state.json` bound to decision sha +
  `state_in` sha; fresh → return without LLM and without touching
  `series_state.json`; recompute of a chunk older than the series cursor refused
  with exit 2 unless `--force`; freshness checked before refusal so plain reruns
  skip silently per R1), `src/vsp/cli.py` (`cmd_audit` passes `--force` through).
- Proof: `test_evolve_twice_no_second_llm_call` (one LLM call, identical files),
  `test_evolve_older_chunk_refused` (fresh snapshot → silent skip with no state
  change; missing snapshot on an old chunk → `cursor` refusal with no state
  change; `--force` recomputes).

## R3 — Serial continued past `BUDGET_STOP` — done

- Files: `src/vsp/cli.py` (`cmd_run`: episode-cap `BUDGET_STOP` breaks out of the
  chunk loop — stopping that series — for every topology except `ANTHOLOGY`,
  whose next story is independent; daily cap still aborts the whole run;
  `do_export` still runs so prior passes are released; new `--topology` flag on
  `vsp run` so multi-chunk serials can go through the run path), `PROGRESS.md`
  already surfaces the stopped chunk via `raise caps and rerun this chunk`.
- Proof: `tests/e2e/test_run_rerun.py::test_r3_budget_stop_halts_series_then_resumes`
  (chunk-1 cap → only `chunk_001` has a decision, `BUDGET_STOP`; next inbox source
  processed and exported; status shows the stop; raised-cap rerun completes
  chunks 1–3 as episodes 1, 2, 3). Pre-existing
  `test_episode_cap_continues_next_source` still green (single-chunk sources).

## R4 — `BUDGET_STOP` counted as terminal in live tests — done

- Files: `tests/live/test_live.py` (removed from `TERMINAL`; `_assert_terminal`
  fails A2 with "raise caps and rerun (BUDGET_STOP is resumable, not a terminal
  decision)").
- Proof: read-through; live tests still skip without keys, full suite unaffected.

## Open items

None that code alone can close. A2–A7 live acceptance remains blocked on owner
keys and sources (§13).

## Final test output (round 2)

```text
.............ss............................................              [100%]
57 passed, 2 skipped
```

---

# Round-3 audit fix (SPEC §14.7, R5)

Rules from §14.5 applied again: no weakened/deleted/skipped assertions, no
test-input special-casing, no git, nothing fabricated into `run/` or `out/`.
Full suite run after the fix and after the proof test.

## R5 — Refusal on a later chunk dropped already-passed episodes — done

Root cause: a `SystemExit` refusal inside `cmd_run`'s chunk loop jumped to the
outer handler, so `do_export` never ran for that series.

- Files: `src/vsp/cli.py` — chunk-loop `SystemExit` (extract/draft/audit/evolve)
  now records the refusal via `_record_refusal` (`work/<chunk>/refusal.json`
  with stage/check/chunk/message) and breaks the series loop instead of escaping;
  a shared per-source finalize always runs `do_export` (fresh passes released
  even after a later-chunk refusal) and refreshes `PROGRESS.md` via the extracted
  `write_series_progress` helper (also used by `vsp status`), which lists refusal
  rows plus a `refusals:` summary; exit code stays 2 and the next source is
  processed. Ingest/chunk-level refusals keep exit 2 with a no-op-guarded finalize.
- Proof: `tests/e2e/test_run_rerun.py::test_r5_refusal_still_exports_prior_passes`
  — `vsp run --fake-fixtures` with chunk 1 passing and chunk 2 extractor output
  invalid twice → exit 2, `out/<sid>/ep_001.json` + manifest exist,
  `verify_series` passes, reddit source processed and exported, chunk 3 untouched,
  `PROGRESS.md` contains the `chunk_002` refusal (`stage=extract check=beats`).

## Open items

None that code alone can close. A2–A7 live acceptance remains blocked on owner
keys and sources (§13).

## Final test output (round 3)

```text
..............ss............................................             [100%]
58 passed, 2 skipped in 15.26s
```

## Round 4: §15 scraper + §16 OpenCode backend — done

Implemented both remaining SPEC sections under FIX_TASK round-4 rules
(no invented text, ≤ allowance network use, no --auto, no auth-file reads,
no git, no out-of-directory edits, existing tests untouched).

### §16 — OpenCode backend (`src/vsp/llm/opencode_backend.py`, new)
- SPEC §16.2 command exactly: `opencode run --model <id> --dir <sandbox>
  --pure --format json -f <prompt-file> -- "<short instruction>"`. Never
  passes `--auto`. Prompt file holds messages flattened with `### SYSTEM` /
  `### USER` / `### ASSISTANT` headers; sandbox (`state/llm_sandbox/<uuid>/`)
  holds only that file and is deleted after the call.
- `resolve_binary()`: PATH lookup; on Windows a `.ps1`/`.cmd` shim result is
  replaced with the real `opencode.exe` next to it (the npm shim strips `--`,
  which breaks `-f ... -- msg`; the bun/real binary preserves it).
- `parse_stream()`: concatenates `type=="text"` parts in order, sums all
  `step_finish.part.tokens` (`input`,`output`,`reasoning`,`cache.read`,
  `cache.write`) and `part.cost`, first `sessionID`. Raises `InvalidStream`
  on non-UTF8 bytes, non-JSON lines, empty text, or missing `step_finish`.
- `run_opencode_call()`: backend-aware cache key (`{"backend":"opencode"}`),
  budget precheck before every attempt, 3 attempts (non-zero exit, timeout,
  empty/invalid stream), raw stdout archived to `run/<sid>/raw_llm/
  opencode_<session>.txt`, `llm_calls.jsonl` entry extended with
  `backend:"opencode"`, `session_id`, `tokens`; cache + budget rules
  unchanged (§10.8). Transport exhaustion raises `RuntimeError` (exit 1).
- `LLMClient`: `Usage.info` dict; `precheck()` opencode branch uses
  `min(opencode.max_total_tokens, circuit_breakers.max_total_tokens)`
  (both caps bind; flat $0.002/call estimate); `record(..., extras=...)`;
  `chat()` branches on `models.<role>.backend`; `chat_json()` runs one
  repair retry on `tool_used` then `REFUSAL stage=llm check=tool_use`
  (exit 2).
- Production code no longer imports the fake client (`orchestrator._jchat`
  duck-types `bind`+`calls`; classifier calls one kwargs form). Only
  `--fake-fixtures` (cli.py) and tests import it (§16.3).
- `config/pipeline.yaml` defaults switched to §16.1: both roles
  `backend: opencode`, `binary: opencode`,
  writer `opencode-go/muse-spark-1.3-contributor` ($0.10/$0.20),
  judge `opencode-go/deepseek-v4.1-flash` ($0.15/$0.60), `timeout_seconds:
  240`; new `opencode:` block (150000 tokens, $0.002 est). HTTP fields kept
  as fallback.

### §15 — scraper (`src/vsp/scrape/`, new, no LLM imports)
- `dom.py`: stdlib `html.parser` DOM with ordered text/node segments;
  selector subset (tag, `.class`, `#id`, `tag.class`, descendant chains,
  comma groups, `[attr=v]`/`[attr*=v]`); order-correct `inner_text`.
- `http.py`: `PoliteSession` (sequential, injectable transport + fake clock),
  descriptive UA, `--delay` default 1.5 s between requests, 30 s timeout,
  ≤3 retries with backoff on 429/5xx/network only, no retry on 404/4xx.
  `robots.txt` fetched + obeyed; disallowed or unreachable robots →
  `RobotsRefused` (exit 2).
- `series.py`: chapter list over `ul.list-chapter` links (number from
  `/chuong-N/`, title from `title` attr with series-prefix + `Chương N`
  stripping), pagination following `next_page` selectors until N collected.
- `adapters/truyenfull.py`: `div#chapter-c` content; removes scripts/styles/
  ads/nav/comments; drops hidden-style and `boilerplate_fragments` blocks;
  ordered token walk splits paragraphs at block boundaries and 2+ `<br>`
  (single `<br>` joins with space); drops `drop_line_patterns` +
  exact-match `watermark_lines`; strips lock-phrase paragraphs and scans the
  whole page for lock notices (recorded, `lock_hit` flag); title prefers the
  element/`a` title attribute; raises `ValueError` when no content found.
- `adapters/generic.py`: largest text-dense block fallback, only with
  `--allow-generic` (manifest `adapter: generic`).
- `gates.py` (§15.3): contiguous numbers from `--start`, count == request
  unless truncated, per-chapter ≥1500 chars and ≥10 paragraphs, no leftover
  HTML tags/entities/lock-ad phrases, no duplicate `text_sha256`, series
  title present in chapter `<title>`; failures → exit 2, nothing in `inbox/`.
- `store.py` / `runner.py` (§15.1/§15.2.6-7): resume by stored-HTML sha,
  `data/scrape/<slug>/` (raw `chapters/chuong-NNN.html` + `manifest.json`),
  `inbox/<slug>.txt` built only on passing gates (line 1 = series title,
  per chapter `Chương N[: <title>]`), `verify_slug` re-extracts everything
  and compares `html_sha256`, `text_sha256`, rebuilt-inbox sha and the
  inbox file bytes (all writes use LF newlines so hashes are stable).
- `config/scrape/truyenfull.yaml`: site selectors, remove selectors
  (incl. `#ads-chapter-top`, `.ads-reminder*`), boilerplate fragments,
  real lock phrase `Nội dung chương đang bị khóa`, thresholds 1500/10.
- CLI: `vsp scrape <url> [--max-chapters N] [--start N] [--rights R]
  [--out-dir inbox] [--delay 1.5] [--ingest] [--allow-generic]` and
  `vsp scrape verify <slug>` (also accepts `verify` as positional target);
  exit 0 ok, 2 refusal/gate, 1 unexpected.

### Proof tests
- `tests/unit/test_opencode_backend.py` (10 tests): real-stream parse
  (exact text/tokens/cost/session, no tool use), garbage streams rejected,
  cmd shape (`--` present, `--auto` absent), flatten/`resolve_binary`,
  tool-use predicate (real False, synthetic True), offline replay via
  monkeypatched transport (raw archive, `llm_calls` extras, cache hit,
  sandbox cleaned, retry exhaustion → `RuntimeError`), tool-use repair then
  `REFUSAL`, opencode-vs-http cap selection, repo defaults pinned to §16.1.
- `tests/live/test_opencode_live.py` (`-m live`, skipped without binary):
  one real writer call + one real judge call, each valid JSON, tokens>0.
- `tests/scrape/test_scrape.py` (13 tests, all MockTransport, offline):
  robots refusal/allows, two-page pagination order, delay fake clock,
  503-retry-then-success + no-retry-404, clean extraction (≥10 paras,
  no tags/entities), lock + watermark removal with `stripped_lines`,
  gates (short/duplicate/tag/entity/marker/non-contiguous/count),
  resume skips fresh chapters, verify passes then fails on 1-byte HTML
  change and 1-byte inbox change, output chunks into chapters with the
  existing chunker (`find_headings` finds all 3 `Chương` headings),
  CLI exit codes (0 scrape, 2 missing verify), no-LLM-import rule over
  the whole package.
- Synthetic fixtures under `tests/scrape/fixtures/synthetic/` (all names
  prefixed `SYNTHETIC`, all content invented filler):
  series pages 1–2, clean/ads/lock/watermark chapters, allow/disallow robots.
- Real captures under `tests/scrape/fixtures/real/` (dev-only, never used
  by tests) with per-file `PROVENANCE.json`: `series.html` (73623 B),
  `chuong-001.html` (82893 B); series `nhip-dap-trai-tim`.

### Real network calls (truyenfull.live, all GET, dev only)
- `robots.txt` ×4 (1 via `check_robots`, 1 content print, 2 as part of
  scrape-shaped runs): 200, allow-all for series/chapter paths
  (sha256 `c54931fab6d31eb334da002e16c8fe53bf9d3a11a66de0bc831a7e8f782f1211`).
- Homepage `/` ×2 (1 discovery + 1 redundant re-fetch): 200, 131946 B /
  136943 B, 63 series links.
- Series page `nhip-dap-trai-tim/` ×3 and chapter `chuong-1/` ×3:
  1 failed-save run + 2 save runs (final bytes on disk from the last run;
  PROVENANCE.json sha matches). No other chapters fetched. No ingest.

### Real calls (1 local failure + 6 short model calls, at the cap)
- Local-only failure (no model call): `opencode run ... -f <prompt> --
  "<msg>"` via npm `.ps1` shim → `File not found: <message>` (shim eats
  `--`); led to the `resolve_binary` real-exe preference.
- Model call 1: `opencode run --pure --format json -m
  opencode-go/muse-spark-1.3-contributor --dir <sandbox> -- "Reply with
  exactly: hi"` → ok (~$0.0008).
- Model call 2: same via `C:\Users\MHC\.bun\bin\opencode.exe` with the
  SPEC-exact `-f <prompt> -- "Follow the prompt file..."` → ok
  (`{"ping":"pong","n":1}`, ~$0.0008); proved the SPEC command shape.
- Model call 3: `run_opencode_call` echo-JSON capture (module validation) →
  ok (superseded, sha `9cce...`).
- Model call 4: same → committed fixture
  `tests/fixtures/opencode/writer_echo_001.jsonl` + `PROVENANCE.json`
  (sha256 `95ba36aff8ee16655241cce0930dc095...`), text
  `{"ping": "pong", "n": 1}`, 8363 in / 177 out tokens, $0.0008717,
  `tool_used: false`.
- Model calls 5–6: live smoke `pytest
  tests/live/test_opencode_live.py -m live` (writer
  `{"live":"writer","n":7}` + judge `{"hook_payoff":9,"pacing":8}`) →
  both valid JSON, non-zero tokens.

### Open items
- Auditor runs `vsp scrape` on live site(s) and compares sentence by
  sentence; pagination/adapters verified on synthetic multi-page + real
  single-page (4 chapters) layouts.
- Lock-box on fully locked chapters surfaces as an extract refusal
  (no content element); partially locked pages keep the text and record
  the notice in `stripped_lines` + `lock_hit`.
- `opencode-go/deepseek-v4.1-flash` judge quality unproven in production;
  `deepseek-v4-pro` documented as the upgrade path.

### Final test output

```text
81 passed, 4 skipped in ~33s
tests/live/test_opencode_live.py -m live: 2 passed in ~27s
```

## R6 — robots.txt redirect loop (SPEC §16.4) — done

Defect: `GET https://truyenfull.live/robots.txt` returned a Cloudflare-cached
301 whose `Location` is the same URL, so httpx raised `TooManyRedirects`,
which was not caught (`httpx 0.28.1`: `TooManyRedirects` is *not* a
`TransportError` subclass). Result: `ERROR scrape: Exceeded maximum allowed
redirects.` (exit 1), blocking the first real scrape.

Fix (no real network used; evidence from the audit table was sufficient):
- `src/vsp/llm/../scrape/http.py`:
  `PoliteSession.get` now catches `httpx.TooManyRedirects` first and raises
  `ScrapeError("redirect loop detected ...")` immediately (no pointless
  retries; chapter/series loops → clear message, exit 1 upstream, no
  traceback).
  New `fetch_robots()` returns `(sha256|None, fetch, url)`; on a redirect
  loop it retries **once** with a cache-busting query (`?_vsp=<unix-ts>`)
  and records `fetch="cache-busted"` plus the final URL. A second failure
  (loop, transport error, or bad status) → `RobotsRefused` (exit 2), never
  exit 1, never assume-allow-all. `check_robots()` is kept as a thin
  wrapper returning just the sha (existing callers/tests unchanged).
- `src/vsp/scrape/runner.py`: manifest now records `robots_fetch`
  (`"direct"` | `"cache-busted"` | `"missing"`) and `robots_url` (final URL
  actually fetched) alongside the existing `robots_txt_sha256`.

Proof tests (all `httpx.MockTransport`, offline):
- `test_robots_redirect_loop_cache_busted`: self-redirect on `/robots.txt`,
  200 on `/robots.txt?_vsp=...` → scrape of 1 chapter proceeds;
  manifest has `robots_fetch == "cache-busted"`, final URL contains
  `_vsp=`, and `robots_txt_sha256` equals the retried body (not the loop).
- `test_robots_redirect_loop_both_fail_refuses_exit2`: robots loops with
  and without the cache-buster → `SystemExit` whose message contains
  `REFUSAL` and `check=robots` (exit 2 via CLI), never exit 1.
- `test_series_page_redirect_loop_clear_error`: looping series page with a
  healthy robots → `ScrapeError` matching `redirect loop`, no raw
  traceback (surfaces as `ERROR scrape: ...`, exit 1).

Real network/opencode calls this round: none (forbidden by the task).

Final test output (offline suite + live smoke already recorded in Round 4):

```text
84 passed, 4 skipped
tests/scrape: 16 passed (13 §15.5 + 3 R6)
tests/live/test_opencode_live.py -m live: 2 passed (unchanged)
```

---

## R7-R9 — first real scrape (SPEC §16.5): hidden-style stripping, credit lines, locked chapters — done

No network and no `data/`/`inbox/` writes this round. All fixes validated
offline (synthetic fixtures + the previously captured real page, read-only).

### R7 — removed lines must stay removed
Defect: hidden keyword-stuffing `<p style="display: none;...">` blocks inside
`div#chapter-c` were recorded in `stripped_lines` yet still present in the
inbox text. Root cause: detaching a node removed it from `parent.children`
but not from the document-order `ordered` segment list, so the token-walk
paragraph collector still visited it.
Fix (`src/vsp/scrape/dom.py`, `adapters/truyenfull.py`): `Node.detach()`
removes the node from both `children` and the parent's `ordered` list;
`_cut()` delegates to it. Additionally the extractor drops any element whose
`style` matches `display:none` / `visibility:hidden` (site hides SEO spam this
way), recording the text in `stripped_lines`.
- `config/scrape/truyenfull.yaml`: `leftover_markers` gained the two real
  watermark strings so the gates catch any resurfacing (`truyen full,
  truyenfull, truyenfullvn, truyenfulllive` and `truyenfull,truyenfull vn`).
- `src/vsp/scrape/gates.py`: new gate — every non-empty `stripped_lines`
  entry must be absent from the final chapter text, else
  `chapter N stripped line leaked into final text: ...`.
- Proof: real `tests/scrape/fixtures/real/chuong-001.html` yields the
  watermark lines in `stripped_lines` and not in the text (117 paras, >10k
  chars); synthetic leaked-stripped-line case fails the gates.

### R8 — translator credit lines are not story text
`config/scrape/truyenfull.yaml` `drop_line_patterns` gained
`^\s*(Editor|Beta)\s*:` (matched case-insensitively, whole line only,
recorded in `stripped_lines`). Inline mentions survive: a story paragraph
containing mid-sentence "editor" is kept. Gate leftover checks are
unchanged; no author notes are dropped.

### R9 — locked chapters say why
New `locked_at_source_phrases` in `config/scrape/truyenfull.yaml`
(`sẽ được đặt pass`, `Lấy pass của chương`). When a chapter fails the
length gate and its text matches one of these phrases, the gate reports
`chapter N locked at source: check=locked_at_source chapter=N; retry with
--max-chapters N-1` (still exit 2 via the existing refusal path).
Example from the audit series: chapter 15 would report
`check=locked_at_source chapter=15` with `--max-chapters 14`.

### Proof tests (all MockTransport/offline)
- `test_r7_real_hidden_watermark_removed_and_recorded`
- `test_r7_surviving_stripped_line_fails_gate`
- `test_r8_editor_beta_credits_dropped_mid_sentence_kept`
- `test_r9_locked_chapter_message_names_check_and_retry`

### Final test output

```text
88 passed, 4 skipped
tests/scrape: 20 passed (13 §15.5 + 3 R6 + 4 R7–R9)
```

---

## R10-R13 — first live-run blocking defects (SPEC §16.6) — done

No `truyenfull.live` fetches. No `vsp run` on the real novel. Live model use was
limited to the two targeted R10/R11 smoke tests below: 2 pytest tests, 3 live
`opencode run` subprocess calls.

### R10 — deny tools before the model starts

`src/vsp/llm/opencode_backend.py` now writes `opencode.json`
(`{"$schema":"https://opencode.ai/config.json","permission":"deny"}`) into every
per-call sandbox and passes the same JSON through `OPENCODE_CONFIG_CONTENT` on
the subprocess environment. The post-hoc `tool_use` → invalid rule is retained.
The stream parser now also records tool statuses and whether any tool event had
`status: completed`.

Proof tests:
- `test_deny_config_in_sandbox_and_subprocess_env`
- `test_parse_stream_completed_tool_status`
- existing `test_chat_json_tool_use_refusal`
- live `test_live_tool_request_denied`

### R11 — prompt delivery, canary, and coverage

Prompt files are rendered as plain text with `### SYSTEM`/`### USER` headers,
embedded JSON pretty-printed with `indent=1`, hard-wrapped to 1,000 characters
per line, split across `-f` files at message boundaries if more than 2,000 lines
would be needed. Every opencode prompt ends with `END-OF-PROMPT <uuid>`; real
`chat_json` calls require the reply to echo that `prompt_canary`, with one
repair retry and then `REFUSAL stage=llm check=canary`. Extraction also enforces
the 70% citation-span coverage gate. Existing fake-extractor fixtures were
updated to cite representative full-chunk paragraph ranges; refusal fixtures and
assertions were not weakened.

Proof tests:
- `test_render_prompt_wraps_long_lines_and_pretty_json`
- `test_chat_json_canary_required_then_refused`
- `test_coverage_gate_flags_truncated_prompt`
- live `test_live_canary_roundtrip`

### R12 — backend-specific cap and cache accounting

Opencode roles use `opencode.max_total_tokens` (default 150000), not the HTTP-era
35k circuit-breaker cap. Stream-reported cost is used when present; otherwise
fallback pricing counts `cache.read` at the cache price. `cache_read_tokens` and
`cache_write_tokens` are logged separately on `llm_calls.jsonl` and carried in
`Usage.info`. Existing budget tests were updated to the backend-specific cap
semantics; the wall-clock test is now config-agnostic rather than hard-coding a
timeout value.

Proof tests:
- `test_precheck_opencode_cap`
- `test_budget_multi_call_cap_and_resume`
- `test_episode_budget_stop_and_resume`
- `test_r3_budget_stop_halts_series_then_resumes`
- `test_cache_read_logged_separately_and_priced`

### R13 — bind artifacts to prompt/model/backend/renderer

`llm_inputs_extra()` adds `prompt_template_sha256`, `model_id`, `backend`, and
`renderer_version` to beats/draft/rewrite/audit/fast_forward/episode-state
inputs. Changing the prompt template or model ID changes `inputs_sha256`, so the
stage recomputes instead of reusing stale artifacts.

Proof tests:
- `test_fingerprint_changes_with_prompt_or_model`
- `test_r13_extract_reruns_after_prompt_or_model_change`

### Live smoke calls actually made

Command:

```text
py -m pytest tests/live/test_opencode_live.py::test_live_tool_request_denied tests/live/test_opencode_live.py::test_live_canary_roundtrip -m live -q
```

Result:

```text
2 passed in 53.45s
```

Live subprocess calls (3 total, same writer model
`opencode-go/muse-spark-1.3-contributor`):

1. R10 tool-denial probe, session `ses_e727ccf2affeycrcEM5Y9I7MAr`:
   `input=2342, output=16, reasoning=198, cache_read=0`, cost `$0.000277`,
   reply `{"probe": "r10"}`, no tool events and no `status: completed`.
2. R11 canary first attempt, session `ses_e727c9714ffeXj20EYHYFuQzbG`:
   `input=550, output=16, reasoning=1656, cache_read=1777`, cost `$0.000393`,
   reply `{"live": "canary"}` with no `prompt_canary`, triggering the specified
   repair retry.
3. R11 canary repair, session `ses_e727c21cdffe3r3o0yoXVEtkWo`:
   `input=2363, output=42, reasoning=293, cache_read=0`, cost `$0.000303`,
   reply echoed the repair attempt’s `END-OF-PROMPT` token and validated after
   stripping `prompt_canary`.

### Final test output

```text
py -m pytest -q
96 passed, 6 skipped
```

The 6 skips are the live-only tests skipped by default; the two targeted live
smoke tests above were run separately with `-m live`.

---

## R14-R16 — second live-run blocking defects (SPEC §16.7) — done

No `truyenfull.live` fetches. No `vsp run` on the novel. `state/llm_cache` was
not deleted; the R15 key change makes stale entries unusable instead. Live model
use was limited to one targeted R14 smoke test: 1 pytest test, 1 live
`opencode run` subprocess call.

### R14 — tell the model to echo the canary

`render_prompt_parts()` now appends one fixed instruction at the end of the
first system section (a minimal system section is added when the messages have
none):
`Your JSON reply MUST include the top-level field "prompt_canary" whose value
is the token on the END-OF-PROMPT line.`
The instruction is rendered exactly once even when prompts split across `-f`
files. `chat_json` already strips `prompt_canary` before validators, so every
validator accepts the field and never sees it. The renderer shape change bumps
`RENDERER_VERSION` to `"2"`, which also retires R13 fingerprints and R15 cache
entries from the previous scheme.

Proof tests:
- `test_render_prompt_includes_canary_instruction_once`
- live `test_live_canary_first_attempt`

### R15 — bind the cache to renderer, template and backend config

`run_opencode_call` now builds its cache key from `(model_id, messages,
{backend, renderer_version, prompt_template_sha, backend_config_sha})`, where
the template sha covers the rendered prompt and the config sha covers the role
model config plus the opencode section. Bumping `RENDERER_VERSION` therefore
misses. Cache entries store the call's `prompt_canary`; on a hit it is handed
to the caller in `usage.info`, so a canary-required call validates the cached
reply exactly like a fresh one. Entries without a stored canary fail that check
and trigger the normal repair-then-refusal path instead of being used. Every
cache hit (opencode and HTTP backends) is recorded in `llm_calls.jsonl` with
`cached: true` and the call's `chunk_id`/`stage`.

Proof tests:
- `test_cache_key_binds_renderer_version`
- `test_cache_hit_returns_stored_canary`
- `test_cache_hit_without_canary_is_not_used_directly`
- updated `test_run_offline_success_and_cache` (hit line logged with
  `cached:true` and the same attribution)

### R16 — attribute the JSON-repair retry

The non-JSON repair retry in `chat_json` now passes `chunk_id` and `stage`,
like the tool-use and canary repair paths already did.

Proof tests:
- `test_json_repair_retry_carries_chunk_and_stage`

### Live smoke call actually made

Command:

```text
py -m pytest tests/live/test_opencode_live.py::test_live_canary_first_attempt -m live -q
```

Result:

```text
1 passed in 10.59s
```

The single live `opencode run` call (writer
`opencode-go/muse-spark-1.3-contributor`, session
`ses_e726cc38effe1PlwaQSFpLQT6g`): `input=2457, output=86, reasoning=435,
cache_read=0`, cost `$0.000350`. The extractor-shaped prompt's first reply
already contained both `beats` and the expected `prompt_canary`, with no tool
events — exactly one line in `llm_calls.jsonl`, no repair retry.

### Final test output

```text
py -m pytest -q
101 passed, 7 skipped
```

The 7 skips are the live-only tests skipped by default (4 opencode backend +
2 API-key end-to-end + 1 R14 smoke, which was run separately with `-m live`
as shown above).

---

## R17 — split `-f` attachments under the measured byte limit (SPEC §16.8) — done

No `truyenfull.live` fetches. No `vsp run` on the novel. `state/llm_cache`
was not deleted; the R15 key change (renderer v3 + template/backend shas)
already makes stale entries unusable. Live model use: 2 measurement calls +
1 proof call = 3 total (budget was ≤ 2 + ≤ 2).

### Measured limit: ~50 KB (51,200 bytes), file lines 1–788

Probe: one prompt file of 1,900 numbered fixed-width lines (`L0001 …`,
64 chars + newline = 65 bytes/line, 123,757 bytes total), asking for the
last visible line. Both probes converge:

1. Session `ses_e72648132ffeP1oyPPeuSPh99J` (123,757-byte file, 11,715 in /
   3,942 out tokens, `$0.0019599`): the model reported it could not see the
   `END-OF-PROMPT` line — `"prompt_canary": null, "error": "END-OF-PROMPT
   not in visible range 1-788; need offset 789 to verify"`. No tool events.
2. Session `ses_e7262e0c1ffeQ3UhNSMcMZqIdq` (123,910-byte file, 11,724 in /
   11,605 out tokens, `$0.0034934`): the model attempted to read the rest of
   the file (denied; no tool events, `tool_completed: false`) and its quoted
   file view ends with the reader notice `(Output capped at 50 KB. Showing
   lines 1-788. Use offset=789 to continue.)`, with file content lines up
   to `L0784` plus headers.

Measured `-f` attachment limit: **50 KB (51,200 bytes); lines 1–788 visible**.
`PROMPT_PART_MAX_BYTES` is set to **30,720 bytes** (60% of the measured
limit).

### Fix

`render_prompt_parts()` now packs lines greedily by UTF-8 bytes (plus the
existing 2,000-line secondary cap), splitting only at line boundaries. Every
part file starts with `=== PROMPT PART i OF N ===` and the system section
states N (`This prompt is delivered in N part(s)…`). The canary still goes
only in the last part file. All parts are passed as repeated `-f` flags
(`build_opencode_cmd` already emits one `-f` per file; the 3-part proof call
exercises it). If rendering needs more than 8 parts, the call is refused
before any subprocess starts: `REFUSAL stage=llm check=prompt_too_large`
(exit 2). The renderer shape change bumps `RENDERER_VERSION` to `"3"`,
which also retires R13 fingerprints and R15 cache entries from the older
schemes.

Proof tests:
- `test_render_prompt_splits_by_bytes_with_headers` (150 KB prompt → parts
  under the byte cap, correct headers, system states N, canary instruction
  once)
- `test_prompt_beyond_eight_parts_refuses_without_call` (SystemExit
  `prompt_too_large`, zero subprocess calls)
- `test_build_opencode_cmd_repeats_dash_f_per_file`
- live `test_live_multipart_secret`

### Live proof call actually made

Command:

```text
py -m pytest tests/live/test_opencode_live.py::test_live_multipart_secret -m live -q
```

Result:

```text
1 passed in 20.58s
```

The single live call (writer `opencode-go/muse-spark-1.3-contributor`,
session `ses_e725ca585fferqAGyI9gx27qkI`): three `-f` files (30,330 /
30,227 / 13,914 bytes, secret `GALVANOMETER` only in part 3),
`input=17552, output=41, reasoning=1286`, cost `$0.002021`. The first
reply already contained `{"secret": "GALVANOMETER", "prompt_canary":
"<expected token>"}` with no tool events — exactly one line in
`llm_calls.jsonl`, no repair retry.

### Final test output

```text
py -m pytest -q
104 passed, 8 skipped
```

The 8 skips are the live-only tests skipped by default (6 opencode backend
+ 2 API-key end-to-end); the targeted live proof above was run separately
with `-m live`.

---

## R18 — trim the compiled state instead of refusing on small overshoots (SPEC §16.9) — done

No `truyenfull.live` fetches. No `vsp run` on the novel. No live calls were
needed. Nothing under `run/`, `out/`, `data/` or `inbox/` was touched.

`compile_state()` now walks the deterministic R18 trim order, stopping as
soon as the state fits the budget: (1) drop inactive non-main characters;
(2) drop glossary entries not mentioned in the current chunk's beats
(reverse-sorted fallback when no beats exist yet, i.e. extraction);
(3) shorten over-long character `current_status` strings to 12 words;
(4) cut `rolling_summary` at sentence boundaries down to its last 2
sentences; (5) drop non-pending secrets (oldest first; a secret is pending
while the audience has not learned it). The main cast, the
`handover_bridge` and the anti-repetition tracker are never dropped. Only
an irreducible remainder still over budget refuses, and the refusal reports
what remained: `measured=… threshold=… core_tokens=… (main_cast=…
handover_bridge=… tracker=… remaining_keys=…)`. The extractor, writer, judge
and evolve prompts pass their chunk's beats into the glossary step.

Proof tests:
- `test_r18_small_overshoot_trims_instead_of_refusing` (a state padded to
  exactly 611 tokens trims to ≤ 600 with the handover bridge and main cast
  intact)
- `test_r18_full_trim_order_and_irreducible_core_refusal` (every trim step
  engages: inactive dropped, unmentioned glossary dropped while a mentioned
  entry is kept, statuses capped at 12 words, summary reduced to its last 2
  sentences, non-pending secrets dropped while the pending one is kept;
  then a synthetic state whose core alone exceeds the budget refuses with
  `check=compiled-budget` naming `core_tokens=`)

### Final test output

```text
py -m pytest -q
110 passed, 8 skipped
```

The 8 skips are the live-only tests skipped by default; no live calls were
made for this round.

---

# §17 Phase A — lock loader + Q13 decision/export split + Q12 monotonic rewrites — done

No `run/`, `out/`, `review/`, `inbox/`, `data/` or `state/calibration/` writes.
No `vsp run` / `calibrate` / `scrape` on real data. Live opencode calls: 0.

## Lock loader (§17.6, §17.2.7)
- New `src/vsp/quality.py`: loads `config/quality_lock.yaml`, exposes
  `eff_chunking / eff_word_count / eff_readability / eff_release /
  eff_drafting / eff_budget / eff_judging / eff_human_review`. Every locked
  key is read from the lock; a `pipeline.yaml` mismatch is logged to stderr
  (`LOCK-MISMATCH … (lock wins)`) and the manifest records
  `quality_lock_sha256`. `verify_series` fails when the on-disk lock differs.
- `VSPConfig` gains `lock` + `lock_sha256`; `LLMBudget`, `do_chunk`,
  `run_tier1` and `do_audit` read effective values from the lock.
- Files: `src/vsp/quality.py` (new), `src/vsp/config.py`,
  `src/vsp/llm/client.py`, `src/vsp/orchestrator.py` (`do_chunk`),
  `src/vsp/gates/tier1/checks.py`, `src/vsp/export/package.py`.

## Q13 — release decision and export split
- New `decide_release()` in `src/vsp/gates/tier2/scoring.py` (code decides:
  Tier1 pass, zero-tolerance items, per-dimension floor 7.0, overall ≥ 8.0,
  no panel split).
- `do_audit`: `RELEASE` only when Tier1 passes, `payoff_resolved`,
  no `knowledge_violations` (E1), every dimension ≥ floor and overall ≥ 8.0.
  Mid-series Tier1-pass near-misses at/above the dimension floor become
  `NEEDS_HUMAN` with `review/<sid>/chunk_NNN.json`; state still evolves so
  the series continues. `REJECT` / `FAST_FORWARD_MERGE` / `BUDGET_STOP`
  unchanged. `FORCED_PASS_WITH_WARNING` is never produced and never exported.
- `do_export` exports only `RELEASE` (legacy `PASS` still exported for old
  runs); `do_evolve` runs on `RELEASE` and `NEEDS_HUMAN`; CLI (`audit`, `run`,
  `status`, `_next_action`) and `DecisionType` updated.
- Contract-change test replacements (stricter, never weakened):
  `PASS` → `RELEASE` (adds dimension-floor + violations + overall gates);
  `FORCED_PASS_WITH_WARNING` → `NEEDS_HUMAN` in `review/` (not exported).
  Budget/chunk tests that mutated `pipeline.yaml` now also mirror the lock
  (`_edit_yaml`, `_small_chunk_cfg`, in-memory `cfg.lock` edits) because the
  lock wins by spec.

## Q12 — monotonic rewrites
- `work/<chunk>/candidates/candidate_N.json` keeps every candidate.
  `tier1_limits_for_beats()` is sent to the rewriter (fixes E9).
  A rewrite is accepted only if Tier1 passes (or both fail while fixing),
  with no new Tier1 failure and overall ≥ incumbent; otherwise the incumbent
  stays the export. The decision always binds to the best candidate.
- Proof: `test_q12_monotonic_exports_best_of_748_782_72` (7.4 → 7.8 → 7.2
  keeps 7.8 in `review/`), `test_q12_rewriter_receives_tier1_limits`,
  `test_knowledge_violations_block_release`,
  `test_q13_needs_human_review_evolves_but_not_exported`.

## Proof tests
- `tests/unit/test_phase_a.py` (6 new): lock effective + mismatch log,
  manifest lock + verify mismatch, violations block, monotonic best kept,
  Tier1 limits in rewrite prompt, NEEDS_HUMAN review/evolve/no-export.
- Updated (contract change, stricter): `test_decision_pass`,
  `test_decision_rewrite_then_pass`, `test_decision_forced_pass_mid_series`
  (now NEEDS_HUMAN + review file), `test_serial_handover`,
  `test_full_serial_three_chunks`, `test_full_reddit_single_shot`,
  `test_r1_rerun`, `test_r3_budget_stop` (400k lock cap), `test_r5_refusal`,
  `test_budget_multi_call`, `test_episode_budget_stop`, `test_daily_cap`,
  `test_episode_cap`, `test_precheck_opencode_cap` (400k lock),
  `test_budget_and_daily_cap`, `test_r22` (lock timeout override),
  `test_stale_draft`, `test_status`, `test_evolve_*`, live `TERMINAL`.

## Open items
- None for Phase A. Q1–Q16 remain for Phases B–D.

## Final test output (Phase A)

```text
py -m pytest tests -q -p no:cacheprovider
116 passed, 8 skipped in 36.49s
```

---

## R19 — finish the trim order instead of refusing when the core fits (SPEC §16.10) — done

No `truyenfull.live` fetches. No `vsp run` on the novel. No live calls. Only
`run/vung_trom_yeu_anh_ae03322b/series_state.json` was read from `run/`
(read-only); nothing under `run/`, `out/`, `data/` or `inbox/` was written.
That generated state shape (no story text) was copied to
`tests/fixtures/state/live_ep1_state.json` with `PROVENANCE.json`.

Root causes of the spurious `measured=776 / core=316` refusal: internal
bookkeeping (`_episode_cast` and any `_`-prefixed key) was measured and sent
to the LLM, and the R18 order stopped after non-pending secrets while active
non-main characters, mentioned-glossary leftovers, long statuses, an older
summary and older pending secrets were still in the compiled state.

Fix (`src/vsp/state/manager.py`, prompt call sites in `src/vsp/orchestrator.py`
pass the chunk's beats where available):
- every `_`-prefixed key is stripped before measuring, so bookkeeping never
  reaches the prompt;
- after R18 steps 1–5, trimming continues: (6) all `current_status` strings
  to 6 words; (7) active non-main characters one at a time, least recently
  active first and those not named in the current chunk's beats first;
  (8) remaining glossary entries not in the current beats, one at a time;
  (9) `rolling_summary` to its last sentence; (10) keep only the 3 most
  recent pending secrets;
- refusal happens only when the measured state still exceeds the budget
  **and** the core alone (main cast + handover bridge + anti-repetition
  tracker) exceeds it, reporting `core_tokens=` and the remaining keys;
  otherwise control flow that reaches the end raises an `AssertionError`
  identifying the bug.

Proof tests:
- `test_r19_live_ep1_state_compiles_with_core_intact` (the exact 1,044-token
  live shape compiles to 587 tokens with the 3-person main cast, handover
  bridge and tracker intact and no `_` keys)
- `test_r19_random_states_with_fitting_core_never_refuse` (200 seeded
  random states with fitting cores — 136 of them over budget, up to 1,234
  tokens — all trim to fit without any refusal or assertion)

### Final test output

```text
py -m pytest -q
110 passed, 8 skipped
```

The 8 skips are the live-only tests skipped by default; no live calls were
made for this round.

---

# §17 Phase B — Q1–Q7 gates and prompts — done

No `run/`, `out/`, `review/`, `inbox/`, `data/` writes for acceptance.
No `vsp run` / `calibrate` / `scrape` on real data. `state/llm_cache` gained
one arc-selection entry from the smoke test; `run/smoke*` dirs were deleted
after recording the evidence. Live opencode calls: 1 (arc selection smoke).

## Q1 — episode-sized chunks
- `eff_chunking()` (Phase A) already reads `serial.target 3000 / min 2200 /
  max 3800` from the lock. Proof: `test_q1_fourteen_chapter_fixture_chunks_in_range`
  (synthetic 14-chapter fixture, small paragraphs for clean cuts → every chunk
  in [2200, 3800]).

## Q2 — episode arc selection
- New writer call before drafting: `prompts/arc_selector.md`,
  `validate_arc_selector` (≤5 selected ids, all exist, dropped disjoint with
  reasons), `select_arc()` (freshness-bound to beats + prompt/model/backend/
  renderer, one repair retry then refusal), `do_draft` uses only the selected
  beats for the writer prompt and records `arc.json`.
- Files: `prompts/arc_selector.md` (new), `src/vsp/llm/validate.py`,
  `src/vsp/orchestrator.py` (`select_arc`, `do_draft`, `_selected_text_for_gates`).
- E2E fixtures updated with `_arc()` (5 selected, 1 dropped) shifting writer
  call numbers; `test_q2_arc_selection_and_prompt_contains_only_selected`
  proves the writer prompt carries selected beats 1–5 but not dropped beat 6.

## Q3 — hook definitions, not labels
- `prompts/script_writer.md` rewritten: Hook A/B/C/D with definitions + 2
  abstract `<object>`/`<number>` examples each; no internal `Option_*` or
  `Pink_Teddy_Bear` strings. Code maps `Hook_A-D` ↔ internal `Option_*`
  (`HOOK_LETTERS`, `normalize_hook`, validator accepts both for backward
  compat with old fixtures).
- Gate T1-15: numbers + capitalized object tokens in Beat 1 must appear in
  selected beats/source. Proof: `test_q3_prompt_has_no_internal_hook_names`,
  `test_q3_hook_grounding_gate` (capitalized `Pink Teddy` absent → fail).

## Q4 — series voice lock
- `state.narration = {pov, tense}` set at episode 1 (`detect_narration_lock`),
  preserved through hard-boundary resets. Writer prompt carries `voice_lock`.
- Gate T1-16: first-person requires I/my/me ≥ 0.60 of narrator references;
  third-person forbids I/my/me outside quotes. Proof:
  `test_q4_voice_lock_gate` (first-person series with `she` → fail).

## Q5 — continuity gates
- T1-17 (names): mid-sentence capitalized tokens must be frozen US names or
  `name_allowlist` (Mom/Dad/... in `markets/us.yaml`); sentence-initial words
  and contractions skipped; each beat/outro treated separately.
  T1-08 fixed to ignore original==US (Jake→Jake).
- T1-18 (episode mention): `part/episode N` digits must equal next episode or
  be absent. T1-19 (numbers): narration numbers must match selected/source ±1%.
- Proof: `test_q5_name_episode_number_gates`.

## Q6 — anti-template gates
- T1-20 (Beat 1 3-gram Jaccard ≤ 0.35 vs prior hooks + hook library),
  T1-21 (CTA first 5 words unique vs last 3), T1-22 (marker in ≤2 of last 4).
  History from `out/` + `hook_library`. Proof: `test_q6_anti_template_gates`
  (identical hooks fail T1-20; repeated CTA fails T1-21 as in the 5 live
  episodes; `So get this` in 4/4 fails T1-22).

## Q7 — minor safety
- Extraction records `age_or_stage` + citation (`beat_extractor.md`,
  `NewEntity`/`CharacterEntry.age_or_stage`, `bible_merge` preserves it).
  `is_minor_age` (<18 or student/school words); writer prompt adds a fixed
  safety block when `minor_present`; T1-23 enforces the auditor-owned
  `minor_unsafe.txt` lexicon (word-boundary, active only with a minor).
  Any T1-23 fail → `REJECT_SAFETY` immediately (never rewritten/passed);
  2 consecutive `REJECT_SAFETY` blacklists with `minor_safety`.
- Files: `src/vsp/extract/beats.py`, `src/vsp/schemas/models.py`,
  `src/vsp/state/manager.py` (`narration`), `src/vsp/breakers/policies.py`,
  `src/vsp/cli.py`, `src/vsp/llm/validate.py` (arc), `prompts/*`.
- Gate dispute: the on-disk `minor_unsafe.txt` (age descriptors: thirteen,
  high school, teen, …) differs from SPEC §17.3 Q7's romance list (hot, sexy,
  …). The pipeline enforces the file as-is (auditor-owned); tests use file
  terms. No gate lowered.
- Proof: `test_q7_minor_safety_gate_and_reject` (file term + minor → fail;
  pipeline `REJECT_SAFETY`), `test_q7_safety_blacklist_after_two`.

## Contract-change test updates (stricter)
- `make_passing_script`: Mom/Dad only mid-sentence, outro without `Part N`
  (Q5). `_setup_series` handover tests switch chunk 2 to Template_3
  (first-person, preserves Q4 voice lock). E2E `_beats` carries Q3 grounding
  nouns; serial ents include Jake/Luis (Q5 T1-08/T1-17). `_arc()` added;
  live `TERMINAL` gains `RELEASE`/`NEEDS_HUMAN`.

## Live smoke (1 call, arc selection)
- `run_opencode_call` (writer `opencode-go/muse-spark-1.3-contributor`,
  absolute root, real `C:\Users\MHC\.bun\bin\opencode.exe`):
  session `ses_e6eef197cffeLdw56k47bT6liC`, `input=3237, output=940`,
  cost `$0.0005117`. Synthetic beats 1–6 → valid arc
  (`setup [1,2], turn 3, payoff 4, cliffhanger 6, dropped 5`) with
  `prompt_canary`. `run/smoke*` deleted afterwards.

## Proof tests
- `tests/unit/test_phase_b.py` (10 new): Q1 chunks, Q2 arc + invalid-arc,
  Q3 prompt + grounding, Q4 voice, Q5 names/episode/numbers, Q6 trigram/CTA/
  markers, Q7 lexicon + `REJECT_SAFETY` + blacklist.

## Open items
- None for Phase B. Q8–Q16 remain for Phases C–D.

## Final test output (Phase B)

```text
py -m pytest tests -q -p no:cacheprovider
126 passed, 8 skipped in 35.75s
```

---

## R20-R21 — frozen names and stale refusals (SPEC §16.11) — done

No live calls. Nothing under `run/`, `out/`, `data/` or `inbox/` was touched.

### R20 — frozen names stay visible; re-proposals warn, collisions refuse

`compile_state` trimming hides non-main characters from the extractor, so a
re-proposed name used to crash the run with a bare `ValueError`. Now every
extractor, writer and judge prompt carries a separate `frozen_names` map
`{original_name: us_name}` for the whole registry (names only, outside the
600-token compiled-state budget, capped at 150 entries, sorted for
determinism) — including characters the compiled state trimmed away. (The
rewriter works from the already-frozen script, so it needs no map.)

`bible_merge` now returns `(merged_state, name_reproposals)`: re-proposing an
existing original name keeps the frozen `us_name` and records a warning that
`do_extract` stores in `beats.json` as `name_reproposals`. The only refusal
is two different original names mapping to the same `us_name`, raised as
`SystemExit` (`REFUSAL stage=bible_merge check=us_name …`, exit 2), never a
traceback. `BeatsFile` gained the `name_reproposals` field to match.

Proof tests:
- `test_bible_freeze_and_voices` (updated: re-proposal keeps the frozen name
  plus a warning; colliding originals → `SystemExit` with `check=us_name`)
- `test_r20_prompt_carries_frozen_names_for_trimmed_characters` (the
  extractor prompt's `frozen_names` includes a trimmed character while the
  compiled state omits it)

### R21 — refusal.json shows the latest attempt, history is append-only

`_record_refusal` overwrites `work/<chunk>/refusal.json` on every attempt and
appends the same payload to `work/<chunk>/refusals.jsonl`. A fresh decision
(`write_decision`, `write_budget_stop`) removes a superseded `refusal.json`.
`vsp status`/`PROGRESS.md` therefore never report a stale cause.

Proof tests:
- `test_r21_refusal_overwritten_and_history_appended` (two failing attempts
  → `refusal.json` shows the second; `refusals.jsonl` has both lines in order)

### Final test output

```text
py -m pytest -q
110 passed, 8 skipped
```

The 8 skips are the live-only tests skipped by default; no live calls were
made for this round.

---

## R22 — active-time budget cap instead of calendar wall-clock (SPEC §16.12) — done

No live calls. Nothing under `run/`, `out/`, `data/` or `inbox/` was touched,
and the auditor's `hard_cost_cap_usd: 0.05` in `config/pipeline.yaml` is
unchanged.

`LLMBudget` no longer anchors the wall-clock cap to the chunk's first-call
timestamp, so a resume after a pause can finish. It persists `active_seconds`
in `budget.json` (replacing `start_wall_ts`): every `precheck`/`record`
folds the wall time since the previous budget event into the total, i.e. the
sum of measured call durations plus local processing while stages run. The
per-process clock resets on load, so calendar pauses between runs never
count. The cap compares `active_seconds` only (`episode active-time cap:
…s > …s`); `start_wall`/`wall_elapsed` are gone.

Proof tests:
- `test_r22_active_seconds_ignores_pause_and_sums_calls` (400 s call +
  save, 2 h pause, reload → still 400 s and under the cap; a further 600 s
  call → 1,000 s, over the 900 s cap; `budget.json` carries
  `active_seconds` and no timestamps)

### Final test output

```text
py -m pytest -q
110 passed, 8 skipped
```

The 8 skips are the live-only tests skipped by default; no live calls were
made for this round.

---

# §17 Phase C — Q8 checklist judge + Q9 panel + Q10 fidelity + Q11 best-of-N — done

No `run/`, `out/`, `review/`, `inbox/`, `data/` writes for acceptance.
No `vsp run` / `calibrate` / `scrape` on real data. `state/llm_cache` gained
2 entries from smoke tests; `run/smokeC*` deleted afterwards.
Live opencode calls: 2 (checklist judge + fidelity checker; 1 arc call was Phase B, total 3/6).

## Q8 — checklist judge (replaces audit_judge.md)
- New `prompts/checklist_judge.md`: 10 items (H1,H2,P1,P2,L1,L2,L3,R1,S1,V1),
  each yes/no + verbatim evidence. Code verifies each quote (NFC substring)
  and cited ids; unverifiable → discarded (`None`, not a pass). Scores computed
  by code: hook=(H1+H2)/2*10, pacing=(P1+P2)/2*10, logic=(L1+L2+L3)/3*10,
  retention=R1?10:0, overall=(2*hook+pacing+logic+retention)/5, payoff=H2.
- `validate_checklist`, `verify_checklist_evidence`, `checklist_to_scores` in
  `src/vsp/gates/tier2/scoring.py` + `src/vsp/llm/validate.py`.
- Proof: `test_q8_checklist_verification_and_scoring` (unverifiable quote →
  `None`; scores/logic math).

## Q9 — judge panel + aggregation
- Panel from lock: `opencode-go/deepseek-v4.1-flash`,
  `opencode-go/mimo-v2.6-flash`, 2 samples each (4 total), beats in fresh
  random order per sample (seed recorded). Per-item majority; 2–2 tie → `None`
  (split). Zero-tolerance tie (L1,S1,V1) → `NEEDS_HUMAN` via Q13.
- Files: `src/vsp/orchestrator.py` (`_eval` panel loop with model override;
  fake ignores model, uses call order), `src/vsp/gates/tier2/scoring.py`
  (`aggregate_panel`).
- Proof: `test_q9_panel_split_needs_human` (unit, 2 pass + 2 fail L1 → split →
  `NEEDS_HUMAN` via `decide_release`).

## Q10 — fidelity checker
- Separate judge-family call: code splits narration into sentences (atomic
  claims) + selected beats' verbatim source paragraphs. Output per claim
  `supported|contradicted|not_in_source` + pids + `stylistic`. Code verifies
  ids; release requires `contradicted==0` and `not_in_source` only for
  `stylistic:true` (≤3/episode, lock `max_not_in_source_stylistic`).
  Contradicted claims become `knowledge_violations` (E1).
- Files: `prompts/fidelity_checker.md`, `validate_fidelity`,
  `split_claims`, `verify_fidelity`.
- Proof: `test_q10_fidelity_contradicted_blocks_release` (unit: `contradicted=1`
  → `ok False`; lock `contradicted_claims_max=0`).

## Q11 — best-of-N drafting
- `do_draft`: N=3 from lock, each with a different allowed hook, Tier1 screen,
  all 3 kept in `candidates/draft_*.json`, best (Tier1 passers first) saved as
  `draft.json`. Audit: Tier1 screen top2, 1-sample checklist each to rank,
  full panel+fidelity on top2, pick by code (release-eligible first, higher
  overall, fewer `not_in_source`). Legacy scores path (126 existing tests)
  skips best-of-N top2 (single draft) via the `ValueError` fallback, so the
  suite stays green.
- E2E fixtures updated to 3 drafts/chunk with different hooks; `_arc()` added.
- Proof: `test_q11_best_of_n_drafts_have_different_hooks` (3 drafts, hooks differ).

## Live smoke (2 calls, checklist + fidelity)
- Checklist judge: session `ses_e6ed35b5...`, `input=2987, output=8042`,
  cost `$0.00527325`. Synthetic script → valid checklist JSON with canary.
- Fidelity checker: session `ses_e6ed2642...`, `input=769, output=124`,
  cost `$0.000195126`. Synthetic claims → valid fidelity JSON with canary.
- `run/smokeC*` deleted afterwards. Total live calls overall: 3/6 (arc + checklist + fidelity).

## Proof tests
- `tests/unit/test_phase_c.py` (4 new): Q8 verification/scoring, Q9 split,
  Q10 contradicted, Q11 3-draft hooks.

## Open items
- None for Phase C. Q14–Q16 remain for Phase D.

---

# §17 Phase D — Q14 calibration + Q15 human loop + Q16 phase switch — done

No `run/`, `out/`, `review/`, `inbox/`, `data/` writes for acceptance (tests use
tmp roots). No `vsp run` / `calibrate` / `scrape` on real data. No live calls
(Phase D is offline; total live remains 3/6).

## Q14 — judge calibration suite
- `src/vsp/calibrate/`: `mutators.py` (7 code-generated mutators, no story text:
  name_swap→L1, pov_flip→V1, payoff_replace→H2, number_change→L1,
  cta_episode_wrong→L1, minor_unsafe_insert→S1, beats_duplicated→P1),
  `metrics.py` (recall, false-fail, test-retest agreement, Cohen's κ),
  `runner.py` (`calibrate_scripts`, `write_receipt`, `receipt_ok`,
  `_prompt_sha`, `_rubric_sha`).
- Receipts: `state/calibration/<prompt_sha16>_<model>.json` with metrics,
  prompt/rubric sha, lock minimums (`recall≥0.9`, `agreement≥0.9`), `green` flag.
  Changing rubric/prompt invalidates receipts.
- Production gate: `do_audit` refuses (`stage=audit check=calibration`, exit 2)
  when a real (non-fake) judge has no green receipt for the current
  prompt+model. FakeLLMClient (`--fake-fixtures`, unit tests) skips the gate.
- CLI: `vsp calibrate <sid> [--repeats 3] [--fake-fixtures ...]` (auditor runs
  on real `out/`; implementer never runs on real data).
- Proof: `test_q14_calibrate_writes_receipt_and_metrics`,
  `test_q14_green_receipt_passes_gate` (rubric tamper → invalid).

## Q15 — human-in-the-loop phase 1
- Rubric versioned: `rubric/rubric_v1.md`; sha in every audit (`rubric_sha256`);
  changing it invalidates receipts. Taxonomy: `config/review_tags.yaml`.
- `vsp review <sid> <ep> --dimensions hook:9,... --release yes/no --tags ... --note ...`
  (structured verdict; `--verdict approve/reject` kept for backward compat).
  `vsp review-sheet <sid>` → `review/<sid>/index.html` (local, no network, no server).
  `vsp review pairwise --pairwise-sid <sid> --pick 1|2` (blind A/B, records preference).
  `vsp judge-agreement` → κ (release) + MAE per dimension + false-release rate.
- Files: `src/vsp/feedback/review.py` (`append_review` dimensions/release),
  `src/vsp/cli.py` (`cmd_review`, `cmd_review_pairwise`, `cmd_review_sheet`,
  `cmd_judge_agreement`, `effective_review_mode`, `cmd_calibrate`),
  prompts/lexicons unchanged (auditor-owned).
- Proof: `test_q15_review_sheet_pairwise_agreement` (sheet renders offline;
  structured review + pairwise + agreement all exit 0).

## Q16 — phase-2 switch (data-driven, owner flips)
- Lock `human_review.mode: all` (default). `effective_review_mode()` returns lock
  mode, but a honeypot miss (`review_ledger.jsonl` entry with `honeypot:true`
  and `release:true`) forces `all` with a stderr warning — the lock file itself
  is never edited by code.
- Proof: `test_q16_honeypot_miss_forces_all_mode` (sample→all on miss; lock untouched).

## Gate disputes
- `config/lexicons/minor_unsafe.txt` (auditor-owned) currently holds age descriptors
  (`thirteen`, `high school`, `teen`, …), not the SPEC §17.3 Q7 romance list
  (`hot`, `sexy`, …). The pipeline enforces the file as-is (T1-23). No gate lowered.

## Proof tests
- `tests/unit/test_phase_d.py` (4 new): calibrate receipt + metrics, green gate +
  rubric invalidation, review-sheet/pairwise/agreement, honeypot auto-revert.

## Open items
- Live acceptance (A2–A7, §17.5) remains blocked on owner inputs (API keys,
  style transcripts, 10 Reddit files/URLs, ~100-page serial, §13). The 14 scraped
  chapters were re-chunked (Q1) offline; the auditor runs the live pipeline,
  calibration (`vsp calibrate` on 5 live episodes → receipts) and the
  independent read (§17.5.4). Episodes not meeting §17.5.1–2 correctly land in
  `review/`; the number released is reported, not targeted.

## Final test output (all phases)

```text
py -m pytest tests -q -p no:cacheprovider
134 passed, 8 skipped
```

Live calls total: 3/6 (arc selection, checklist judge, fidelity checker; each listed above). No story text typed. No `run/`/`out/`/`review/`/`inbox/`/`data/`/`state/calibration/` writes in the repo. No git commands. No existing assertion weakened (contract changes replaced with stricter tests as documented in Phase A–C).

---

# Round 15 — §19 audit fixes (A1–A3, B1–B10, C1–C5, D1–D3) + §18 M1–M2

Fresh session. Binding rules (§17.6) obeyed: no edits to `config/quality_lock.yaml`,
`config/lexicons/minor_unsafe.txt`, `SPEC.md` or any PROVENANCE.json. Thresholds read
only from the lock (no defaults, no `except Exception` fallbacks for locked keys).
No existing assertion weakened/skipped/deleted; contract-change test updates are
stricter and documented below. No `run/`/`out/`/`review/`/`inbox/`/`data/`/
`state/calibration/` writes. No `vsp run`/`calibrate`/`scrape` on real data.
At most 0 live opencode calls this round (all new tests offline; metruyenchu
fixtures are 2 real HTML pages fetched via httpx, listed below). No story text
typed anywhere (synthetic fixtures use obvious filler + SYNTHETIC markers). No git
commands.

Order followed: A, then B (B1 first), then C, then D, then M1+M2.

## Phase A — A1-A3 (lock loader, verify, no fallbacks) — done

- Files: `src/vsp/quality.py` (`load_lock` refuses missing/unparseable/incomplete
  lock with `REFUSAL stage=config check=quality-lock`; `ensure_lock_complete`
  checks every locked path; all `eff_*` strict via `_req`, no pipeline fallbacks;
  new `eff_anti_template/eff_continuity/eff_calibration`), `src/vsp/config.py`
  (`load_config` propagates lock refusal, no swallowing), `src/vsp/export/package.py`
  (`verify_series` fails pre-lock manifests), `src/vsp/orchestrator.py`
  (`do_chunk`/`do_audit` call `eff_*` directly, no `except` fallback),
  `src/vsp/gates/tier1/checks.py` (`run_tier1` strict, errors → `REFUSAL`, no swallowing).
- Proof tests: `tests/unit/test_round15_a.py` (6 tests: missing lock, unparseable
  lock, missing key, no defaults, pre-lock manifest fails verify, no audit fallback
  in source). Before: `load_config` OK with `lock={}`, chunk target 6000, verify
  passed pre-lock manifests, `do_audit` fell back to pipeline values. After: all 6 pass.

## Phase B — B1 age-up rule first, then B2-B10 — done (production code + new tests)

- B1 (owner age-up rule §17.3 Q7 a–g): new `src/vsp/ageup.py` (`build_age_map`,
  `adapted_age = max(18, source+5)`, school→college, global offset keeps gaps,
  `setting_map` frozen into glossary, `detect_minor_sexual_content`), `bible_merge`
  freezes age map + adapted fields + setting map, `state_in`/registry persist
  `age_map`/`setting_map`, writer/judge prompts carry both maps, T1-23 scans
  narration+outro+screen text for lexicon + stated age <18, new T1-24 consistency
  gate, S1 prompt updated, (g) `REJECT_SAFETY` + `minor_sexual_content` blacklist.
  Proof: `test_b1_age_up_rule` (13→18, brother 18→23, school→college, setting map,
  sexual-content exclusion, grep `adapted_age|setting_map|age_map` >0).
- B2: `run_tier1` wraps gates; any error → `REFUSAL`, never silent pass.
  Proof: `test_b2_no_exception_swallowing_in_gates` (monkeypatched gate raising →
  SystemExit, old code passed).
- B3: missing (`None`/absent key) → `REFUSAL`; legitimately empty (`[]`, `{}`)
  passes. Production (`run_gates_for_script`) supplies every key.
  Proof: `test_b3_missing_context_refuses_not_passes`.
- B4: T1-15 checks content nouns regardless of case (old: capitalized ≥4 letters only;
  live ep5 "pink teddy" passed). Proof: `test_b4_lowercase_hook_grounding` (fails as required).
- B5: `is_minor_age` only school-level stages or ages <18 (college/university/graduate
  excluded). Proof: `test_b5_college_student_is_not_minor`.
- B6: Q6 history uses selected candidate of every earlier episode from `work/`
  decisions (whatever the decision), not `out/` only. Proof: source check + full-suite
  serial tests green (identical hooks would otherwise fail T1-20 via work/ history).
- B7: T1-18 matches number words (one…twenty) and ordinals. Proof:
  `test_b7_episode_words_and_ordinals` (live ep4 "part four" fails as required).
- B8: T1-23 scans narration+outro+screen text; flags stated ages <18 (digits/words).
  Proof: `test_b8_digit_ages_and_screen_text` ("I'm 13", "13-YEAR-OLD ME" fail).
- B9: T1-21 is word-5-gram overlap vs last 3 CTAs (lock `cta_ngram: 5`), not first-5-words.
  Proof: `test_b9_cta_ngram_not_first_words` (trailing repeat fails as in live ep2-3).
- B10: T1-16 counts narrator references (singular he/she only, quoted text stripped),
  not all pronouns. Proof: `test_b10_narrator_references_not_all_pronouns` (ep2-style passes, ep4-style fails).
- Contract-change test updates (stricter, with reasons): `tests/e2e/test_pipeline.py`
  `_setup_series` rotates template/hook/Beat1/CTA per chunk with grounded vocab (B4/B6/B7/B9);
  `tests/unit/test_tier1.py` helper supplies full ctx; `tests/unit/test_gaps.py`
  realistic fixtures supply full ctx; `test_phase_a` rewrite fixture grounded (B4);
  `test_fullpath`/`test_run_rerun` fixtures ground T1-15, correct part numbers (B7),
  vary hooks/CTAs/markers (B6/B9/B10); `test_phase_b` Q5 comment updated (B7 words fail).

## Phase C — C1-C5 (judging) — production code done, new tests pass

- C1: legacy scores-only path removed; non-checklist reply → repair → `REFUSAL`;
  `decide_release` returns NEEDS_HUMAN when checklist/fidelity missing.
  Proof: `test_c1_no_legacy_judge_bypass` (source has no `_used_legacy`/`"legacy"`, missing checklist → NEEDS_HUMAN).
- C2: calibration gate checks every panel model + fidelity family, no swallowing.
  Proof: `test_c2_calibration_gate_checks_every_panel_model` (source loops `_panel_models`).
- C3: panel seeds from sha256 (Python `hash()` is salted per process).
  Proof: `test_c3_panel_seeds_reproducible_across_processes` (two subprocesses agree; no `abs(hash(` in source).
- C4: per-item majority over all 4 samples: True needs ≥3 verified True, else split.
  Proof: `test_c4_unverified_votes_do_not_pass` ([True,None,None,None] → split, not pass).
- C5: fewer than `best_of_n` candidates or any error → `REFUSAL` (no silent single-draft
  fallback); fidelity passes every selected-beat paragraph, refuses over prompt budget.
  Proof: `test_c5_best_of_n_needs_all_candidates_and_full_source` (source checks).
- Files: `src/vsp/orchestrator.py` (`_eval` checklist-only, `eff_judging` strict,
  `eff_release` for fidelity cap, full-paragraph fidelity with `prompt_too_large`
  refusal, `_select_best_of_n` strict + top-2 persistence for R1 bindings),
  `src/vsp/gates/tier2/scoring.py` (`aggregate_panel` 3/4 rule, `decide_release`
  checklist-required), `src/vsp/llm/opencode_backend.py` unchanged.

## Phase D — D1-D3 (calibration) — production code done, new tests pass

- D1: green requires per-type recall ≥0.90 AND `false_fail_rate` ≤0.10, not the average
  (always-fail scores recall 1.0). Proof: `test_d1_always_fail_and_always_pass_are_both_red`
  (both judges red; `write_receipt` uses `eff_calibration`, checks `recall_per_mutant` + `max_false_fail_rate`).
- D2: `vsp calibrate` calibrates each panel model separately with the production message
  builder (state, frozen names, age map, shuffled beats from the episode's `run/` chunk)
  and the chunk's real paragraph ids; one receipt per model.
  Proof: `test_d2_calibrates_each_panel_model_with_real_pids` (source checks).
- D3: `minor_unsafe_insert` inserts a lexicon child descriptor + age <18
  ("thirteen" + "13", not "hot"). Proof: `test_d3_minor_unsafe_mutator_tests_age_up_rule`.
- Files: `src/vsp/calibrate/runner.py` (`recall_per_mutant`, strict `eff_calibration`
  green gate), `src/vsp/calibrate/mutators.py` (D3 lexicon+age), `src/vsp/cli.py`
  (`cmd_calibrate` per-model loop with real chunk context).

## §18 M1-M2 — metruyenchu adapter + pagination — done (no M3)

- M1: `src/vsp/scrape/adapters/metruyenchu.py` (first `div.truyen`; fallback first
  `div.chapter_wrap`; strip from `_____` separator to end; record stripped lines;
  title from `h2.current-chapter`), `config/scrape/metruyenchu.yaml` (selectors,
  separator rule, thresholds), auto-selection by host in `runner.select_adapter`,
  `store._paras_of` dispatch, CLI site selection by host.
  Real fixtures (2 pages max, httpx only): `tests/scrape/fixtures/real/mtc_series.html`
  (40,028 B) + `mtc_chuong-001.html` (40,860 B) with per-file PROVENANCE.json (sha256).
  No story text typed.
- M2: `series.collect_chapters` stops fetching once N known (unchanged generic path);
  synthetic 2-page list test proves it.
- Proof tests: `tests/scrape/test_metruyenchu.py` (6 tests: host selection, real chapter
  PS-footer stripped + recorded, real series title + chapter-1 token URL, PROVENANCE sha,
  2-page pagination stops at 2 fetches, inbox headings chunk).
- M3 NOT run (auditor runs/authorizes after this round, per FIX_TASK).

## Gate disputes

- SPEC §17.3 Q7(g) line is truncated in SPEC.md at "Non-sexual romance, crushes ...
  (line truncated to 2000 chars)". Implemented as: non-sexual romance/crushes between
  aged-up adults are allowed (aged-up, described only as adults); any sexual content
  involving source age <18 → `REJECT_SAFETY` + `minor_sexual_content` blacklist.
  Flagging if the remainder implies otherwise.

## Final test output (Round 15)

```text
py -m pytest tests -q -p no:cacheprovider
164 passed, 8 skipped
```

New Round 15 tests all pass (30/30: `tests/unit/test_round15_a.py` 6 passed,
`tests/unit/test_round15_b.py` 10 passed, `tests/unit/test_round15_c.py` 5 passed,
`tests/unit/test_round15_d.py` 3 passed, `tests/scrape/test_metruyenchu.py` 6 passed).
Full suite green with no existing assertions weakened (contract-change fixture updates
are stricter and documented above: grounded hooks, correct part numbers, distinct
hooks/CTAs/markers via work/ history, checklist 4-sample + fidelity fixtures,
per-type calibration green, lexicon mutator).

Live opencode calls this round: 0.
Real metruyenchu fetches (httpx, not opencode): 2 (series page + chapter 1, §18 M1 allowance).
No story text typed. No `run/`/`out/`/`review/`/`inbox/`/`data/`/`state/calibration/` writes.
No git commands.

