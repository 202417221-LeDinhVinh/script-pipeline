# MASTER TECHNICAL SPECIFICATION & PRD
## Project: Viral Script Pipeline (MVP)
*An Automated, Deterministic Pipeline for Converting Raw Long/Short Creative Texts into High-Retention Short-Form Video Scripts*

---

## 1. Executive Summary & MVP Scope

### 1.1 Objective
Build an automated, stateful script extraction pipeline that transforms diverse, multilingual creative texts (Reddit posts, web novels, serial fiction, anthologies) into **captivating 65–75 second short-form video scripts** (targeting TikTok Creator Rewards $>60\text{s}$ threshold, YouTube Shorts, and Reels). The system prioritizes opening disruptive hooks, mid-video retention pacing, cultural adaptation for Tier-1 markets (US), and rigorous multi-tier quality gates inspired by the deterministic philosophy of `sat-extraction-pipeline`.

### 1.2 Target Models
Designed and optimized for ultra-low-cost, high-performance open-source / Chinese API models:
- **Writer Agent:** `Qwen-2.5-72B-Instruct`
- **Audit Judge Agent:** `DeepSeek-V3`

Model IDs, endpoints and per-token prices are **configuration**, never code constants. Both
roles are called through one OpenAI-compatible chat client (DashScope, DeepSeek, OpenRouter,
SiliconFlow, or a self-hosted lab server all expose this API). Writer and judge must be
different model families so the audit is not self-grading.

### 1.3 MVP Boundaries (Strict Definition of Done)
- **In-Scope (MVP):**
  1. Text Ingestion & Source Provenance Tracking.
  2. Story Topology Classification & Hard-Boundary Chunking.
  3. Story Beat Extraction, Character Bible, and Two-Layer State Management.
  4. Script Drafting Engine with Dynamic Template Routing (3 Pacing Templates, 4 Cold Open Hook Options).
  5. US Cultural Adaptation & Anti-AI Slop Processing.
  6. Two-Tier Quality Gates: Tier 1 (Deterministic Python code, zero token cost) and Tier 2 (LLM-as-a-Judge audit with targeted rewrites).
  7. Circuit Breakers (token/cost caps, mid-series fast-forward fallback, source blacklisting).
  8. Artifact Packaging & Structured Script JSON export (`out/<series_id>/ep_<NNN>.json`).
- **Out-of-Scope (Deferred to Post-MVP):**
  - Text-to-Speech (TTS) audio rendering (ElevenLabs / F5-TTS / CosyVoice).
  - Video rendering engine (MoviePy / FFmpeg / ComfyUI / Stable Diffusion).
  - Automated social posting, IP proxy management, and device farms.
  - Large-scale scraping, OCR of protected platforms, audio/visual watermarking.
  - Learning from traffic metrics (RL / auto-tuning from views and retention).
  - Output languages other than `en-US`, and market profiles other than `US`.
  - Affiliate links, sponsor insertion, paywall/subscription locking.

### 1.4 Alignment with the Business Proposal
The business proposal ("Business Proposal.docx", Lê Vĩnh) defines the full product. This MVP
implements its script-generation core only. Mapping of proposal items:

| Proposal item | MVP treatment |
|---|---|
| Multi-language sources, chunking so long texts never overflow context | In scope (§4, §10 M1) |
| Cheap Chinese models | In scope, config-driven (§1.2) |
| Cultural Adaptation Agent | In scope (§6, glossary in Stage 2) |
| Currency → `$`, units dynamic per target audience | In scope as a **market profile**; only `US` shipped. AU/CA use metric, so the unit gate must read the profile, never hard-code a metric ban |
| Quality gates so volume does not produce junk | In scope (§7) |
| Human-in-the-loop feedback, learn successful hooks | Minimal in scope: `review` ledger + approved-hook library fed back as few-shot examples (§10.9). Traffic-driven learning deferred |
| Week 1–2: E2E on a 100-page chapter / 10 Reddit stories | MVP acceptance A2/A3 (§12) |
| Week 3–4: 3–5 fully automated videos/day | Becomes **3–5 release-ready scripts/day** from an inbox. Video needs TTS + render, which are out of scope |
| Easy sources first (Reddit) | File ingestion + single Reddit post URL. No crawler |
| TTS, visuals, posting, anti-repost protection, monetization | Deferred; schema keeps `tts_*`, `b_roll_vibe`, `sfx_cue`, `bgm_directive` fields for the future renderer |

---

## 2. System Architecture & End-to-End Dataflow

The pipeline executes through sequential, immutable stages where each stage validates data against a strict schema and writes intermediate artifacts to disk:

```
[Raw Source Text (URL/File)]
             │
             ▼
[Stage 0: Ingestion & Topology Routing]
  ├── Classifies narrative structure: Single-Shot | Anthology | Episodic | Continuous
  └── Assigns SHA-256 hash & provenance tracking
             │
             ▼
[Stage 1: Dynamic Boundary Chunking]
  ├── Enforces Hard-Boundary Firewall (no mixing of separate cases/stories)
  └── Implements Sliding Arc (5,000–7,000 words) with dialogue Clean-Cut checks
             │
             ▼
[Stage 2: Beat Extraction & State Tracking]
  ├── Extracts global entities -> series_bible.json (Static)
  └── Initializes episode_handover.json (Dynamic)
             │
             ▼
[Stage 3: Script Drafting Engine]
  ├── Topology Router selects Pacing Template (1–3) & Cold Open Hook Option (1–4)
  ├── Applies US Archetype Mapping & Conversational Spoken Rhythm
  └── Enforces Payoff Binding Rule (Beat 1 resolves at Beat 4/5)
             │
             ▼
[Stage 4: Two-Tier Quality Gates]
  ├── Tier 1: Deterministic Python Gates (WPM, 185-235 words, Flesch-Kincaid <= 6.5, Regex)
  └── Tier 2: LLM-as-a-Judge Audit (DeepSeek-V3, 4 criteria, 10-point scale)
             ├── Score >= 8.0: PASS
             ├── Score 5.0–7.9: Targeted Rewrite by Beat ID (Max 2 retries)
             └── Score < 5.0: HARD REJECT or Mid-Series Fast-Forward Merge
             │
             ▼
[Stage 5: State Evolution & Circuit Breakers]
  ├── Updates character_knowledge_matrix & narrative_cursor
  └── Enforces Episode ($0.02), Series (2 consecutive failures), and Daily ($5.00) caps
             │
             ▼
[Stage 6: Artifact Export & Receipt Packaging]
  └── Emits finalized JSON to out/<series_id>/ep_<NNN>.json with manifest
```

---

## 3. Data Contracts & JSON Schemas

### 3.1 Chunk Metadata (`chunk_metadata.json`)
Emitted by Stage 1 prior to Beat Extraction:
```json
{
  "chunk_id": "holmes_case_01_part_1",
  "series_id": "holmes_adventures_001",
  "topology_type": "EPISODIC_SERIES",
  "word_count": 4850,
  "is_arc_start": true,
  "is_hard_boundary_end": false,
  "state_reset_instruction": "KEEP_MAIN_CAST_FLUSH_PLOT",
  "raw_text_content": "..."
}
```

### 3.2 Series State Architecture (`series_state.json`)
Combines static lore (`series_bible.json`) and dynamic handover (`episode_handover.json`) into a compact footprint (~400 tokens) to prevent context bloat on cheap LLMs:

```json
{
  "series_id": "novel_zh_089",
  "current_episode": 2,
  "narrative_cursor": {
    "last_processed_chapter": 3,
    "last_processed_beat_id": 14,
    "story_arc_progress": "35%"
  },
  "anti_repetition_tracker": {
    "prev_pacing_template": "Template_1_In_Medias_Res",
    "prev_hook_option": "Option_2_Out_Of_Context_Confession",
    "forbidden_for_current_ep": ["Template_1_In_Medias_Res", "Option_2_Out_Of_Context_Confession"]
  },
  "character_registry": [
    {
      "original_name": "Lục Cẩn Niên",
      "us_name": "Marcus Vance",
      "role": "Billionaire Fiancé (Antagonist)",
      "tts_voice_id": "en_us_male_deep_02",
      "current_status": "Suspicious of protagonist, holds the prenatal test result"
    }
  ],
  "localized_glossary": {
    "Lục Thị Gia Tộc": "The Vance Trust Fund",
    "50 vạn tệ sính lễ": "$80,000 Prenup Clause",
    "Bệnh viện Nhân Ái": "St. Jude Memorial Hospital"
  },
  "knowledge_and_plot_state": {
    "rolling_summary": "Protagonist (Chloe) faked her medical report to trap Marcus, but his mother secretly swapped the envelope at the dinner party.",
    "secrets_ledger": [
      {
        "fact": "The baby is not Marcus's",
        "known_by_audience": true,
        "known_by_protagonist": true,
        "known_by_antagonist": false
      }
    ]
  },
  "handover_bridge": {
    "pending_cliffhanger": "Marcus is tearing open the real envelope right in front of the family.",
    "required_resolution_in_3_to_8s": "Reveal that the envelope inside was actually blank paper swapped by the maid.",
    "ending_emotional_tone": "high_panic_breathless"
  }
}
```

### 3.3 Standard Script Output Schema (`script_output.json`)
The production artifact consumed by downstream video/audio engines:
```json
{
  "script_metadata": {
    "series_id": "novel_zh_089",
    "episode_number": 2,
    "pacing_template": "Template_1_In_Medias_Res",
    "hook_option": "Option_2_Out_Of_Context_Confession",
    "hook_payoff_beat_id": 4,
    "target_market": "US",
    "estimated_duration_sec": 68,
    "total_word_count": 204,
    "provenance": {
      "source_url_or_path": "data/novels/chapter_03.txt",
      "source_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    }
  },
  "scene_beats": [
    {
      "beat_id": 1,
      "block_type": "Disruption_Hook",
      "timestamp_est": "00:00 - 00:03",
      "narration_text": "I smiled as the doctor pulled my husband's plug, even though I had the donor match right in my purse.",
      "tts_emotion_prompt": "urgent, cold, slightly sarcastic, fast-paced",
      "screen_text_overlay": "PULLED HIS PLUG?!",
      "b_roll_vibe": "satisfying_kinetic_sand_cutting",
      "sfx_cue": "heart_monitor_flatline_record_scratch.mp3",
      "bgm_directive": "start_tense_synth_beat"
    },
    {
      "beat_id": 2,
      "block_type": "Fast_Context_And_Handover",
      "timestamp_est": "00:03 - 00:15",
      "narration_text": "Rewind two hours to when Marcus tore open that envelope at dinner. It was blank. The maid had swapped it just in time.",
      "tts_emotion_prompt": "relieved but cautious",
      "screen_text_overlay": "THE ENVELOPE WAS BLANK",
      "b_roll_vibe": "soap_cutting_macro",
      "sfx_cue": "whoosh_transition.mp3",
      "bgm_directive": "steady_suspense_drone"
    },
    {
      "beat_id": 3,
      "block_type": "Escalation",
      "timestamp_est": "00:15 - 00:35",
      "narration_text": "Marcus threw a glass against the wall, demanding a search of every bedroom. If they found the real medical records under my mattress, my trust fund was gone.",
      "tts_emotion_prompt": "intense, escalating pressure",
      "screen_text_overlay": "SEARCH EVERY ROOM",
      "b_roll_vibe": "minecraft_parkour_high_speed",
      "sfx_cue": "glass_shatter.mp3",
      "bgm_directive": "bass_heavy_pulse"
    },
    {
      "beat_id": 4,
      "block_type": "Micro_Twist_And_Payoff",
      "timestamp_est": "00:35 - 00:55",
      "narration_text": "Turns out, Marcus collapsed clutching his chest before his security reached the stairs. That wasn't a panic attack. His mother had poisoned his wine.",
      "tts_emotion_prompt": "shocked, whispering conspiracy",
      "screen_text_overlay": "POISONED HIS OWN SON",
      "b_roll_vibe": "subway_surfers_fluid",
      "sfx_cue": "bass_drop_thud.mp3",
      "bgm_directive": "eerie_silence_then_drone"
    },
    {
      "beat_id": 5,
      "block_type": "Climax_And_Cliffhanger",
      "timestamp_est": "00:55 - 01:08",
      "narration_text": "When the elevator doors opened at the hospital morgue, I saw who was actually lying on that gurney, and it wasn't Marcus...",
      "tts_emotion_prompt": "chilling, breathless suspense",
      "screen_text_overlay": "NOT MARCUS ON THE GURNEY",
      "b_roll_vibe": "hydraulic_press_slow_crush",
      "sfx_cue": "metal_door_slam_echo.mp3",
      "bgm_directive": "sharp_cutoff"
    }
  ],
  "call_to_action": {
    "type": "cliffhanger_subscription",
    "outro_narration": "Hit follow for Part 3 to find out whose body was in that morgue.",
    "loop_connector_word": "Because..."
  }
}
```

---

## 4. Ingestion, Topology & Dynamic Chunking

### 4.1 Topology Classifier Matrix
Every input text is routed into one of four processing streams:

| Stream Type | Source Characteristics | Chunking Strategy | State Lifecycle |
|---|---|---|---|
| **1. Single-Shot** | Reddit posts (`r/AITA`, `r/ProRevenge`), creepypasta $< 3,500$ words | Ingested whole. No splitting. Produces 1–2 standalone episodes. | Full state flush after completion. |
| **2. Anthology** | Short horror collections, Zhihu compilations | Chunk per independent story. Mark `is_hard_boundary_end = true`. | 100% state reset (including characters) at story boundary. |
| **3. Episodic Series** | Detective cases (e.g., Sherlock Holmes), procedural crime | Chunk strictly within case boundaries. Never merge Case A and Case B. | Persist main cast & voice IDs; flush victims, suspects, and secrets. |
| **4. Continuous Serial** | Web novels, romance/revenge serials (100+ pages) | Sliding Arc (5k–7k words) with 300-word clean-cut boundary checks. | State persists continuously across all episodes. |

### 4.2 Hard-Boundary Firewall Rule
When `is_hard_boundary_end == true`:
- The drafting agent is forbidden from creating an open cliffhanger bridging into the next chunk.
- The episode must deliver a decisive payoff or resolution loop.
- The state manager automatically purges ephemeral entities.

---

## 5. Retention Scripting & Hook Engine

### 5.1 Three Pacing Templates (65–75 Seconds Pacing)
1. **Template 1: In Medias Res (Revenge / Betrayal / High Action)**
   - `00:00 - 00:03`: Disruption Hook (Cold Open).
   - `00:03 - 00:15`: Fast Context & Handover (Resolves previous cliffhanger in 3–8s).
   - `00:15 - 00:35`: Escalation & Pattern Interrupt (Pacing shift every 8–10s).
   - `00:35 - 00:55`: Micro-Twist / Reversal (Sudden pivot).
   - `00:55 - 01:10`: Climax & Loop or Paywall Cliffhanger.
2. **Template 2: The Breadcrumb Mystery (Psychological Thriller / Paranormal / Clue-based)**
   - `00:00 - 00:04`: Anomaly Hook (Subtle but deeply disturbing detail).
   - `00:04 - 00:20`: Clue Stacking (3 rapid, escalating anomalies).
   - `00:20 - 00:40`: The Trap / Investigation (Slowing cadence, heightened suspense).
   - `00:40 - 00:55`: False Relief (Apparent safe explanation).
   - `00:55 - 01:10`: Chilling Reveal (Final second revelation overturning safety).
3. **Template 3: The Moral Dilemma / Dual POV (Family Drama / Toxic Workplace / AITA)**
   - `00:00 - 00:04`: Polarizing Question (Incite instant moral division).
   - `00:04 - 00:25`: POV A (Victim perspective, builds empathy).
   - `00:25 - 00:45`: POV B / Grey Area (Contradictory evidence or selfish motive exposed).
   - `00:45 - 01:00`: The Breaking Point (Irreversible conflict).
   - `01:00 - 01:10`: Engagement Verdict (Call-to-action asking viewers to judge).

Template windows are **soft** targets (reported as warnings, ±4 s tolerance). The hard timing
rules are the word-count gate and the hook-length gate in §7.1 / §11.

### 5.2 Four Hook Taxonomies (Breaking Bad Cold Open - Zero Clickbait)
- **Option 1: The "Pink Teddy Bear" Hook:** Extracts a bizarre, out-of-context object or metric from the climax and places it at second 0 (e.g., *"My water bill went up by \$12.50 this month, and that's why the FBI is currently digging up my backyard."*).
- **Option 2: The Out-of-Context Confession:** Highlights the narrator's most ruthless decision without initial justification.
- **Option 3: The Contradiction Hook:** Juxtaposes two diametrically opposed factual truths in one sentence.
- **Option 4: The Calm Before the Storm:** Depicts serene normality paired with a strict countdown to disaster.
- **Payoff Binding Rule:** Every Beat 1 hook must declare `hook_payoff_beat_id` pointing to Beat 4 or Beat 5. If the script fails to resolve the opening anomaly by second 55, it receives an automatic `FAIL` from the audit gate.

### 5.3 Anti-Repetition Rule
The engine strictly prohibits utilizing the same `pacing_template` or `hook_option` across two consecutive episodes of the same series.

---

## 6. Cultural Adaptation & Anti-AI Slop Rules

### 6.1 US Archetype & Socioeconomic Mapping Matrix
When adapting foreign texts (Chinese web novels, Indian family sagas, Korean webtoons) for Tier-1 US audiences:

| Source Concept | US Cultural Equivalent | Narrative Mechanics |
|---|---|---|
| Martial Arts Sect / Ancient Clan | Ivy League Fraternity / Secret Society / Mafia Syndicate / Mega-Corp | Replace "Martial Arts Manual" with "Proprietary Patent" or "Leaked Deposition". |
| Caste / Social Hierarchy (Môn đăng hộ đối) | Old Money (Hamptons / Beverly Hills) vs. Blue-Collar / Trailer Park | Class contempt framed around country club gatekeeping and trust funds. |
| Dowry / Bride Price (Sính lễ) | Prenup Agreement / Student Loan Debt Repayment Condition | Replaced with punitive legal contracts or financial blackmail. |
| Imperial Harem In-Fighting | Corporate Boardroom Succession (HBO's *Succession* style) or High School Queen Bee | Replace empresses with board members, first wives, or executive mistresses. |
| Overbearing CEO (Tổng tài bá đạo) | Billionaire Tech Founder / VP at Private Equity Firm | Eliminates cartoonish dominance; grounded in NDAs, buyout threats, and corporate power. |
| Blind Filial Piety | Legally binding co-signed mortgage or child custody leverage | Replaces passive suffering with an inescapable legal/financial trap. |

### 6.2 Anti-AI Slop & Spoken Cadence Engineering
1. **Mandatory Contractions:** Must use *I'm, don't, can't, she's, they've, wasn't, wouldn't*. Prohibit formal uncontracted forms.
2. **Show, Don't Tell:** Reject literary translations (*"He sneered coldly"*, *"You are courting death"*). Replace with conversational actions (*"He laughed right in my face and told me to get lost"*).
3. **Conversational Discourse Markers:** Enable natural spoken hooks: *Look,, Honestly,, So get this,, The crazy part is...*
4. **Slang Whitelist (Max 2–3 per 70s script):** *gaslight, red flag, Karen, receipts, unhinged, crash out, toxic, ghosted*. Prohibit forced Gen-Z slang (*skibidi, rizz, no cap*).
5. **Few-Shot Style Transfer:** System Prompts must embed 3 verified transcripts from top-upvoted Reddit drama posts (`r/TrueOffMyChest`) to enforce natural cadence and punchy sentence structure.

---

## 7. Two-Tier Quality Gates (Quality Assurance System)

### 7.1 Tier 1: Deterministic Python Gates (Zero Token Cost)
Executes purely via regex and algorithmic parsing in Python. Any violation triggers immediate failure:
1. **Word Count & WPM Gate:**
   - Script target: **190–225 words** (calculated over combined `narration_text`).
   - Hard Reject: $< 185\text{ words}$ (fails 60s minimum) or $> 235\text{ words}$ (exceeds 75s pacing).
2. **Readability Gate:**
   - Flesch-Kincaid Grade Level must be $\le 6.5$ (Grade 6 comprehension).
   - Maximum sentence length: $\le 20\text{ words}$ per sentence.
3. **Forbidden Cliché & AI-ism Gate (Regex):**
   - Prohibited openings: `"Have you ever wondered"`, `"In a world where"`, `"Picture this"`, `"Once upon a time"`.
   - Prohibited AI transitions: `"Little did they know"`, `"A testament to"`, `"Sent shivers down my spine"`, `"Buckle up"`, `"Needless to say"`.
4. **Cultural & Metric Regex Gate (market-profile driven):**
   - Forbidden units/currencies come from `config/markets/<market>.yaml`. The `US` profile
     forbids: `km`, `kilometer`, `meter`, `Celsius`, `kg`, `Yuan`, `RMB`, `Rupees`, `VND`
     (word-boundary, case-insensitive, so `parameter` does not match `meter`).
   - Prohibits unconverted foreign honorifics: `oppa`, `gege`, `tiểu thư`, `trưởng lão`
     (list in `config/lexicons/honorifics.txt`, NFC-normalized before matching).
   - Prohibits any CJK character and any `character_registry[].original_name` in narration.

### 7.2 Tier 2: LLM-as-a-Judge Audit Gate (`DeepSeek-V3`)
Evaluates the script across four 10-point dimensions:
1. **Hook & Payoff Alignment (Weight: 2x):** Did Beat 1 disrupt scrolling? Does Beat 4/5 resolve the anomaly? (0 points if unresolved clickbait).
2. **Pacing & Escalation:** Is there forward momentum? Are there static pauses exceeding 2 descriptive sentences?
3. **Logic & Knowledge Consistency:** Verified against `character_registry` and `secrets_ledger`. Do characters act only on information they know?
4. **Retention / Comment Trigger:** Does the ending provoke outrage, moral debate, or an irresistible open loop?

*Evaluation Routing:*
- **Overall Score $\ge 8.0$:** `PASS` $\rightarrow$ Promote to `out/<series_id>/`.
- **Overall Score $5.0 - 7.9$ (Targeted Rewrite):** Audit Judge outputs a structured JSON specifying exact `failed_beat_ids` and instructions. The Writer Agent modifies **only** the flagged beats (Max 2 rewrite cycles).
- **Overall Score $< 5.0$ (Hard Reject):** Flawed source material $\rightarrow$ Discard chunk immediately.

---

## 8. Circuit Breakers & Resilience Policies

```json
{
  "circuit_breaker_config": {
    "episode_limits": {
      "max_rewrite_attempts": 2,
      "max_total_tokens": 35000,
      "hard_cost_cap_usd": 0.02,
      "timeout_seconds": 90
    },
    "failure_handling_policy": {
      "if_standalone_or_ep1": "HARD_REJECT",
      "if_mid_series_score_above_7": "FORCED_PASS_WITH_WARNING",
      "if_mid_series_score_below_7": "COMPRESS_TO_STATE_AND_MERGE_NEXT_CHUNK"
    },
    "series_kill_switch": {
      "max_consecutive_failed_chunks": 2,
      "min_acceptable_series_avg_score": 6.5,
      "action_on_trigger": "BLACKLIST_NOVEL_SOURCE"
    },
    "global_daily_safety": {
      "max_daily_api_spend_usd": 5.00,
      "alert_webhook": "https://webhook.site/..."
    }
  }
}
```

### 8.1 Mid-Series Fast-Forward Fallback Policy
For ongoing serials where Episode $N$ ($N > 1$) fails after 2 rewrite attempts:
- If score is **7.0–7.9**: Execute `FORCED_PASS_WITH_WARNING` to maintain serial continuity.
- If score is **$< 7.0$**: Do not break the series. Trigger **Fast-Forward Merge**:
  1. Abort generating Episode $N$ as an independent video.
  2. Condense the essential plot developments into a 2-sentence summary (10s voiceover).
  3. Inject summary into `episode_handover.json` under `required_resolution_in_3_to_8s`.
  4. Advance `narrative_cursor` and merge the next chapter chunk.

### 8.2 Consecutive-Failure Series Blacklist
If a long novel fails on `max_consecutive_failed_chunks` (default 2) consecutive chunks, or its
running average score drops below `min_acceptable_series_avg_score` after ≥3 audited chunks,
terminate ingestion for that novel and add the source hash to the global blacklist.
"Failed" means a chunk decision of `REJECT` or `FAST_FORWARD_MERGE`.

`HARD_REJECT` never deletes anything: rejected drafts, audits and decisions stay in `run/`
as evidence; only `out/` is withheld.

---

## 9. File System Layout & Provenance Tracking

Attempt artifacts are keyed by **chunk** (a chunk may be fast-forwarded and never become an
episode). Episode numbers are assigned only on `PASS` / `FORCED_PASS_WITH_WARNING`, always
3-digit (`ep_001`). Every artifact is written atomically (temp file + rename) and carries an
`inputs_sha256` map of the artifacts it was derived from.

```
run/<series_id>/
├── 00_source_raw.json           # Normalized text, paragraphs[], URL/path, sha256, rights_status
├── 01_topology_meta.json        # Stream classification, language, genre, chunk boundaries
├── 02_series_bible.json         # Static character registry & localized glossary
├── series_state.json            # Current compiled state (§3.2); pointer to latest snapshot
├── PROGRESS.md                  # Human-readable resume status (generated by `vsp status`)
├── chunks/
│   └── chunk_001.json           # §3.1 chunk metadata
├── work/chunk_001/
│   ├── beats.json               # Stage 2 beats with source paragraph citations
│   ├── draft.json               # Writer output, with pipeline-computed metadata/timestamps
│   ├── gates_0.json             # Tier 1 receipt for draft (bound to draft sha256)
│   ├── audit_0.json             # Tier 2 scorecard for draft
│   ├── rewrite_1.json           # Targeted rewrite (only if needed), then gates_1/audit_1 ...
│   ├── fast_forward.json        # 2-sentence summary (only on FAST_FORWARD_MERGE)
│   └── decision.json            # Terminal routing outcome + episode_number if assigned
├── episodes/
│   └── ep_001_state.json        # Immutable state snapshot after ep_001
├── llm_calls.jsonl              # One line per LLM call: role, model, prompt hash, tokens, cost, latency
└── raw_llm/                     # Verbatim LLM responses, referenced from llm_calls.jsonl

state/                           # Global, cross-series
├── blacklist.json               # Blacklisted source sha256 + reason + date
├── spend_ledger.jsonl           # Daily spend (local date) across all series
└── llm_cache/                   # Response cache keyed by sha256(model+messages+params)

feedback/
├── review_ledger.jsonl          # Human verdicts (append-only)
└── hook_library.json            # Approved hooks, rebuilt from the ledger

out/<series_id>/
├── ep_001.json                  # Validated, release-ready production script (§3.3)
├── ep_002.json
└── manifest.json                # Series manifest, content hashes, gate receipts
```

---

## 10. End-to-End Implementation Plan (for the Coding Agent)

Read §11 before coding: it resolves every ambiguity in §1–§9 and **overrides them** where they
conflict. Build milestone by milestone; do not start a milestone until the previous exit
criteria pass.

### 10.1 Project Setup
- **Location:** a new standalone repository, e.g. `D:\Projects\viral-script-pipeline\`. Do
  not build inside `sat-extraction-pipeline`; it only provides the design philosophy
  (deterministic tools, hash-bound artifacts, evidence never deleted).
- **Stack:** Python ≥ 3.11, `pydantic` v2 (schemas), `httpx` (LLM + Reddit HTTP), `PyYAML`
  (config), `textstat` (Flesch-Kincaid), `pytest`. CLI via stdlib `argparse`. Pin exact
  versions in `requirements.txt` at install time; no open ranges. No other runtime deps
  without a stated reason.
- **Secrets:** API keys only from environment variables (`VSP_WRITER_API_KEY`,
  `VSP_JUDGE_API_KEY`, optional `VSP_ALERT_WEBHOOK`). `.env` is gitignored and never logged.
- **Gitignore:** `run/`, `out/`, `state/`, `feedback/`, `data/`, `inbox/`, `.env`.

```
viral-script-pipeline/
├── pyproject.toml / requirements.txt
├── config/
│   ├── pipeline.yaml            # models, endpoints, prices, WPM, thresholds, breakers (§8)
│   ├── markets/us.yaml          # currency, unit system, forbidden units, slang whitelist
│   ├── voices.yaml              # tts_voice_id pool by gender/age/role (placeholders OK)
│   └── lexicons/                # cliches.txt, ai_isms.txt, honorifics.txt,
│                                # uncontracted.txt, banned_slang.txt, slang_whitelist.txt
├── prompts/                     # versioned templates; sha256 of each goes into receipts
│   ├── topology_classifier.md   beat_extractor.md   script_writer.md
│   ├── beat_rewriter.md         audit_judge.md      state_updater.md
│   └── fast_forward_summarizer.md
├── assets/style_examples/       # 3 owner-supplied transcripts (§6.2.5)
├── src/vsp/
│   ├── cli.py  config.py  orchestrator.py
│   ├── schemas/                 # pydantic models for every JSON artifact in §3 and §9
│   ├── io/                      # atomic_write, sha256, paths, artifact freshness check
│   ├── ingest/                  # file_loader, reddit_loader, normalize
│   ├── topology/                # heuristics, llm_classifier
│   ├── chunking/                # headings (multi-language), boundary_chunker, clean_cut
│   ├── llm/                     # client, json_parse_repair, cache, budget, fake_client
│   ├── extract/                 # beats, bible_merge
│   ├── draft/                   # router, writer, rewriter, splice, timing
│   ├── gates/tier1/             # one module per check in §10.6
│   ├── gates/tier2/             # judge, scoring
│   ├── state/                   # compile_state, evolve, resets, fast_forward
│   ├── breakers/                # policies, ledger, blacklist, alert
│   ├── export/                  # package, manifest, verify
│   └── feedback/                # review, hook_library
└── tests/  unit/  e2e/  fixtures/  (live tests marked @pytest.mark.live, skipped by default)
```

### 10.2 CLI Contract
All commands are idempotent and resumable. A stage is skipped when its output exists, parses,
and its `inputs_sha256` matches current inputs; otherwise it is recomputed. `--force` reruns.

| Command | Purpose | LLM cost |
|---|---|---|
| `vsp ingest <path\|reddit-url> [--series-id] [--topology T] [--language L] [--rights R] [--complete]` | Stage 0 | 0 |
| `vsp chunk <series_id>` | Topology + Stage 1 | 0–1 call |
| `vsp extract <series_id> [--chunk N]` | Stage 2 | 1 call/chunk |
| `vsp draft <series_id> --chunk N` | Stage 3 | 1 call |
| `vsp gate <series_id> --chunk N` / `vsp gate --file script.json` | Tier 1 only | 0 |
| `vsp audit <series_id> --chunk N` | Tier 2 + rewrite loop + routing | ≤ 5 calls |
| `vsp export <series_id>` | Stage 6 | 0 |
| `vsp verify <series_id>` | Re-validate schema + hashes of `out/` | 0 |
| `vsp run <path\|--inbox DIR>` | Full pipeline for each source, sequential | all |
| `vsp status [series_id]` | Progress, decisions, spend; regenerates `PROGRESS.md` | 0 |
| `vsp review <series_id> <ep> --verdict approve\|reject [--tags t1,t2] [--note]` | Human feedback | 0 |
| `vsp hooks rebuild` | Rebuild `hook_library.json` from review ledger | 0 |

Exit codes: `0` success, `2` deterministic refusal (gate/blacklist/schema), `3` budget/
breaker stop, `1` unexpected error. Every refusal prints stage, check ID, measured value,
threshold and the artifact path.

### 10.3 Stage 0 — Ingestion
- Inputs: `.txt`, `.md`, `.html` (tags stripped), `.json` (saved Reddit thread), or a single
  Reddit post URL fetched via `https://www.reddit.com/comments/<id>.json` with a descriptive
  `User-Agent`, one request, no crawling. Take `title` + `selftext`; ignore comments.
- Normalize: Unicode NFC, unify line endings, collapse spaces, keep paragraph breaks. Store
  `paragraphs[]` with stable integer IDs; all later citations use these IDs.
- `source_sha256` = sha256 of normalized UTF-8 text; also store `raw_bytes_sha256`.
- Refuse (exit 2) if the hash is in `state/blacklist.json`.
- `rights_status` ∈ `public_domain | licensed | owner_permission | unknown` (default
  `unknown`); carried into the manifest. The pipeline does not judge rights; it records them.
- `series_id` = `<slug(title)>_<first 8 hex of sha256>` unless `--series-id` is given.

### 10.4 Topology & Stage 1 — Chunking
- **Size unit:** `word_equiv`. Whitespace tokens for space-delimited languages; for `zh`/`ja`
  CJK characters ÷ 1.6; per-language factors in `pipeline.yaml` (e.g. `vi: 0.7` because
  Vietnamese tokens are syllables). All 3,500 / 5,000–7,000 / 300 thresholds use `word_equiv`.
- **Classification:** if `--topology` given, use it. Else if `word_equiv < 3500` and ≤ 1
  heading → `SINGLE_SHOT` with no LLM call. Else one judge-model call with the heading list,
  first ~1,500 words and 3 sampled passages, returning `{topology, confidence,
  source_language, genre, notes}`. `confidence < 0.6` → refuse with "pass --topology".
- **Heading detection** (regex per language, in `chunking/headings.py`): `Chapter N`,
  `Part N`, `Case N`, `Chương N`, `Phần N`, `第N章`, `第N回`, roman numerals, and scene breaks
  (`***`, `---`, `◇◇◇`, `* * *`).
- **Per stream:**
  - `SINGLE_SHOT`: 1 chunk, `is_hard_boundary_end=true`, reset `FULL_FLUSH`. 1 episode.
  - `ANTHOLOGY`: split at story headings; stories > 7,000 split by sliding arc; only the
    final part of a story has `is_hard_boundary_end=true`; reset `FULL_RESET`.
  - `EPISODIC_SERIES`: split at case headings, never across a case; sliding arc inside a
    case; last part of a case is hard boundary; reset `KEEP_MAIN_CAST_FLUSH_PLOT`.
  - `CONTINUOUS_SERIAL`: sliding arc, target 6,000 (min 5,000, max 7,000); reset `PERSIST`;
    the final chunk is a hard boundary only if `--complete` was passed.
- **Clean cut:** candidate cuts are paragraph ends within ±300 of the target. Preference:
  chapter heading > scene break > paragraph end whose paragraph has balanced quotes (`""`,
  `“”`, `「」`, `«»`) and the next paragraph does not open with dialogue. If none qualifies,
  cut at the nearest paragraph end and set `clean_cut=false` (warning, recorded).
- One chunk ⇒ at most one episode. A 100-page chapter (~25–35k words) yields ~5 chunks.

### 10.5 Stage 2 — Beat Extraction, Bible, State
- One writer-model call per chunk, input = raw chunk text + compiled state (§10.8). Output:
  - `beats[]`: `{beat_id (series-monotonic), summary_en, characters[] (original names),
    source_paragraph_ids[], tension 1–5, is_twist, is_cliffhanger_candidate,
    bizarre_detail (nullable)}`
  - `new_entities[]`: `{original_name, proposed_us_name, role, gender, main_cast: bool}`
  - `glossary_additions{}` using the §6.1 archetype matrix (embedded in the prompt).
  - `genre_tags[]` (for template routing).
- Deterministic validation (refuse on failure, one repair retry): every
  `source_paragraph_id` exists in the chunk; every beat character is in the registry or
  `new_entities`; ≥ 5 beats per chunk.
- `bible_merge` (deterministic): `us_name` is assigned once and **frozen** for the series;
  name collisions refused; `tts_voice_id` assigned deterministically from `voices.yaml` by
  gender/role, round-robin, never reused within a series.

### 10.6 Stage 3 — Drafting & Stage 4 Tier 1 Gates
**Router (deterministic):** genre tags → template priority list (revenge/betrayal/action →
T1; mystery/thriller/paranormal/horror → T2; family/workplace/AITA → T3). Remove anything in
`forbidden_for_current_ep`, take the first remaining. Allowed hooks = all 4 minus the
previous episode's hook. The writer picks one hook from the allowed set and must say which.

**Writer call** input: compiled state, chunk beats, verbatim source paragraphs cited by
twist/cliffhanger beats (not the full chunk), the template's 5 block definitions, allowed
hooks, §6 rules, 3 style examples, up to 3 approved hooks for the chosen option from
`hook_library.json`, `is_hard_boundary_end`, and the handover bridge. Output: 5 beats
(`narration_text`, `tts_emotion_prompt`, `screen_text_overlay`, `b_roll_vibe`, `sfx_cue`,
`bgm_directive`, `block_type`), `hook_option`, `hook_payoff_beat_id`, `call_to_action`.

**Pipeline-computed fields** (never trusted from the LLM): `total_word_count`,
`estimated_duration_sec`, every `timestamp_est` (cumulative words at configured WPM),
`pacing_template`, `provenance`, `series_id`, `episode_number`, `target_market`.

**Tier 1 checks** — each emits `{id, pass, measured, threshold, beat_ids}` into
`gates_<k>.json`, bound to the script's sha256:

| ID | Check | Rule |
|---|---|---|
| T1-01 | Schema | Exactly 5 beats, ids 1–5, block types match template, no empty fields |
| T1-02 | Word count | Spoken words (5 narrations + `outro_narration`) in [185, 235]; warning outside [190, 225] |
| T1-03 | Readability | `textstat` Flesch-Kincaid grade ≤ 6.5 on spoken text |
| T1-04 | Sentence length | Every sentence ≤ 20 words |
| T1-05 | Hook length | Beat 1 ≤ 20 words |
| T1-06 | Clichés / AI-isms | No match from `cliches.txt` / `ai_isms.txt` (case-insensitive, quotes normalized) |
| T1-07 | Market units | No forbidden unit/currency from the market profile |
| T1-08 | Localization | No honorific from list, no CJK chars, no registry `original_name` |
| T1-09 | Contractions | No phrase from `uncontracted.txt` ("I am", "do not", "cannot", "was not", …) |
| T1-10 | Slang | Whitelist hits ≤ 3; `banned_slang.txt` hits = 0 |
| T1-11 | Anti-repetition | Template and hook not in `forbidden_for_current_ep` |
| T1-12 | Payoff binding | `hook_payoff_beat_id` ∈ {4, 5} |
| T1-13 | Hard-boundary firewall | If `is_hard_boundary_end`, CTA type ≠ `cliffhanger_subscription` |
| T1-14 | Template timing | Beat windows vs §5.1 ±4 s — **warning only** |

Tier 1 failures map to beat IDs where possible (global failures like T1-02 target all
beats with a "add/cut N words" instruction) and consume one rewrite attempt. Tier 2 runs
only after Tier 1 passes, so no tokens are spent judging a script that is already invalid.

### 10.7 Tier 2 Audit, Rewrite Loop & Routing
- **Judge call** (judge model) input: script, compiled state, chunk beat summaries. It never
  sees the writer's prompt or reasoning. Output: `{scores: {hook_payoff, pacing, logic,
  retention} (0–10 each), payoff_resolved: bool, knowledge_violations[],
  failed_beat_ids[], rewrite_instructions: [{beat_id, issue, instruction}]}`.
- **Overall score computed by the pipeline**, not the judge:
  `(2·hook_payoff + pacing + logic + retention) / 5`. If `payoff_resolved=false`,
  `hook_payoff` is forced to 0 and the verdict cannot be PASS.
- **Rewriter call** receives the full script but returns only the flagged beats; `splice`
  replaces them and asserts every unflagged beat is byte-identical. Then Tier 1 → Tier 2
  again. Max 2 rewrites (Tier 1 or Tier 2 triggered) per chunk.
- **Decision table** (written to `decision.json`, final):

| Condition | Decision |
|---|---|
| Tier 1 pass and score ≥ 8.0 | `PASS` |
| 5.0 ≤ score < 8.0, or Tier 1 fail, with rewrites left | rewrite |
| Exhausted or score < 5.0, and (standalone, episode 1, or hard-boundary chunk) | `REJECT` |
| Exhausted, mid-series, Tier 1 pass, score ≥ 7.0 | `FORCED_PASS_WITH_WARNING` |
| Exhausted or score < 5.0, mid-series, otherwise | `FAST_FORWARD_MERGE` (§8.1) |
| Episode token/cost/time cap hit | `BUDGET_STOP` (not a source failure; resumable) |

A Tier 1 failure is never force-passed.

### 10.8 Stage 5 — State Evolution, Resets, Breakers
- **Compiled state** (§3.2) is what prompts receive. Budget ≤ 600 tokens (estimate chars/4);
  include main cast + characters active in the last 2 episodes only.
- After `PASS`/`FORCED_PASS`: one small writer-model call (`state_updater.md`) proposes
  `{rolling_summary, secrets_ledger changes, character status changes, handover_bridge}`.
  Deterministic validation: ledger only adds facts or flips booleans of existing facts;
  `narrative_cursor` strictly advances; `anti_repetition_tracker` is set by code, not LLM.
  Write `episodes/ep_NNN_state.json` (immutable) and update `series_state.json`.
- **Hard-boundary resets** (code): `FULL_FLUSH`/`FULL_RESET` clear everything;
  `KEEP_MAIN_CAST_FLUSH_PLOT` keeps `main_cast` registry entries + voice IDs and clears
  secrets, summary, handover and non-main characters; `PERSIST` changes nothing.
- **Fast-forward:** one call condenses the chunk into ≤ 2 sentences (≤ 30 words), stored in
  `fast_forward.json` and injected into `handover_bridge.required_resolution_in_3_to_8s`;
  cursor advances; no episode number consumed.
- **Budget middleware** wraps every LLM call: pre-call estimate (prompt tokens + max_tokens
  × configured price) must fit the remaining episode budget (35k tokens, $0.02, 90 s wall
  clock) and the daily cap ($5.00, `state/spend_ledger.jsonl`, local date). Actual usage
  comes from the API `usage` field and is appended to `llm_calls.jsonl` and the spend ledger
  before anything else happens. Cache hits cost 0. Daily cap reached → stop `vsp run`
  cleanly, POST to `VSP_ALERT_WEBHOOK` if set.
- **LLM client:** OpenAI-compatible `/chat/completions`, JSON mode when supported,
  temperature from config (writer ~0.8, judge 0.0), per-call timeout, 2 retries with backoff
  on 429/5xx only. Invalid JSON → one repair retry (counted against budget), then refusal.
- **Blacklist:** after each decision, apply §8.2; on trigger write
  `state/blacklist.json` and stop the series.

### 10.9 Stage 6 — Export, Manifest, Feedback
- `export` writes only `PASS`/`FORCED_PASS` episodes to `out/<series_id>/ep_NNN.json`,
  validated against the §3.3 pydantic model (no extra fields), atomically.
- `manifest.json`: series_id, source provenance + `rights_status`, pipeline version, sha256
  of config and of each prompt file, model IDs, and per episode: file, sha256, decision,
  Tier 1 receipt sha256, Tier 2 overall + dimension scores, warnings, rewrite count, cost.
- `verify` re-hashes every `out/` file against the manifest and re-validates schema; any
  mismatch exits 2. `out/` is never hand-edited: fix inputs and re-export.
- **Feedback loop (MVP):** `vsp review` appends `{series_id, ep, script_sha256, verdict,
  tags, note, reviewer, timestamp}` to `feedback/review_ledger.jsonl`. `vsp hooks rebuild`
  collects Beat 1 of approved episodes into `hook_library.json`, grouped by hook option
  (newest first, max 10 per option). The writer prompt includes up to 3 of these per draft.
  This is the "learn successful hooks" seed; traffic-metric learning comes later.

### 10.10 Testing Strategy
- **Offline by default.** `FakeLLMClient` returns fixture JSON keyed by prompt name and
  call order, and reports synthetic `usage`. All unit and e2e tests use it; no network.
- **Unit:** every Tier 1 check with pass/fail fixtures (too short, too long, cliché,
  `kilometer`, `parameter` must pass, honorific, CJK leak, `I am`, 4 slang words, repeated
  template, payoff beat 3, cliffhanger on hard boundary); chunker on synthetic English,
  Vietnamese and Chinese texts (headings, scene breaks, open-quote avoidance, ±300 window,
  case-boundary firewall); scoring formula; splice byte-identity; bible name freezing;
  reset modes; budget pre-check and daily cap; artifact freshness / resume.
- **E2E (fake LLM):** one test per decision-table row, plus: 2 consecutive failures →
  blacklist; 3-episode serial where ep 2's handover contains ep 1's cliffhanger; resume after
  a simulated crash recomputes nothing already fresh; `verify` detects a tampered `out/` file.
- **Live (`-m live`, needs keys):** one Reddit story and one 3-chunk serial; used for
  acceptance in §12, never in CI.

### 10.11 Milestones (maps to the proposal's 4-week MVP)

| Milestone | Scope | Exit criteria |
|---|---|---|
| **M1 — Week 1: Deterministic core** | Repo, config, schemas, io, ingest (file + Reddit URL), topology heuristics, chunker, all Tier 1 checks, `ingest/chunk/gate/status` CLI | `pytest` green offline; `vsp gate --file` rejects every bad fixture with the right check ID; chunking a 30k-word fixture yields 5k–7k chunks with no cross-boundary mix |
| **M2 — Week 2: LLM path** | LLM client, cache, budget, fake client, prompts, extraction + bible, router, writer, Tier 2 judge, rewrite loop, decision table for standalone sources | All decision-table e2e tests green; live run on 10 Reddit stories reaches a terminal decision for each (acceptance A2) |
| **M3 — Week 3: Series** | State compile/evolve, resets, fast-forward, breakers, blacklist, export, manifest, verify | Serial e2e tests green; live 100-page serial produces ≥ 3 consecutive episodes with handover (A3); `vsp verify` green |
| **M4 — Week 4: Automation & feedback** | `vsp run --inbox`, `review`, `hooks rebuild`, prompt tuning from first reviews, `PROGRESS.md` | Unattended inbox run meets A5–A7 |

---

## 11. Resolved Decisions (binding; override §1–§9 on conflict)

1. **WPM = 185** (config). 185 words = 60 s, 235 words ≈ 76 s, target 190–225 ≈ 62–73 s.
   `estimated_duration_sec = round(spoken_words / 185 × 60)`. Spoken words include
   `outro_narration`.
2. **Hook window:** the 0–3 s window in §5.1 is unrealistic (the §3.3 example hook is 20
   words ≈ 6.5 s). Hard rule is T1-05 (≤ 20 words); template windows are warnings (T1-14).
3. **Payoff binding:** enforced as `hook_payoff_beat_id ∈ {4,5}` (T1-12) plus the judge's
   `payoff_resolved`. The "by second 55" wording is dropped because Beat 5 starts at 55–60 s.
4. **Episode 1 Beat 2** has no previous cliffhanger; it is the fast-context setup.
   For episode N > 1 it must resolve `required_resolution_in_3_to_8s` (judged in `logic`).
5. **CTA type enum:** `cliffhanger_subscription | engagement_verdict | loop_back |
   story_complete`. Template 3 uses `engagement_verdict`.
6. **Single-shot** sources produce exactly 1 episode in the MVP (the "1–2" option is deferred).
7. **Rejects never delete** evidence (§8.2). **Series failure** = 2 consecutive
   `REJECT`/`FAST_FORWARD_MERGE` decisions.
8. **Target market** is `US` / `en-US` only, but every market-specific value (units,
   currency, slang, honorific handling) lives in `config/markets/us.yaml`, so adding AU/CA
   is configuration plus tests, not code.
9. **Cost cap realism:** $0.02 / 35k tokens per episode is achievable only with cheap
   pricing and the "beats + cited excerpts" writer input of §10.6. A 7k-word Vietnamese
   or Chinese chunk alone can be 10–15k tokens. Prices are config; the first live runs must
   report measured cost per exported episode, and the owner decides whether to retune caps.

---

## 12. MVP Acceptance Criteria (Definition of Done)

| ID | Criterion | Evidence |
|---|---|---|
| A1 | `pytest` passes offline, including every decision-table e2e test | test output |
| A2 | 10 Reddit stories via `vsp run --inbox` each reach a terminal decision; ≥ 5 exported | `vsp status`, `decision.json` files |
| A3 | One ~100-page serial yields ≥ 3 consecutive exported episodes; ep N+1 Beat 2 resolves ep N's cliffhanger | `out/` files, state snapshots, judge `logic` scores |
| A4 | Every `out/` file validates against §3.3 and `vsp verify` passes | verify output, manifest |
| A5 | Measured mean cost per exported episode reported; daily cap stops a run in a test | `spend_ledger.jsonl`, e2e test |
| A6 | One unattended `vsp run --inbox` produces ≥ 5 exported scripts with no manual step | run log, `out/` |
| A7 | Owner reviews 5 exported scripts via `vsp review`; ≥ 3 approved | `review_ledger.jsonl` |

## 13. Inputs Required from the Owner

| Input | Needed by | Blocking? |
|---|---|---|
| API provider(s), keys, model IDs, price per 1M input/output tokens | M2 | Yes for live runs; fake client covers tests |
| 3 style-example transcripts for `assets/style_examples/` (§6.2.5) | M2 | Yes for live drafting |
| 10 Reddit story URLs/files and one ~100-page serial, with `rights_status` | M2/M3 | Yes for A2/A3 |
| Voice ID pool (`voices.yaml`) | M2 | No, placeholders allowed |
| Alert webhook URL | M3 | No |

Source rights note: adapting copyrighted novels/posts for monetized videos is a legal risk
the proposal itself raises for movie reviews. The MVP records `rights_status`; prefer
public-domain (e.g. Sherlock Holmes for `EPISODIC_SERIES`) or permitted sources for testing.


---

## 14. Implementation Audit (2026-10-30) & Fix Plan

**Audited:** `C:\Users\MHC\Desktop\viral-script-pipeline` (written 2026-10-26 22:01–22:08).
**Verdict: NOT up to standard.** The code is real (not stubs) and `pytest` is green (26 passed,
2 skipped), but resume can export an unaudited script, the episode budget is not enforced,
several gates can be bypassed by omission, the chunker misclassifies boundaries, the live tests
are empty, and no acceptance criterion past A1 was attempted (`run/` holds 2 ingested + chunked
sources with 0 LLM calls; no `work/`, no `out/`). The README's claim "Implements §1–§13 end to
end" is false.

Every item below is binding for the fixing agent. Each has an ID; reference it in commit
messages / the fix report. "Probe" = reproduced by the auditor with a script.

### 14.1 Critical — gate integrity and correctness

| ID | Defect (evidence) | Required fix | Proof test |
|---|---|---|---|
| F1 | **Resume exports an unaudited script.** `do_extract` binds `beats.json` to the hash of the *whole current* `series_state.json`; after any `do_evolve`, re-running re-extracts earlier chunks (probe P7: LLM re-called), which changes `beats.json` → `do_draft` regenerates `draft.json`. `do_audit` returns an existing `decision.json` without checking its `inputs_sha256`, and `do_export` exports `draft.json`/`rewrite_N.json` as found. | Bind each chunk's extraction to the **state snapshot it was extracted from** (store `state_in.json` per chunk; never re-key to later state). `decision.json` must be bound to the exact script sha it judged; `do_audit` must recompute when stale. `do_export` must refuse (exit 2) unless sha(script) == `decision.inputs_sha256.script` == `gates_N.script_sha256`. | e2e: full 2-chunk serial run, then rerun `vsp run`: zero LLM calls, byte-identical `out/`. Tamper `draft.json` after decision → export refuses. |
| F2 | **Episode budget not enforced.** `get_llm` builds a new `LLMClient`+`LLMBudget` per stage, so the 35k-token/$0.02 cap resets at extract, draft, audit and evolve. `timeout_seconds` is per HTTP call, not episode wall-clock. HTTP retries are not re-checked against budget. | One budget per chunk spanning extract→draft→audit→rewrites→fast-forward→evolve, persisted in `work/chunk_NNN/budget.json` so resume counts prior spend. Enforce 90 s episode wall-clock. | Unit: 4 stage calls whose sum exceeds the cap → 4th refused. Resume after crash keeps prior spend. |
| F3 | **`BUDGET_STOP` never produced** (probe P10). `BudgetExceeded` escapes; `cmd_audit` maps it to exit 1 via the generic handler; `cmd_run` aborts the whole inbox on an *episode* cap. | Episode cap → write `decision.json` = `BUDGET_STOP` (non-terminal, resumable, **not** counted as a series failure), continue with the next source. Daily cap → stop `vsp run`, exit 3, alert. All refusals exit 2, budget exit 3. | e2e: episode cap → BUDGET_STOP + next source processed; daily cap → run stops with exit 3; resume after raising cap completes the chunk. |
| F4 | **Gates bypassed by omission.** `finalize_script` defaults `hook_payoff_beat_id` to 4 when the writer omits it (probe P5); `payoff_resolved` defaults to `True` when the judge omits it; `hook_option` may be `""` or any string and still passes Tier 1 and schema (probe P6); judge scores are not range-checked; `failed_beat_ids` outside 1–5 raises `ValueError` → exit 1. | Pydantic-validate **every** LLM output (extractor, writer, rewriter, judge, state_updater, fast-forward, classifier) with no defaults for required fields; invalid → one repair retry → refusal (exit 2). Enums for `hook_option`, `pacing_template`, `block_type`. Scores ∈ [0,10]. T1-01 checks enums and that **all** beat fields are non-empty. | Unit per omitted/invalid field → refusal or T1 fail, never pass. |
| F5 | **State reset skipped on non-pass hard boundaries** (probe P11). `apply_reset` runs only inside `do_evolve` after PASS. A REJECTed anthology story or case leaks its characters/secrets/handover into the next one. | Apply the chunk's `state_reset_instruction` after **any** terminal decision on a hard-boundary chunk (PASS, FORCED_PASS, REJECT). | e2e: anthology story 1 REJECT → story 2 compiled state has no story-1 entities. |
| F6 | **Boundary detection is wrong.** `HEADING_PATTERNS` include scene breaks (`* * *`, `---`) and markdown headings, so a scene break inside one case becomes a hard boundary with a state flush (probe P1: 1 case → 2 hard chunks). In `EPISODIC_SERIES`, every `Chapter N` is treated as a case boundary (probe P2: 1 case → 3). `is_case_heading` is defined but unused. | Three distinct classes: **story/case boundaries**, **chapter headings**, **scene breaks**. EPISODIC splits only on case headings; ANTHOLOGY on story headings (fallback: boundaries returned by the topology classifier); chapters and scene breaks are only preferred cut points. | Unit: P1 and P2 inputs → 1 chunk each, multiple-case input still splits. |
| F7 | **Chunk size rules.** Serial chunking passes `hard_stops=set()`, so chapter headings are never preferred cuts. Trailing chunks can be far below the 5,000 minimum (probe P4: `[6000, 1100]`). | Pass chapter heading positions as top-preference cuts. If the remainder < `sliding_min`, rebalance the last two chunks (even split at a clean cut) or merge if ≤ `sliding_max`. Same inside long anthology stories/cases. | Unit: 7,100-word serial → 2 chunks each ≥ 3,000 or 1 merged; headings chosen when inside the ±300 window. |
| F8 | **Language never detected.** Ingest defaults `language="en"`; heuristic classification hard-codes `source_language: "en"`; the classifier's `source_language` is ignored by the chunker. A Vietnamese/Chinese file without `--language` gets the wrong `word_equiv`. | Deterministic detection at ingest (CJK ratio → zh/ja/ko; Vietnamese diacritic ratio → vi; else en), stored in `00_source_raw.json`; `--language` overrides; classifier output recorded but code detection wins unless forced. | Unit: VI and ZH fixtures detected without flags; word_equiv uses the right factor. |

### 14.2 Major — spec conformance

| ID | Defect | Required fix |
|---|---|---|
| F9 | `do_draft` always assigns `hooks[0]` and tells the writer "you must use these", so a series only alternates Option_1/Option_2. Spec §10.6: writer picks from the allowed set. | Send all allowed hooks; validate the writer's pick ∈ allowed (T1-11); record it. Optional deterministic hint: least-recently-used hook in the series. |
| F10 | T1-10 counts whitelist slang and banned slang by substring (`blob.count`), so words containing a slang term are counted. | Word-boundary regex for multi-word and single-word terms; unit tests for false positives. |
| F11 | Compiled state: `_active_characters` is never written, so "main cast + characters active in last 2 episodes" is unimplemented; trimming halves the summary mid-word and cuts the registry to the first 2 entries arbitrarily. | Record active characters per episode from beats; deterministic trim order (drop non-main inactive first, then summary at sentence boundary); assert ≤ 600 tokens. |
| F12 | Manifest/verify: `tier1_sha256` stores the script sha, not the receipt sha; per-episode `cost` is the literal string `"see llm_calls.jsonl"`; `llm_calls.jsonl` has no `chunk_id`; the exported file changes `episode_number`, so receipts can't be re-bound; `verify` only checks file hash + schema. | Add `chunk_id`/`stage` to every `llm_calls.jsonl` line; manifest gets receipt file sha, audited-script sha, numeric cost/tokens per episode; `verify` re-checks gates + decision binding to the audited script (hash computed with `episode_number` normalized). |
| F13 | `evolve_state` increments `last_processed_chapter` per episode instead of per chapter; `story_arc_progress` is never updated; same in fast-forward. | Derive from chunk paragraph range vs chapter headings; progress = processed word_equiv / total. |
| F14 | Hook library keeps the first 10 approvals in ledger order (spec: newest first); repeated reviews of the same script aren't deduped; `vsp review` on a missing episode silently records an empty sha. | Newest first, dedupe by `script_sha256`, refuse review of a missing episode (exit 2). |
| F15 | `PROGRESS.md` is one status line. Spec §9/§10.2: resume status. | Per chunk: decision, score, rewrites, cost, next action; series totals; blacklist status. |

### 14.3 Tests — current suite gives false confidence

| ID | Defect | Required fix |
|---|---|---|
| T1 | `tests/live/test_live.py` bodies are docstrings only; with keys set they "pass" while doing nothing. | Real live tests: run the pipeline on the inbox via an injected real client and assert A2/A3 conditions; skipped without keys. |
| T2 | No offline e2e of the real path. `cmd_run` can't accept a fake client (probe P9); e2e tests hand-write `beats.json`/`draft.json`, so ingest→chunk→extract→draft→audit→evolve→export never runs together. | Injectable LLM factory (`--fake-fixtures DIR` or a module-level factory used by tests). E2E: `vsp run` on a Reddit-like file and a 3-chunk serial with fixture LLM responses, asserting `out/`, manifest, verify, and that ep N+1's writer prompt contains ep N's `pending_cliffhanger`. |
| T3 | Weak assertions: `test_decision_reject_standalone` accepts REJECT **or** FAST_FORWARD; `test_clean_cut_open_quote_avoidance` asserts `len >= 1`; resume test only re-reads `decision.json`; budget test covers one call. | Exact expected decisions; clean-cut test asserts the cut is not after an open quote and not before a dialogue line; resume test per F1; budget tests per F2/F3. |
| T4 | Missing tests: score < 5 at first audit (no rewrite), Tier 1 failure never force-passed, each reset mode, blacklist refusal at ingest, Reddit URL loader (mocked HTTP), invalid LLM outputs (F4), stale-artifact recomputation, CLI exit codes 0/1/2/3. | Add them. |
| T5 | The passing-script fixture is made of nonsense words (`Twas`, `ope`, `nought`) tuned to game Flesch-Kincaid; no realistic script is gate-tested. | Add ≥ 3 realistic, human-readable scripts (one per template) that pass all Tier 1 checks, plus realistic failing variants. |

### 14.4 Acceptance and owner inputs

| ID | Issue | Required action |
|---|---|---|
| X1 | `assets/style_examples/*.txt` were written by the agent (≈300 chars each). §6.2.5/§13 require 3 verified top-upvoted transcripts supplied by the owner. `inbox/` samples are also invented (one 170-word story, one 1.6 KB "serial"). | Rename to clearly marked placeholders (`PLACEHOLDER_` prefix + README note). Do not use them as acceptance evidence. Ship a realistic offline fixture set under `tests/fixtures/` instead. |
| X2 | A2–A7 never attempted; README claims completion. | Fix README to state exactly what is verified. A2–A7 require owner API keys and sources (§13); record them as **blocked on owner inputs** in `PROGRESS.md`/README, with the exact commands to run once keys exist. |
| X3 | No version control: the folder is inside the untracked Desktop git tree with no commits. | Do not run git commands on the Desktop repo. (Owner decides on a standalone repo.) |

### 14.5 Fixing-agent rules
1. Work only inside `C:\Users\MHC\Desktop\viral-script-pipeline`. No network installs beyond
   `requirements.txt`; add a dependency only with a written reason.
2. Fix in order F1–F8, then T2/T3/T4, then F9–F15, T1, T5, X1–X2. After each item run
   `py -m pytest tests -q`; never weaken or delete an existing assertion to get green,
   never skip a test that currently runs, never special-case test inputs in production code.
3. Do not mark anything done that is not demonstrated by a test or command output. Write
   `FIX_REPORT.md` at the repo root: per ID, files changed, test names proving it, and
   final `pytest` output. Items you cannot finish are listed as open with the reason.
4. Live acceptance (A2–A7) is out of reach without owner keys; do not fabricate LLM outputs
   in `run/` or `out/` to simulate it.


### 14.6 Round-2 audit (2026-10-30, after the first fix pass)

Independent probes confirmed F2–F15, T2–T5 and X1 as fixed (boundary classes, chunk
rebalancing, language detection, strict LLM validation, receipt-bound export, numeric cost,
tamper detection). `pytest`: 53 passed, 2 skipped. These defects remain or were introduced:

| ID | Defect (evidence) | Required fix | Proof test |
|---|---|---|---|
| R1 | **F1 only partly fixed: re-running a completed series calls the LLM again.** `do_draft` keys `draft.json` to `route_template(...)` computed from the **current** `series_state.json`. After `do_evolve`, the current state forbids that episode's template, so the routed template differs, the inputs mismatch, and the draft is regenerated (probe: replay of chunks 1 and 3 re-called the writer; a second `vsp run` on a finished Reddit story made 2 extra LLM calls and exited 2). With a real LLM the new draft makes the decision stale → re-audit → possible re-evolve/re-number. | Draft routing (template, allowed hooks, forbidden list, handover, is_ep1) must use the chunk's `state_in.json`, not the live state. A chunk with a fresh terminal decision must be skipped by `draft`, `audit` **and** `evolve`. | E2E: run a 3-chunk serial and a single-shot through `vsp run --fake-fixtures`, then run again with an **empty** fixture file: exit 0, zero new lines in `llm_calls.jsonl`, `series_state.json` and `out/` byte-identical. |
| R2 | **`do_evolve` is not idempotent.** It has no freshness check: every call re-invokes the state updater and overwrites `series_state.json` and `episodes/ep_NNN_state.json` (probe: evolve replay re-called the LLM for all 3 chunks). Re-evolving chunk 1 after chunk 3 would roll the series state back to chunk 1's handover. | Bind `ep_NNN_state.json` to the decision sha + `state_in` sha; if fresh, return it without an LLM call and without touching `series_state.json`. Refuse (exit 2) to evolve a chunk older than the series cursor unless `--force`. | Unit: evolve twice → one LLM call, identical files. Evolve chunk 1 after chunk 3 → no state change. |
| R3 | **Serial continues past a `BUDGET_STOP`.** In `cmd_run`, an episode-cap hit on chunk N does `continue`, so chunks N+1… of the same `CONTINUOUS_SERIAL`/case are drafted without chunk N's handover (probe: 3-chunk serial → `BUDGET_STOP` on chunks 1, 2 and 3). | On `BUDGET_STOP`, stop processing that series (all topologies except `ANTHOLOGY`, where the next story is independent) and move to the next source. `vsp status` shows the stopped chunk as the next action. | E2E: serial with chunk 1 over cap → only chunk 1 has a decision; next inbox source is processed; rerun with a raised cap completes chunks 1–3 in order. |
| R4 | Live tests count `BUDGET_STOP` as terminal (`TERMINAL` set in `tests/live/test_live.py`). Spec §10.7: it is resumable, not a terminal decision. | Remove it from `TERMINAL`; a live run that hits the cap fails A2 with a clear message. | Read-through. |

Rules from §14.5 still apply. Update `FIX_REPORT.md` with an R1–R4 section.


### 14.7 Round-3 audit (2026-10-30)

R1–R4 verified fixed by independent probes (replay of a finished 3-chunk serial and a
single-shot: zero LLM calls, identical state and `out/`; serial stops at `BUDGET_STOP`).
`pytest`: 57 passed, 2 skipped.

| ID | Defect (evidence) | Required fix | Proof test |
|---|---|---|---|
| R5 | **A refusal on a later chunk drops already-passed episodes from export.** In `cmd_run`, a `SystemExit` refusal inside the chunk loop jumps to the outer handler, so `do_export` never runs for that series (probe: serial chunk 1 `PASS`, chunk 2 extract refused → exit 2, `out/` empty). | On a refusal, stop that series, still export its fresh `PASS`/`FORCED_PASS` episodes, record the refusal (stage, check, chunk) in `PROGRESS.md`, keep exit code 2, and continue with the next source. | E2E via `vsp run --fake-fixtures`: chunk 1 passes, chunk 2 extractor output invalid twice → exit 2, `out/<sid>/ep_001.json` + manifest exist, `vsp verify` passes, next inbox source processed. |


---

## 15. Scraper Module (MVP addition, 2026-10-30)

The owner moved web-novel scraping into the MVP. It lives in its own package
`src/vsp/scrape/` with its own tests in `tests/scrape/`, and has **no dependency on the
LLM code**. First target: `https://truyenfull.live/<slug>/` (chapter pages
`/<slug>/chuong-N/`).

### 15.1 CLI
`vsp scrape <series-url> [--max-chapters N] [--start N] [--rights R] [--out-dir inbox]
[--delay 1.5] [--ingest] [--allow-generic]` and `vsp scrape verify <slug>`.

- `scrape` writes `inbox/<slug>.txt` and `data/scrape/<slug>/` (raw HTML + manifest).
- `--ingest` then calls the existing `do_ingest` (language detection decides `vi`).
- `verify` re-extracts every chapter from the **stored raw HTML** and checks that the
  text hashes and the `inbox` file match the manifest. Any mismatch → exit 2.

### 15.2 Behaviour
1. **Politeness:** read and obey `robots.txt` (refuse disallowed paths, exit 2); a
   descriptive `User-Agent`; ≥ `--delay` seconds between requests (default 1.5); timeout
   30 s; at most 3 retries with exponential backoff, on 429/5xx/network errors only;
   requests are sequential, never parallel.
2. **Chapter list:** parse the series page, follow its pagination until N chapters are
   collected, keep the list order and the site's chapter numbers and titles.
3. **Site adapter** (`adapters/truyenfull.py`): select the chapter-content element.
   Remove scripts, styles, ads, the "Nội dung chương đang bị khóa … quảng cáo" lock box,
   navigation, comments and inline watermark spans. Keep paragraph breaks. Boilerplate
   line patterns live in a config file (`config/scrape/truyenfull.yaml`), not in code.
   Every removed line is recorded in the manifest (`stripped_lines`) so removals stay
   reviewable.
4. **Generic adapter** (fallback, largest text-dense block): only with
   `--allow-generic`; marks the manifest `adapter: generic`.
5. **Resume:** a chapter whose stored HTML exists and whose sha matches the manifest is
   not re-downloaded.
6. **Output file format:** line 1 = series title; for each chapter, a heading line
   `Chương N: <title>` and then its paragraphs, separated by blank lines, NFC-normalized.
   The existing chunker must detect these headings as chapter headings.
7. **Manifest** `data/scrape/<slug>/manifest.json`: series URL, title, author if shown,
   adapter, fetched_at, robots.txt sha; per chapter: number, title, URL, HTTP status,
   `html_sha256`, `text_sha256`, char count, paragraph count, `stripped_lines`.

### 15.3 Validation gates (deterministic; failure → exit 2, nothing written to `inbox/`)
- Chapter numbers are contiguous from `--start` and the count equals the request,
  unless the series has fewer chapters, which is recorded.
- Each chapter: ≥ 1,500 characters and ≥ 10 paragraphs (configurable); no HTML tags
  or entities left; none of the configured lock/ad phrases left.
- No two chapters have the same `text_sha256`; the page title matches the list title.
- The `inbox` file is exactly the concatenation defined in 15.2.6, and its sha is in the
  manifest.

### 15.4 Anti-fabrication rules (binding for any implementing agent)
1. **Story text comes only from fetched HTML.** Never type, paraphrase, summarize,
   translate or "fill in" story text in code, fixtures, `inbox/`, `data/` or docs.
2. Test fixtures are either (a) **synthetic** HTML that mimics the page structure, uses
   obviously fake filler (`Đoạn thử nghiệm số 1 …`) and has `SYNTHETIC` in its file name and
   in an HTML comment, or (b) **at most 2 real pages captured by the scraper itself**,
   stored under `tests/scrape/fixtures/real/` with a `PROVENANCE.json` holding URL,
   fetched_at and sha256 of the bytes. Real fixtures are never edited by hand.
3. Tests make **no network calls** (inject an HTTP transport; use `httpx.MockTransport`).
4. Do not run a full scrape of the target series. Development fetches are limited to
   the series page and ≤ 2 chapter pages. The auditor runs the real scrape.
5. No LLM call anywhere in `src/vsp/scrape/`.
6. If the site blocks or changes structure, report it in `FIX_REPORT.md`. Do not work
   around it with invented content or other sites.

### 15.5 Proof tests (minimum)
Robots.txt refusal; pagination across 2 list pages; rate-limit delay (fake clock);
retry on 503 then success; no retry on 404; lock box and watermark removal with
`stripped_lines` recorded; short-chapter gate; duplicate-chapter gate; leftover-tag gate;
resume without re-download; `verify` passes, then fails after one byte of stored HTML
or of the inbox file is changed; the output file chunks into chapters with the existing
chunker; the CLI exit codes.

---

## 16. OpenCode LLM Backend (owner decision, 2026-10-30)

The owner chose to run both LLM roles through the OpenCode CLI (`opencode-go` plan)
instead of DashScope/DeepSeek API keys. This replaces §1.2 as the default. The HTTP
backend stays available.

### 16.1 Models
- Writer: `opencode-go/muse-spark-1.3-contributor` ($0.10 in / $0.20 out per 1M tokens)
- Judge: `opencode-go/deepseek-v4.1-flash` ($0.15 / $0.60). A different model family is
  mandatory (§1.2) so the writer does not grade itself. `deepseek-v4-pro` ($0.66 / $1.98)
  is the documented upgrade if judge quality is too low.
- Prices are copied into `models.<role>.price_*` from `opencode models opencode-go --verbose`.
  The budget uses the `cost` reported in `step_finish` when present, and these prices otherwise.
- Both are configured in `config/pipeline.yaml` under `models.<role>` with
  `backend: opencode`, plus `binary` (default: `opencode` resolved from PATH),
  `model_id`, `timeout_seconds`. No API keys: opencode uses its own login, and the
  pipeline must never read opencode's auth files.

### 16.2 Call mechanics (verified by the auditor on 2026-10-30)
- `opencode run --model <id> --dir <sandbox> --pure --format json -f <prompt-file> -- "<short instruction>"`.
  `--` is required before the message. Without it the `-f` array swallows the message.
- **Sandbox:** an empty per-call directory under `state/llm_sandbox/<uuid>/` holding only
  the prompt file, deleted after the call. The prompt file contains the messages flattened
  with `### SYSTEM` / `### USER` / `### ASSISTANT` headers.
- **Output:** JSON lines. Concatenate `type == "text"` parts in order. Sum
  `step_finish.part.tokens` (`input`, `output`, `reasoning`, `cache.read`,
  `cache.write`) and `step_finish.part.cost` (USD) into `Usage`.
- **Tool use is invalid:** any `tool_use` event → treat the reply as invalid (repair
  retry, then refusal). Never pass `--auto`.
- Observed: one trivial call ≈ 8.3k input tokens (opencode system overhead), ~$0.0008,
  ~3 s. Therefore `max_total_tokens` for this backend is **150,000** per episode. The cost
  cap stays $0.02 per episode and $5.00 per day. The first live run reports measured
  values (§11.9).
- Raw stdout is stored in `run/<sid>/raw_llm/`; every call is logged in
  `llm_calls.jsonl` with `backend: opencode`, `session_id`, tokens and cost. The cache
  and budget rules of §10.8 apply unchanged.
- Non-zero exit, timeout, empty text or missing `step_finish` → retry up to 2 times,
  each re-checked against budget, then `RuntimeError` (exit 1).

### 16.3 Anti-fabrication rules for the backend
- Production code must never return canned or locally generated responses. `FakeLLMClient`
  is importable only from tests and the `--fake-fixtures` flag.
- Unit tests parse a **captured** real event stream stored as
  `tests/fixtures/opencode/*.jsonl` with `PROVENANCE.json` (command, model, date, sha).
  Do not hand-write it.
- Live check (`-m live`, auto-skipped if `opencode` is not on PATH): one real writer
  call and one real judge call that each return valid JSON, with non-zero tokens logged.

### 16.4 Round-5 audit (2026-10-30)

Round 4 verified: 81 passed, 4 skipped; no assertion removed; synthetic fixtures contain no
story text; real fixtures come from another series with matching PROVENANCE sha; `run/`,
`out/`, `inbox/` untouched. One defect blocks the first real scrape:

| ID | Defect (evidence) | Required fix | Proof test |
|---|---|---|---|
| R6 | **robots.txt redirect loop crashes the scraper (exit 1).** On 2026-10-30, `GET https://truyenfull.live/robots.txt` returns a Cloudflare-cached `301` whose `Location` is the same URL (`cf-cache-status: HIT`), so httpx raises `TooManyRedirects`. That exception is not caught (`ERROR scrape: Exceeded maximum allowed redirects.`, exit 1). `GET /robots.txt?v=1` returns 200 with the real rules (`cf-cache-status: MISS`). | In `check_robots` and `PoliteSession.get`, catch `httpx.TooManyRedirects`. For robots only: on a redirect loop or a self-redirect, retry **once** with a cache-busting query (`?_vsp=<unix-ts>`). Record `robots_fetch: "cache-busted"` and the final URL in the manifest. If that also fails → `RobotsRefused` (exit 2), never exit 1, and never assume allow-all. Chapter/series pages that loop → `ScrapeError` with a clear message. | MockTransport: self-redirect on `/robots.txt`, 200 on `/robots.txt?_vsp=…` → scrape proceeds and the manifest records cache-busted. Both looping → exit 2 with `check=robots`. Series page looping → clear error, no traceback. |

Do not fetch truyenfull.live in this round; the evidence above is enough. Add an R6 section
to `FIX_REPORT.md`.


### 16.5 Round-6 audit — first real scrape (2026-10-30)

`vsp scrape https://truyenfull.live/vung-trom-yeu-anh/ --max-chapters 14` → exit 0;
`--verify` ok; 7 sentences sampled from the live chapters 1 and 7 were all found verbatim.
Chapter 15 is password-locked at the source (the page holds only the translator's notice),
and the gate correctly refused `--max-chapters 15` (1,039 chars < 1,500).

| ID | Defect (evidence) | Required fix | Proof test |
|---|---|---|---|
| R7 | **Manifest claims a removal that did not happen.** Each chapter's `stripped_lines` lists `truyen full, truyenfull, truyenfullvn, truyenfulllive` and `truyenfull,truyenfull vn` (hidden `<p style="display: none;…">` inside `div#chapter-c`), yet both lines are still in `inbox/vung-trom-yeu-anh.txt` (14× each, e.g. after chapter 7's last paragraph). | Elements hidden by inline style (`display:none`, `visibility:hidden`, `height:0`) inside the content element must be removed from the text, and recorded once. Add a `leftover_markers` entry so the gate catches these strings in the output. Assert that every `stripped_lines` entry is absent from the final chapter text. | Test with the real captured fixture (`tests/scrape/fixtures/real/chuong-001.html`): the hidden watermark lines are absent from the text and present in `stripped_lines`. A synthetic case where a "stripped" line survives → gate failure. |
| R8 | Translator credit lines are not story text: `Editor: Min+ Adelia`, `Beta: Min`, `Beta: Doãn Uyển Du`, `Editor: Adelia`. | Add `drop_line_patterns` in `config/scrape/truyenfull.yaml` for whole lines matching `^\s*(Editor|Beta)\s*:` only (case-insensitive), recorded in `stripped_lines`. Do not drop author notes or any other line. | Test with synthetic lines, including a story line that contains the word "editor" mid-sentence, which must be kept. |
| R9 | The locked-chapter refusal says only "too short". | When a chapter fails the length gate and its text matches a configured `locked_at_source_phrases` entry (add `sẽ được đặt pass`, `Lấy pass của chương`), report `check=locked_at_source chapter=N` and suggest `--max-chapters N-1`. Still exit 2. | Synthetic locked chapter → message contains `locked_at_source`. |

Do not fetch truyenfull.live. Re-run nothing in `data/` or `inbox/`; the auditor will re-scrape.


### 16.6 Round-7 audit — first live LLM run (2026-10-30) — BLOCKING

`vsp run inbox/vung-trom-yeu-anh.txt --topology CONTINUOUS_SERIAL` (5 chunks): chunk 1
extract made 1 call (9,318 in / 1,742 out tokens, $0.0013, 28 s). Draft made 1 call (91,749 in,
$0.0034, 93 s), then `BUDGET_STOP` (cap 35,000). The evidence is preserved in
`run/_quarantine_20261030_vung_trom_yeu_anh_ae03322b/`.

| ID | Defect (evidence) | Required fix | Proof test |
|---|---|---|---|
| R10 | **SECURITY: the LLM can run tools on the owner's machine.** The draft stream contains 2 `read` and 4 `bash` tool calls with `status: completed`, e.g. `python3 -c "…open(p).read()…"` and `Get-Content -LiteralPath …prompt.md`. opencode's default permission is allow. The backend flagged `tool_used` but only after the tools had already run. Scraped text is untrusted input (prompt injection). | Every call runs with all tools denied **before** the model starts. (a) Write `opencode.json` `{"$schema":"https://opencode.ai/config.json","permission":"deny"}` into the sandbox. (b) Also pass the same config through the `OPENCODE_CONFIG_CONTENT` env var on the subprocess. (c) Keep the post-hoc `tool_use` → invalid rule. Verify with a live smoke call that asks the model to run `bash echo hi`: the stream must show no completed tool call. | Unit: subprocess env + sandbox file contain the deny config. Live (`-m live`): tool-request prompt → no `status: completed` tool events. |
| R11 | **Silent prompt truncation → beats fabricated from 2% of the chunk.** The prompt file is one-line JSON. opencode's file read truncates long lines, so the extractor saw only the start of chunk_001 (36,800 chars, paragraphs 0–438). All 6 returned beats cite paragraphs 1–10 only, and the reply was accepted as valid. | (a) Render prompt files as plain text with real newlines: each message under `### SYSTEM`/`### USER`, JSON pretty-printed (`indent=1`), long strings hard-wrapped so that **no line exceeds 1,000 characters** and the file stays under 2,000 lines (split across several `-f` files if needed). (b) **Canary gate:** append a random token on the last line (`END-OF-PROMPT <uuid>`); every validator requires the reply field `prompt_canary` to equal it, else invalid (repair retry, then refusal). (c) **Coverage gate for extraction:** cited `source_paragraph_ids` must span ≥ 70% of the chunk's paragraph range (min id ≤ first + 10% and max id ≥ last − 10%), else invalid. | Unit: rendered prompt line lengths; missing/wrong canary → refusal; beats citing only the first 10 of 439 paragraphs → refusal. |
| R12 | Budget cap: `opencode.max_total_tokens: 150000` is ignored; the episode cap stayed 35,000. Spec §16.2 sets 150,000 for this backend. Also, ~8–10k tokens of opencode overhead per call are counted as input. | Use the backend-specific cap when `backend: opencode`. Log `cache.read` separately and count it at the cache price. | Unit: opencode backend + 100k-token episode does not stop. |
| R13 | **LLM artifacts aren't bound to the prompt or model.** `beats.json` inputs are `{chunk, state_in}`, so after R11 is fixed, a resumed run would reuse the fabricated beats. | Add `prompt_template_sha256`, `model_id`, `backend` and `renderer_version` to the `inputs_sha256` of every LLM-derived artifact (beats, draft, rewrite, audit, fast_forward, episode state). | Unit: change a prompt file or model id → stage recomputes. |

Do not fetch truyenfull.live. Do not run `vsp run` on the real novel; the auditor does that.
Live opencode calls: at most 6, only for the R10 and R11 smoke tests. Add an R10–R13
section to `FIX_REPORT.md`.


### 16.7 Round-8 audit — second live run (2026-10-30) — BLOCKING

R10 verified live: with a prompt that tells the model to use tools, no tool executed (deepseek
wrote a `glob` call as plain text; `tool_statuses: []`). R11–R13 code exists, but the run
refused on chunk 1 with `REFUSAL stage=llm check=canary role=writer` after 1 logged call
(14,980 in / 2,590 out, $0.0020, 13 s). The reply was well-formed beats JSON, but it had no
`prompt_canary`.

| ID | Defect (evidence) | Required fix | Proof test |
|---|---|---|---|
| R14 | **The model is never told to echo the canary.** The renderer appends `END-OF-PROMPT <uuid>`, but no prompt asks for a `prompt_canary` field. Only the repair message mentions it, so every first attempt fails by construction and doubles cost. | The renderer adds one fixed instruction at the end of the system section: `Your JSON reply MUST include the top-level field "prompt_canary" whose value is the token on the END-OF-PROMPT line.` Validators must accept and strip that field. | Live smoke: extractor-shaped prompt → first attempt passes the canary. Unit: rendered prompt contains the instruction once. |
| R15 | **The LLM cache served a reply generated from the truncated prompt.** The cache key is `(model_id, messages, {"backend": "opencode"})`. It ignores the renderer, prompt-template and canary scheme, so the first extraction call was answered from round 7's cache (the reply to the truncated prompt). That is why there's only 1 logged call. | Cache key includes `renderer_version`, prompt-template sha and backend config sha. A cache hit must also pass the canary/coverage validators; cached replies without a stored canary are never used for canary-required calls. Record `cached: true` in `llm_calls.jsonl` for every hit. | Unit: bump `renderer_version` → miss. A cached reply lacking a canary is not returned for a canary-required call. |
| R16 | The non-JSON repair retry calls `self.chat(role, fix)` without `chunk_id`/`stage`, so it isn't attributed to the chunk budget/log. | Pass `chunk_id` and `stage` on every retry path. | Unit: retry line in `llm_calls.jsonl` has `chunk_id` + `stage`. |

Do not fetch truyenfull.live, do not run `vsp run` on the novel, and do not delete
`state/llm_cache` (R15 must make stale entries unusable). Live opencode calls: at most 4,
only for R14. Add an R14–R16 section to `FIX_REPORT.md`.


### 16.8 Round-9 audit — third live run (2026-10-30) — BLOCKING

The gates worked: the run refused (`check=canary`) instead of accepting bad data. The
extraction reply cited only paragraphs 0–6 (chunk_001 has 0–438), and its canary was
`"UNSEEN_TRUNCATED_AT_LINE_104_END_OF_PROMPT_NOT_IN_CONTEXT"`. The model said: "I need the
full chunk and canary line … pulling the rest of the prompt file". It tried to read the file,
and tools were denied.

| ID | Defect (evidence) | Required fix | Proof test |
|---|---|---|---|
| R17 | **opencode still truncates the `-f` attachment.** Line-length wrapping (R11) was not enough: the model saw lines 1–104 of a single prompt file, which holds the whole chunk (36,800 Vietnamese chars). The cutoff is a size limit on file attachments, probably ~50 KB. | (a) **Measure the limit** with ≤ 2 live calls: a synthetic file of numbered lines (`L0001 …`) ~120 KB; ask the model for the last line number it can see. Record the measured byte limit in `FIX_REPORT.md`. (b) Split the rendered prompt into parts of at most **60% of the measured limit** in UTF-8 bytes, splitting only at line boundaries. Each part starts with `=== PROMPT PART i OF N ===`, and the system section states N. The canary goes only in the last part. (c) Pass all parts as repeated `-f` flags (verify the CLI accepts several files). (d) Hard limit: if N > 8, refuse the call (exit 2) with a clear message instead of sending it. | Unit: a 150 KB prompt splits into parts under the byte cap with correct headers. Live (≤ 2 calls): a 3-part synthetic prompt with a secret word only in part 3 → the model returns the secret and the canary. |

Do not fetch truyenfull.live and do not run `vsp run` on the novel. At most 4 live opencode
calls in total (≤ 2 measurement + ≤ 2 proof). Add an R17 section to `FIX_REPORT.md`.


### 16.9 Round-10 audit — fourth live run (2026-10-30)

R17 verified live: chunk_001 extraction cited paragraphs 0–438 of 0–438. The canary passed,
then draft → Tier 1 → judge (9/8/6/9 → 8.2) → PASS → evolve → `out/<sid>/ep_001.json`
(199 words, 65 s). Cost for chunk 1: 4 calls, $0.0093, 229 s.

| ID | Defect (evidence) | Required fix | Proof test |
|---|---|---|---|
| R18 | **Series halts on a 2% overshoot of the compiled-state budget.** chunk_002: `REFUSAL stage=state check=compiled-budget measured=611 threshold=600`. §10.8/F11 require trimming to fit; a refusal is only allowed when the irreducible core still exceeds the budget. | Continue the deterministic trim order until ≤ budget: drop inactive non-main characters, then glossary entries not mentioned in the current chunk's beats, then shorten `current_status` strings to 12 words, then `rolling_summary` at sentence boundaries down to its last 2 sentences, then non-pending secrets. Never drop the main cast, the `handover_bridge` or the anti-repetition tracker. Refuse only if those alone exceed the budget, and report what remained. | Unit: state at 611 → trimmed ≤ 600 with handover intact. Synthetic state whose core alone is 700 → refusal naming the core size. |

Do not fetch truyenfull.live and do not run `vsp run` on the novel. No live calls are needed.
Add an R18 section to `FIX_REPORT.md`.


### 16.10 Round-11 audit (2026-10-30) — R18 incomplete

Resuming the run gives: `REFUSAL stage=state check=compiled-budget measured=776 threshold=600
core_tokens=316`. The core fits, but the call still refuses, which contradicts R18 and its own
docstring. The live `series_state.json` per key: character_registry 472, knowledge_and_plot_state
275, localized_glossary 62, handover_bridge 62, anti_repetition_tracker 58, `_episode_cast` 33,
narrative_cursor 21. After the trim steps, the active non-main characters (episode 1 has 9
named characters) and the glossary entries mentioned in the beats are still there.

| ID | Defect | Required fix | Proof test |
|---|---|---|---|
| R19 | The trim order stops early and refuses although core ≤ budget. Internal bookkeeping (`_episode_cast` and any `_`-prefixed key) is sent to the LLM. | (1) Strip every `_`-prefixed key from the compiled state before measuring. (2) Continue the order after step 5: (6) shorten all `current_status` strings to 6 words; (7) drop active non-main characters, least recently active first, and those not named in the current chunk's beats first; (8) drop the remaining glossary entries not in the current beats; (9) cut `rolling_summary` to its last sentence; (10) keep only the 3 most recent pending secrets. (3) Refuse **only** if the measured state still exceeds the budget **and** core_tokens > budget. Otherwise it is a bug and must not happen: assert it in tests. | Unit: replay the exact live `series_state.json` shape from `run/vung_trom_yeu_anh_*/series_state.json` (copy it to `tests/fixtures/state/live_ep1_state.json` with `PROVENANCE.json`; it's generated data, not story text) → compiled ≤ 600 with main cast, handover and tracker intact. Property test: 200 random states whose core ≤ 600 → never refuse. |

No live calls. Do not touch `run/`, `out/`, `data/` or `inbox/` beyond reading that one state
file. Add an R19 section to `FIX_REPORT.md`.


### 16.11 Round-12 audit (2026-10-31)

R19 verified: the live state compiles to 587 tokens with the main cast, handover and tracker
intact. The resume then crashed on chunk_002 after one extract call (18,280 in / 4,798 out):
`ValueError: us_name collision for 'Phó Chính Sơ': frozen='Preston Fu' proposed='Freddie Pho'`
(traceback, exit 1).

| ID | Defect | Required fix | Proof test |
|---|---|---|---|
| R20 | **Frozen names are hidden from the extractor, and a re-proposal crashes the run.** Compiled-state trimming (R19) drops non-main characters, so the extractor never sees that `Phó Chính Sơ` is already `Preston Fu` and proposes a new name. `bible_merge` raises a bare `ValueError`. | (a) Every extractor/writer/judge prompt gets a separate `frozen_names` map `{original_name: us_name}` for the **whole** registry (names only, outside the 600-token compiled-state budget, capped at 150 entries). (b) In `bible_merge`, re-proposing an existing original name is **not** an error: keep the frozen `us_name` and record a warning in `beats.json` (`name_reproposals`). (c) The only refusal is two different original names mapping to the same `us_name`, and it is a `SystemExit` refusal (exit 2), never a traceback. | Unit: re-proposal → frozen name kept + warning. Two originals → same us_name → exit-2 refusal. Prompt contains `frozen_names` with trimmed characters. |
| R21 | A stale `work/chunk_002/refusal.json` from an earlier attempt (`compiled-budget measured=776`) survives a later attempt that failed differently, so `vsp status` reports the wrong cause. | Overwrite or remove `refusal.json` when the chunk is attempted again; keep history in `refusals.jsonl` (append-only). | Unit: two failing attempts → `refusal.json` shows the latest; `refusals.jsonl` has 2 lines. |

No live calls. Do not touch `run/`, `out/`, `data/` or `inbox/`. Add an R20–R21 section to
`FIX_REPORT.md`.


### 16.12 Round-13 audit (2026-10-31)

R20/R21 verified. chunk_002 then went extract → draft → judge → 2 rewrites → judge →
`FORCED_PASS_WITH_WARNING` (7.2) → `out/ep_002.json`. The evolve call hit the $0.02 cap
(spent $0.0186). The auditor raised `hard_cost_cap_usd` to **0.05** in `config/pipeline.yaml`,
because the judge alone used $0.0085 on its first pass (12,429 output tokens, mostly reasoning).
The resume then failed with `BUDGET STOP evolve chunk 2: episode wall-clock cap: 912.2s > 900s`.
The chunk's calls total ≈ 291 s of latency. The rest is calendar time between runs.

| ID | Defect | Required fix | Proof test |
|---|---|---|---|
| R22 | **The wall-clock cap measures calendar time since the chunk's first call, across resumes.** A chunk resumed after a pause can never finish. | Persist `active_seconds` in `budget.json` = sum of measured call durations plus local processing time while a stage runs. The cap compares `active_seconds`, never timestamps. | Unit: budget saved, clock advanced 2 h, reloaded → not over the cap. Two calls of 400 s + 600 s → over 900. |

No live calls. Do not touch `run/`, `out/`, `data/` or `inbox/`, and do not revert the
auditor's `hard_cost_cap_usd: 0.05`. Add an R22 section to `FIX_REPORT.md`.


---

## 17. Quality Hardening Plan (2026-10-31) — owner priority: output quality over throughput

### 17.1 Evidence from the 5-episode live run (`run/vung_trom_yeu_anh_ae03322b`)
| # | Finding | Evidence |
|---|---|---|
| E1 | **The judge found invented facts, and the script was exported anyway.** `knowledge_violations` do not block. | chunk_005 `audit_2`: 4 violations ("last coin" vs source "$20 in coins", wrong motive, prop "pink teddy" vs "red-hat Doraemon doll") → `FORCED_PASS` → `out/ep_005.json` |
| E2 | **Rewrites made things worse, and the worst version was exported.** | chunk_005 overall 7.4 → **7.8** → 7.2; the export used rewrite_2. chunk_002 7.6 → 7.6 → 7.2 |
| E3 | **The weighted average hides failing dimensions.** | chunk_001 logic = 6, overall 8.2 → PASS |
| E4 | **Single-trial judge noise is about the size of the decision margin.** The same chunk's scores move ±0.6 around the 7.0/8.0 thresholds. Research: single-trial judging is too noisy, so use multi-trial aggregation and randomized order ("The Coin Flip Judge?", arXiv 2606.13685). Exact-match agreement overstates reliability, so use Cohen's κ (arXiv 2606.19544). Cross-judge agreement is only ~76% (κ≈0.51). | audits above |
| E5 | **Over-compression:** a 5,000–7,000-word chunk goes into a ~200-word script (≈30:1). The judge repeatedly flags overstuffing ("five+ events in ~21 s", "list-like sequence"). | chunk_002 `audit_2`, chunk_004 `audit_2` |
| E6 | **Defects that no gate or judge criterion covers:** narrator POV flips ("I" in ep 1–3, "she" in ep 4–5); the CTA says "part four" in episode 4; the hook label is taken literally (ep 5 invents a "pink teddy"); CTA openings are templated ("Follow so you don't miss…" in 4/5 episodes). | `out/ep_00{1..5}.json` |
| E7 | **Minor safety:** the protagonist is 13; ep 1 frames a "hot college guy" romantically; ep 4 uses a menstruation scene as a cliffhanger. Platform risk (minor sexualization/romance policies). The judge rubric has no safety criterion. | `out/ep_001.json`, `ep_004.json` |
| E8 | `FORCED_PASS_WITH_WARNING` (3/5 episodes) goes to `out/` as release-ready. | decisions |
| E9 | One rewrite introduced a new Tier 1 failure (beat 1 > 20 words), wasting a cycle. | chunk_004 `gates_1` |

### 17.2 Design principles
1. **Code decides, models give evidence.** The judge answers a fixed checklist of yes/no
   questions, each with a verbatim quote from the script and source paragraph ids. Code
   verifies each quote (substring match after NFC normalization) and that the cited ids
   exist. An unverifiable claim is discarded and logged as a judge error. Scores are
   computed by code from the verified checklist.
2. **No single point of judgment.** Use a panel of 2 judge model families, neither the
   writer's, with 2 samples each in randomized order, and aggregate by median. Disagreement
   goes to the human queue, not to a guess.
3. **Release requires every gate.** No weighted averaging across hard criteria. Fidelity,
   safety and continuity are zero-tolerance.
4. **Never export anything worse than the best candidate.** Every candidate and rewrite is
   kept; the selection is recorded and reproducible.
5. **Judges are measured before they are trusted.** A calibration suite with known defects
   runs on every judge-prompt or model change; results are bound by hash into receipts.
6. **Human in the loop first, then sampling.** Phase 1: the owner reviews every release
   candidate. Phase 2 (only after the measured exit criteria): 10% sampling plus honeypots.
7. **Thresholds are locked.** `config/quality_lock.yaml` is auditor-owned; an implementing
   agent must not change it (§17.6).

### 17.3 Required changes (IDs Q1–Q16)
| ID | Change | Acceptance proof |
|---|---|---|
| Q1 | **Episode-sized chunks.** Values from the lock: serial `sliding_target 3000, min 2200, max 3800`. Same clean-cut rules. | Unit: 14-chapter fixture size → chunks in range. |
| Q2 | **Episode arc selection** (new writer call before drafting). From the chunk beats, pick exactly 1 central conflict: `setup_beat_ids`, `turn_beat_id`, `payoff_beat_id`, `cliffhanger_beat_id`, and `dropped_beat_ids` with a one-line reason each. The drafting prompt receives **only** the selected beats plus their verbatim source paragraphs. Dropped beats go into the handover summary. Deterministic check: ≤ 5 selected beats, ids exist. | Unit + prompt contains only selected beats. |
| Q3 | **Hook definitions, not labels.** Prompts never show internal hook names (`Pink_Teddy_Bear`); they show the definition plus 2 abstract examples. Gate: any object/number in Beat 1 must appear in a selected beat summary or its source paragraphs (token match on nouns/numbers after US mapping), else T1-15 fail. | Unit: "pink teddy" absent from beats → fail. |
| Q4 | **Series voice lock.** At episode 1, store `narration.pov` (`first_person:<us_name>` or `third_person`) and `narration.tense` in series state. New gate T1-16: pronoun profile of the narration matches the lock (first person: "I/my/me" ≥ 60% of narrator references; third person: no "I/my/me" outside quoted dialogue). | Unit: ep-4-style "she" in a first-person series → fail. |
| Q5 | **Continuity gates (deterministic).** T1-17: every capitalized name token in narration is in `frozen_names` values or an allowlist (`Mom, Dad, …` in config). T1-18: any "part/episode N" in narration/CTA equals `episode_number + 1` (cliffhanger) or is absent. T1-19: numbers and money amounts in narration appear in the selected beats/source after unit/currency conversion (tolerance ±1%). | Unit per gate. |
| Q6 | **Anti-template gates.** T1-20: Beat 1 word-3-gram Jaccard vs every earlier hook in the series and in `hook_library` ≤ lock value (0.35). T1-21: the first 5 words of the CTA must not repeat any of the last 3 episodes' CTAs. T1-22: no single discourse marker ("So get this", "Look", "Honestly") in more than 2 of the last 4 episodes. | Unit with the 5 live episodes: ep 2–5 CTAs fail T1-21. |
| Q7 | **Age-up adaptation (owner decision 2026-10-31): never reject a story for having minors; adapt them to 18+ consistently.** (a) Extraction records per character `source_age_or_stage` with a source citation (`unknown` allowed). (b) **Age map (deterministic, frozen in the series bible on first appearance):** any character with source age < 18 or a school-student stage gets `adapted_age = max(18, source_age + 5)`. Example: 13 → 18. Ages of the people around them shift by the same offset when the source states them, so relative gaps are kept (brother 18 → 23, a 7-year gap stays 7 years). Life stage: middle/high-school student → college freshman/sophomore. (c) **Setting map** (frozen, added to `localized_glossary`): school → college; teacher → professor/TA; class → lecture/seminar; parent-teacher summons → dean's-office warning or academic-probation email; school uniform → everyday clothes; homeroom/grade N → year N of college. The writer and judge prompts receive the age map and setting map, and every adapted character is described only as an adult. (d) **Gate T1-23 (zero tolerance):** narration and screen text for an episode with any aged-up character must contain no age number < 18, no grade/school-year term below college, and nothing from `config/lexicons/minor_unsafe.txt` (child descriptors: thirteen…seventeen, teen/teenage, middle school, junior high, high school, 7th–12th grade, homeroom, kid sister/little girl when referring to an aged-up character, parent-teacher, school uniform…). (e) **Consistency gate T1-24:** every stated age/stage for a character equals the frozen map in every episode. (f) Judge checklist item S1: no character reads as a minor; romance and attraction involve adults only. (g) **Hard exclusion (no age-up allowed):** if the source contains sexual content involving a character whose source age is < 18 → `REJECT_SAFETY` and blacklist the series with reason `minor_sexual_content`. Aging up does not launder that. Non-sexual romance, crushes and coming-of-age drama are adapted, not rejected. | Unit: the live ep 1 text ("I'm thirteen", "Middle School") fails T1-23. The frozen map gives Tori 18 / Daniel 23 / the 7-year gap kept, and it is identical in ep 1–5. A source chunk with sexual content plus age 15 → REJECT_SAFETY. |
| Q8 | **Checklist judge** (replaces `audit_judge.md`). Items (each yes/no + `evidence: [{quote, beat_id, source_paragraph_ids}]`): H1 Beat 1 creates a specific open question within 20 words; H2 the payoff beat answers it explicitly; P1 no beat introduces more than 2 new events; P2 each beat changes the situation; L1 every factual claim is supported by the cited source (list unsupported claims); L2 characters act only on what they know; L3 Beat 2 resolves the handover (ep > 1); R1 the ending creates a concrete unanswered question or a moral split; S1 no safety issue (§Q7); V1 consistent POV/tense. Code computes the dimension scores: hook = H1+H2, pacing = P1+P2, logic = L1+L2+L3, retention = R1, each normalized to 0–10. | Unit: unverifiable quote → item recorded `unverified`, not counted as pass. |
| Q9 | **Judge panel + aggregation.** Panel from the lock: `opencode-go/deepseek-v4.1-flash`, `opencode-go/mimo-v2.6-flash` (cheapest non-Muse family; swap only via the lock). 2 samples each, beats presented in a fresh random order per sample (the seed is recorded). Per item: majority of the 4. A tie or a 2–2 split on any zero-tolerance item (L1, S1, V1) → `NEEDS_HUMAN`. | Unit with fake judges: split → NEEDS_HUMAN. |
| Q10 | **Fidelity checker (separate call, judge family).** Input: script narration split into atomic claims by code (sentences), plus the selected beats' verbatim source paragraphs. Output per claim: `supported | contradicted | not_in_source` with source paragraph ids. Code verifies the ids. Release requires `contradicted = 0`; `not_in_source` is allowed only for connective/emotional sentences flagged `stylistic: true` (≤ 3 per episode). | Unit: the chunk_005 "last coin" claim → contradicted → no release. |
| Q11 | **Best-of-N drafting.** N = 3 drafts from the lock, each with a different allowed hook. Screen with Tier 1, then 1 judge sample each; panel + fidelity only on the top 2. Pick by code: release-eligible first, then the higher verified-checklist score, then fewer `not_in_source` claims. | Unit with fakes. |
| Q12 | **Monotonic rewrites.** A rewrite is accepted only if it passes Tier 1, has no new failing checklist item, and the panel score is ≥ the incumbent's. Otherwise revert to the incumbent. The exported script is always the best candidate. Every candidate is kept in `work/<chunk>/candidates/`. The rewriter also receives the Tier 1 limits for the beats it edits (fixes E9). | Unit: the E2 sequence (7.4, 7.8, 7.2) exports the 7.8 version. |
| Q13 | **Release decision.** `RELEASE` only if Tier 1 passes, every zero-tolerance item passes, each dimension ≥ the lock floor (7.0), overall ≥ 8.0, and there is no panel split. Mid-series near-misses → `NEEDS_HUMAN` in `review/<sid>/` (state still evolves, so the series continues), never `out/`. `FORCED_PASS_WITH_WARNING` is removed from export. | E2E with fakes. |
| Q14 | **Judge calibration suite** `vsp calibrate`. Code-generated mutations of real episodes (no hand-written story text): name swap between two characters in one beat; POV flip in beats 4–5; payoff beat replaced with a beat from another episode; a number in narration changed; the CTA episode number wrong; the minor-unsafe lexicon inserted; two beats duplicated (pacing). Labels come from the mutator. Metrics per judge and for the panel: recall per defect type, false-fail rate on unmutated items, test–retest pass/fail agreement over 3 repeats, Cohen's κ vs human labels when available. Results are written to `state/calibration/<judge_prompt_sha>_<model>.json`. **Production refuses to run a judge whose current prompt+model has no calibration receipt meeting the lock minimums** (recall ≥ 0.9 on LLM-judged types, test–retest ≥ 0.9). | `vsp calibrate` on the 5 live episodes → receipts. |
| Q15 | **Human-in-the-loop phase 1.** `vsp review-sheet <sid>` writes `review/<sid>/index.html` (local file, no server): the script with timings, the selected source paragraphs side by side, checklist results with quotes, and the panel vote. `vsp review <sid> <ep>` takes a structured verdict: per dimension 1–10, defect tags from a fixed taxonomy (`config/review_tags.yaml`), `release yes/no`, note. `vsp review pairwise <sid>` shows 2 candidates blind in random order and records the preference. `vsp judge-agreement` reports judge-vs-human κ and MAE per dimension. The rubric is versioned in `rubric/rubric_v<N>.md`; its sha is in every audit; changing it invalidates calibration receipts. | CLI tests; review-sheet renders with no network. |
| Q16 | **Phase-2 switch is data-driven.** `config/quality_lock.yaml` `human_review.mode: all` until `vsp judge-agreement` shows, over ≥ 30 human-reviewed episodes: release-decision κ ≥ 0.7, false-release rate ≤ 5%, and every calibration receipt green. Only the owner flips the mode. In `sample` mode, 10% random plus every `NEEDS_HUMAN` plus 1 honeypot (a calibration mutant) per 10 episodes; a missed honeypot reverts the mode to `all` automatically. | Unit for the auto-revert. |

### 17.4 Budget
Expected per episode: arc selection 1 + drafts 3 + screen 3 + panel 4×2 + fidelity 2 + ≤ 2
rewrites with re-judging ≈ 20 calls, ≈ $0.04–0.06. The lock sets `hard_cost_cap_usd: 0.10`,
`max_total_tokens: 400000` (opencode backend) and `timeout_seconds: 1800` active time.

### 17.5 Measured acceptance (run by the auditor, not the implementer)
On the 14 scraped chapters, re-chunked (Q1), all of these must hold:
1. Every `out/` episode: all Tier 1 gates, 0 contradicted claims, 0 safety fails, panel
   release decision, dimensions ≥ 7, overall ≥ 8.
2. Consistent POV across all episodes; T1-20/21/22 pass across the series.
3. Calibration receipts for both panel judges meet the lock minimums.
4. The auditor's independent read of every released episode (fidelity against the source
   text, hook payoff, POV, safety) finds no defect the pipeline missed. Any miss becomes a
   new calibration mutation and a new gate or checklist item.
5. Rerun = 0 new LLM calls, identical bytes.
6. Measured cost per released episode is reported.
Episodes not meeting these are expected to land in `review/`. That is a correct outcome; the
number released is reported, not targeted.

### 17.6 Anti-cheat rules (binding on the implementing agent)
1. **Protected files** (auditor-owned, any edit = round rejected): `config/quality_lock.yaml`,
   `config/lexicons/minor_unsafe.txt` (additions are allowed only with a FIX_REPORT reason),
   every existing test assertion, `tests/fixtures/**/PROVENANCE.json`, and `SPEC.md`.
   The pipeline reads thresholds **only** from the lock; `pipeline.yaml` values for locked
   keys are ignored and a mismatch is logged. The manifest records the lock sha256;
   `vsp verify` fails if it differs from the lock on disk.
2. No production code may reference calibration mutation ids, fixture file names or test
   paths; mutators live under `tests/` and `src/vsp/calibrate/` and are never imported by
   the release path.
3. Never lower a gate, add an exception, or widen a tolerance to make a case pass. If a gate
   seems wrong, write it up in `FIX_REPORT.md` under "Gate disputes" and leave it failing.
4. Never write `run/`, `out/`, `review/`, `inbox/`, `data/` or `state/calibration/`. Never
   run `vsp run`, `vsp calibrate` or `vsp scrape` against real data. Live opencode calls:
   ≤ 6 total, only for smoke tests of new call types, each listed in `FIX_REPORT.md`.
5. Story text is never typed into code, prompts, tests or docs. Prompt examples must be
   abstract (`<object>`, `<number>`), never story-like sentences.
6. Work in the phase order below. At the end of each phase: run the full suite and update
   `FIX_REPORT.md` with IDs done, files, proof tests and pytest output. Re-read the §17
   rows for the next phase before starting it (keeps context small).

Phases: **A** lock loader + Q13 decision and export split + Q12 → **B** Q1, Q2, Q3, Q4, Q5,
Q6, Q7 (gates and prompts) → **C** Q8, Q9, Q10, Q11 (judging) → **D** Q14, Q15, Q16 (calibration
and human loop).

---

## 18. Second Source: metruyenchu.com.vn + Agent-Run End-to-End (owner request, 2026-10-31)

Target: `https://metruyenchu.com.vn/dao-quy-di-tien` ("Đạo Quỷ Dị Tiên", Hồ Vĩ Bút, 1,037
chapters, horror/eastern fantasy). Scope: **chapters 1–10 only**. `rights_status: unknown`
(the site states the text is "sưu tầm"; being unprotected grants no rights).

### 18.1 Facts verified by the auditor (2026-10-31)
- `robots.txt` = `User-agent: *` with no rules → allowed. Series page 200 (38 KB), static HTML.
- Chapter URLs carry a random token: `/dao-quy-di-tien/chuong-1-Odu5R7udF0MJ`. They **must be
  taken from the series chapter list**, never constructed. The list shows 100 chapters per
  page with pagination (`[1][2]…[›][Cuối»]`).
- The chapter text is static HTML inside the first `div.chapter_wrap` … before the second
  `div.chapter_wrap control-last` (≈10.8k chars in chapter 1). Boilerplate inside it: the
  `《 Chương trước Chương tiếp 》 Tải Ebook` nav, and a converter footer that starts with a
  `_____` separator line followed by `PS:` (begging for votes). Strip from the separator line
  to the end of the content.

### 18.2 Work items
| ID | Item | Proof |
|---|---|---|
| M1 | `adapters/metruyenchu.py` + `config/scrape/metruyenchu.yaml` (selectors, boilerplate, separator rule, pagination). Adapter auto-selected by host. | Tests on ≤ 2 real captured pages (series page + chapter 1) with PROVENANCE, plus synthetic pagination and PS-footer fixtures. |
| M2 | Chapter-list pagination honours `--start`/`--max-chapters` and stops fetching list pages once N chapters are known. | Synthetic 2-page list test. |
| M3 | **Agent-run E2E (owner override of §17.6.4 for this source only).** After §17 Phases A–D are green, run exactly: `vsp scrape https://metruyenchu.com.vn/dao-quy-di-tien --max-chapters 10 --rights unknown`, then `vsp scrape --verify dao-quy-di-tien`, then `vsp run inbox/dao-quy-di-tien.txt --topology CONTINUOUS_SERIAL`, then `vsp verify <sid>` and `vsp review-sheet <sid>`. | Command outputs pasted into `FIX_REPORT.md`. |

### 18.3 Rules for M3 (binding)
1. Only the listed `vsp` commands touch `data/`, `inbox/`, `run/`, `out/`, `review/`. Never
   create, edit, move or delete files there by hand or with ad-hoc scripts. Never write LLM
   output yourself.
2. A refusal or `NEEDS_HUMAN` is a valid result. Report it; do not "fix" it by changing
   prompts, gates, thresholds, the lock or the lexicons and rerunning. A genuine **code
   bug** (crash, traceback, wrong binding) may be fixed only with a new failing-then-passing
   test and a FIX_REPORT entry. The run is then resumed with the same command, never with
   `--force`.
3. Total live LLM spend for M3 ≤ $1.00 (the daily cap still applies). Report
   `vsp status` totals.
4. Report per episode: decision, panel votes, failing checklist items, fidelity counts,
   gate warnings, cost. Do not summarize quality in your own words. The auditor judges
   quality independently.

---

## 19. Auditor log for §17/§18

### 19.1 Phase A audit (2026-10-31)
Verified: the removed `decision == "PASS"` assertions were each replaced by `RELEASE` or
`NEEDS_HUMAN` assertions for the Q13 contract (accepted). `decide_release` checks zero-tolerance
items, the dimension floor, the overall minimum and panel splits from the lock.

Defects (fix before Phase C starts; each needs a failing-then-passing test):
| ID | Defect | Probe evidence | Required fix |
|---|---|---|---|
| A1 | A missing or unparseable `config/quality_lock.yaml` is silently ignored. `load_config` catches the error, sets `lock={}`, `lock_sha256=""`, and every `eff_*` falls back to hard-coded or `pipeline.yaml` values (chunking returns the old 6000/5000/7000). | Temp root with the lock deleted, then with `release: [unclosed`: both give `load_config OK; lock_sha=''`, `chunk={'target': 6000, ...}` | `load_config` refuses (`REFUSAL stage=config check=quality-lock`) when the lock is missing, unparseable, or lacks any key the code reads. No defaults for locked keys anywhere in `quality.py`. |
| A2 | `verify` skips the lock check when the manifest has no `quality_lock_sha256` (`if _manifest_lock and ...`). | `package.py:65` | A manifest without the lock sha fails `verify` (manifests written before §17 are reported as `pre-lock` errors, not passed). |
| A3 | `orchestrator.py` ~L770 has `except Exception:` that falls back to `pipeline.yaml` `audit.pass_threshold`, `episode_limits` and literal 7.0 / `[L1,S1,V1]`. | source | Remove the fallback; let the error propagate. |

### 19.2 Phase B audit (2026-10-31)
Probed on the 5 live episodes in `out/vung_trom_yeu_anh_ae03322b/` and their `run/` beats.
Working as specified: T1-23 lexicon catches live ep 1 (`thirteen`, `middle school`); T1-16
fails live ep 4 (ratio 0.00) and ep 5 (0.24); prompts show Hook A–D, not internal labels.

Defects (fix all before continuing Phase C; each needs a failing-then-passing test that uses
the probe input below):
| ID | Defect | Probe evidence | Required fix |
|---|---|---|---|
| B1 | **The owner's Q7 age-up rule is not implemented.** The code still implements the void Q7 (a `minor_present` safety block). No age map, setting map, T1-24 or `minor_sexual_content` exclusion exists. | `grep adapted_age\|setting_map\|age_map src/` → 0 hits | Implement §17.3 Q7 (a)–(g) as written (the 14:35 FIX_TASK update). |
| B2 | `run_tier1` wraps `_context_gates` in `try/except: pass`. Any exception removes all of T1-15…T1-23 and the script passes. The same function falls back to `pipeline.yaml` limits on any error (as A3). | Monkeypatched `check_minor_safety` to raise → checks ≥ T1-15: `[]`, `passed=True` | No exception swallowing in gates; an error is a refusal. |
| B3 | Every context gate passes when its context is missing ("no selection context", "no registry context", "no voice lock yet"). A caller that forgets a key disables the gate. | source | Distinguish *missing* (`None` → refusal) from *legitimately empty* (`[]`, e.g. ep 1 has no prior hooks). The production path must supply every key. |
| B4 | T1-15 checks only capitalized tokens of ≥ 4 letters. Live ep 5's invented "pink teddy" (the E6 case that Q3 exists for) passes. | `'pink teddy' in chunk_005 beats: False`, T1-15 `pass: True` | Check content nouns regardless of case. Proof: live ep 5 beat 1 fails; report the result on the other 4 live hooks in FIX_REPORT. |
| B5 | `is_minor_age` treats any "student" as a minor. | `college student`, `university student`, `20, college student`, `graduate student` → `True` | Only school-level stages (elementary/middle/high school, grade ≤ 12) or ages < 18 count. |
| B6 | Q6 history (`_prior_episode_texts`) reads only `out/`. With `human_review.mode: all`, episodes land in `review/`, so T1-20/21/22 see no history and pass vacuously. | source | Use the selected candidate of every earlier episode of the series, whatever its decision. |
| B7 | T1-18 only matches digits. Live ep 4 "Follow for part four" (expected 5) passes. | T1-18 `measured: no mention` | Also match number words (one … twenty) and ordinals. Proof: live ep 4 fails. |
| B8 | T1-23 misses digit ages and screen text. | `I'm 13 …` → pass; `screen_text_overlay: "13-YEAR-OLD ME"` → pass | Scan narration, outro and screen text; flag any stated age < 18 (digits or words) for a character. |
| B9 | **Auditor spec correction (T1-21).** The repeated CTA phrase sits at the end ("…Follow so you don't miss it."), so a first-5-words check misses it. | live ep 2–4 all pass T1-21 | New rule: no word 5-gram of the CTA appears in any of the last 3 CTAs. The lock key `anti_template.cta_ngram: 5` was added by the auditor. Proof: live ep 2 and ep 3 fail. |
| B10 | T1-16 false-fails a first-person episode: live ep 2 ("If Jay liked me…") gets ratio 0.53 because the ratio counts every he/she in the story. | ep 2 `first=10 third=9` | Measure narrator references, not all pronouns. Proof: ep 1–3 pass, ep 4–5 fail. |

### 19.3 Phase C and D audit (2026-10-31)
The contributor reported all phases done (134 passed, 8 skipped) but did **not** fix §19.1
(A1–A3 still present: `orchestrator.py` ~L812 fallback, `quality.py` defaults) or §19.2. Its
session never re-read `FIX_TASK.md` after 14:28. All of §19.1–19.3 is open.

| ID | Defect | Evidence | Required fix |
|---|---|---|---|
| C1 | **Legacy judge bypass.** If a judge reply has `scores` and no `items`, `_eval` takes the old single-sample path: no checklist, no panel, no fidelity call. `decide_release` skips zero-tolerance items when `checklist` is `None`, so the episode can be RELEASEd without L1/S1/V1 or fidelity. The reply shape decides which gates apply. | `orchestrator.py` `if "items" not in res and "scores" in res: _used_legacy = True` | Production path: a non-checklist reply is a validation failure → repair retry → REFUSAL. `decide_release` returns NEEDS_HUMAN when `checklist` or `fidelity` is missing. Legacy tests move to fixtures in the checklist format. |
| C2 | **Calibration gate checks one model.** It checks `pipeline.yaml` `models.judge.model_id`, not each lock panel model; `mimo-v2.6-flash` is never checked. It sits inside `except Exception: pass`. | source | Check every model in `lock.judging.panel` and the fidelity model. No exception swallowing. |
| C3 | **Panel order seeds are not reproducible.** Seeds use Python `hash()`, which is salted per process. The shuffled beats, and so the prompts and cache keys, change on every resume. That breaks §17.5.5 (rerun = 0 calls). | `hash(('chunk_001',0,'opencode-go/deepseek-v4.1-flash',0))` → `529069047`, then `1235973646` in a second process | Derive seeds from sha256 of the same tuple. Proof test runs two subprocesses and compares. |
| C4 | **Unverified votes are ignored in aggregation.** `[True, None, None, None]` → item `True`. One verified vote passes an item. | probe | Per-item majority over all 4 samples: `True` needs ≥ 3 verified `True`. Any other outcome is a split (not a pass). |
| C5 | Best-of-N silently falls back to a single draft on `ValueError` or any `Exception`. Gate and fidelity source text is cut to the first 20 paragraph ids. | source | On the production path, fewer than `best_of_n` candidates or any error → REFUSAL. Pass every selected-beat paragraph; refuse if it exceeds the prompt budget. |
| D1 | **An always-fail judge gets a green receipt.** Green uses only overall recall and test–retest. A judge that fails everything scores recall 1.0 and agreement 1.0. | `calibrate_scripts(live 5 eps, always_fail)` → `recall=1.0 false_fail=1.0 retest=1.0 GREEN=True` | Green also requires `false_fail_rate ≤ lock.calibration.max_false_fail_rate` (0.10) and recall ≥ `min_recall_per_type` (0.90) **for each mutator type**, not the average. Auditor added both keys to the lock. Proof: the always-fail and always-pass judges are both red. |
| D2 | **Calibration does not reproduce production conditions.** `cmd_calibrate` sends `compiled_state: {}`, `frozen_names: {}`, `chunk_beats: []` and verifies evidence against an empty paragraph set, so any cited paragraph makes the item `None` (= fail). That inflates recall. One receipt is written under `panel[0]` while calls go to `pipeline.yaml`'s judge model. | `cli.py cmd_calibrate` | Calibrate each panel model separately, with the same message builder `_eval` uses (state, frozen names, shuffled beats from the episode's `run/` chunk) and the chunk's real paragraph ids. Write one receipt per model under that model's id. |
| D3 | The `minor_unsafe_insert` mutator inserts "hot", which is not in the current lexicon and does not test the age-up rule. | `runner.py` | After B1, mutate by inserting a child descriptor and an age < 18 for an aged-up character (S1/T1-23). |
