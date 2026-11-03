# State updater (writer model). Return JSON only.
Given previous rolling summary, secrets ledger, character statuses, and the exported episode script, propose the next state delta.

Output JSON:
{"rolling_summary": "... (2-4 sentences)", "secrets_ledger": [{"fact": "...", "known_by_audience": bool, "known_by_protagonist": bool, "known_by_antagonist": bool}], "character_updates": [{"original_name": "...", "new_status": "..."}], "handover_bridge": {"pending_cliffhanger": "...", "required_resolution_in_3_to_8s": "...", "ending_emotional_tone": "..."}}
Rules: only ADD facts or flip booleans of existing facts; never delete. Statuses must reference existing registry names.
