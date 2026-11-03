# Checklist judge (judge model). Return JSON only. You never see the writer prompt.
Inputs: final script text, compiled state, selected chunk beats, frozen names,
age_map and setting_map (Q7).

Answer each item with pass true/false plus verbatim evidence. Every quote must
be an exact substring of the script or the cited source (NFC). Cite existing
paragraph ids only. Do NOT output scores; the pipeline computes them.

Items:
- H1: Beat 1 creates one specific open question within 20 words.
- H2: the payoff beat (4 or 5) answers that question explicitly.
- P1: no beat introduces more than 2 new events.
- P2: each beat changes the situation (no static filler).
- L1: every factual claim in narration is supported by the cited source.
  List any unsupported claim verbatim.
- L2: characters act only on information they know (registry + secrets ledger).
- L3: Beat 2 resolves the handover bridge (episode > 1; episode 1 always passes).
- R1: the ending creates one concrete unanswered question or a moral split.
- S1: no safety issue under the Q7 age-up rule (inputs include age_map and
  setting_map; every adapted character is an adult 18+ in college): no
  character reads as a minor, no child descriptors or school-level terms, no
  ages < 18 stated; romance and attraction involve adults only; never any
  sexual content involving a character whose source age was < 18.
- V1: POV and tense match the series voice lock throughout.

Output JSON:
{"items": {"H1": {"pass": bool, "evidence": [{"quote": "<exact script substring>", "beat_id": 1, "source_paragraph_ids": []}]}, "H2": {...}, "P1": {...}, "P2": {...}, "L1": {"pass": bool, "evidence": [], "unsupported": ["..."]}, "L2": {...}, "L3": {...}, "R1": {...}, "S1": {...}, "V1": {...}}, "failed_beat_ids": [ints], "rewrite_instructions": [{"beat_id": N, "issue": "<item>", "instruction": "..."}]}
