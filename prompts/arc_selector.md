# Episode arc selector (writer model). Return JSON only.
From the chunk beats, pick exactly 1 central conflict for this ~70s episode.

Inputs: chunk beats (each with beat_id, summary_en, characters, tension,
is_twist, is_cliffhanger_candidate).

Output JSON:
{"setup_beat_ids": [<id>, ...], "turn_beat_id": <id>, "payoff_beat_id": <id>,
 "cliffhanger_beat_id": <id>,
 "dropped_beat_ids": [{"beat_id": <id>, "reason": "<one line>"}]}

Rules:
- Select at most 5 beats in total across setup + turn + payoff + cliffhanger.
- Every selected id must exist in the chunk beats. No duplicates.
- Dropped ids must exist, must be disjoint from selected, each with a reason.
- Prefer one conflict: setup sets it up, turn escalates, payoff resolves the
  hook anomaly, cliffhanger opens the next loop.
- Dropped beats go into the handover summary, not into this episode.
