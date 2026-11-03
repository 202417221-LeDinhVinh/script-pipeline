# Task for the implementing agent (round 15: audit fixes + metruyenchu adapter)

Fresh session. An independent auditor checked §17 Phases A-D. They are NOT accepted.

1. Read `SPEC.md` §19 completely (19.1, 19.2, 19.3). Then read §17.3 rows Q7-Q14 and §17.6.
2. Fix every defect: A1-A3, B1-B10, C1-C5, D1-D3. Order: A, then B (B1 first: implement
   the owner's age-up rule exactly as §17.3 Q7 (a)-(g)), then C, then D.
3. For each ID, write a test that FAILS on the current code with the probe input described
   in §19, then make it pass. Name tests `test_<id>_...` (e.g. `test_b4_lowercase_prop`).
4. Then implement §18 M1 and M2 (metruyenchu adapter + pagination). Do NOT run M3.
   The auditor runs or authorizes M3 after verifying this round.

Binding rules (§17.6): do not edit `config/quality_lock.yaml`, `config/lexicons/minor_unsafe.txt`,
`SPEC.md`, any PROVENANCE.json. Read thresholds only from the lock, with no defaults and no
`except Exception` fallbacks for locked keys or gates. Never weaken, skip or delete an existing
assertion; if a contract change forces a test change, replace it with a stricter one and say why.
Never write `run/`, `out/`, `review/`, `inbox/`, `data/`, `state/calibration/`. Never run
`vsp run`, `vsp calibrate`, `vsp scrape` on real data. At most 2 live opencode calls this
round, listed in FIX_REPORT.md. You may fetch at most 2 real metruyenchu pages for M1
fixtures (series page + chapter 1) and must write PROVENANCE.json with their sha256.
No story text typed anywhere. No git commands.

Finish: run `py -m pytest tests -q -p no:cacheprovider`, add a "Round 15" section to
`FIX_REPORT.md` with, per ID: files changed, test name, and the before (failing) /
after (passing) evidence; then the final pytest line.