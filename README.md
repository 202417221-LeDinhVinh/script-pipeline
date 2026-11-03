# Viral Script Pipeline (MVP)

Automated, deterministic pipeline: raw long/short creative texts → 65–75s short-form video scripts.
Covers SPEC §1–§13 (M1 deterministic core, M2 LLM path, M3 series/state/breakers/export,
M4 inbox automation + human feedback).

## Verification status (2026-10-30 fix pass)

- `py -m pytest tests -q` is green offline (fake LLM, no network) — acceptance **A1**
  (includes every decision-table e2e row, resume/budget/blacklist/handover/verify tests).
- `out/` + `verify` are covered offline via `tests/e2e/test_fullpath.py` (3-chunk serial
  and single-shot, manifest + `verify_series` green) — offline evidence for the **A4**
  mechanism (full A4 needs owner data).
- Live acceptance **A2, A3, A5, A6, A7** is **blocked on owner inputs** (SPEC §13):
  API provider + keys + model IDs/prices, 3 verified style transcripts in
  `assets/style_examples/`, 10 Reddit story files/URLs + one ~100-page serial with
  `rights_status`. No fabricated LLM outputs are stored in `run/` or `out/`.
- `assets/style_examples/PLACEHOLDER_*` and `inbox/PLACEHOLDER_*` are invented samples,
  skipped by the pipeline, and are **not** acceptance evidence. Realistic offline fixtures
  live under `tests/fixtures/` (Reddit-like story, 3-chunk serial, one passing script per
  template + failing variants).

## Run once keys and sources exist

```powershell
py -m pip install -e .
$env:VSP_WRITER_API_KEY="..."; $env:VSP_JUDGE_API_KEY="..."
# A2: 10 Reddit stories via inbox (each must reach a terminal decision; >= 5 exported)
py -m vsp.cli run --inbox inbox
py -m vsp.cli status
# A3: ~100-page serial, >= 3 consecutive episodes with handover
py -m vsp.cli ingest data\serial.txt --rights public_domain --topology CONTINUOUS_SERIAL
py -m vsp.cli chunk <series_id>
py -m vsp.cli extract <series_id>
py -m vsp.cli draft <series_id> --chunk 1
py -m vsp.cli audit <series_id> --chunk 1
py -m vsp.cli export <series_id>
py -m vsp.cli verify <series_id>   # A4
# A5: mean cost per exported episode + daily-cap stop
#     (see state\spend_ledger.jsonl; run a large inbox to trip the $5.00 daily cap)
# A6: unattended `vsp run --inbox inbox` producing >= 5 exported scripts
# A7: owner review
py -m vsp.cli review <series_id> 1 --verdict approve --tags hook,pacing
py -m vsp.cli hooks rebuild
# live acceptance tests (need keys): py -m pytest tests/live -m live
```

Single commands: `ingest` (Stage 0), `chunk`, `extract`, `draft --chunk N`,
`gate [--file script.json | <series_id> --chunk N]`, `audit`, `export`, `verify`,
`run [--inbox DIR | source] [--fake-fixtures scripts.json]`, `status`
(regenerates `run/<series_id>/PROGRESS.md`), `review`, `hooks rebuild`.
`--root` overrides the project root (default: repo dir).

Exit codes: `0` success, `2` deterministic refusal (gate/blacklist/schema/binding),
`3` budget/breaker stop, `1` unexpected error.

## Tests

```powershell
py -m pytest tests -q        # offline (fake LLM), must be green — acceptance A1
py -m pytest tests/live -m live  # live LLM, needs keys + owner inputs (§13)
```

## Per-fix report

See `FIX_REPORT.md` (per-ID status, files changed, proving tests, final output).
