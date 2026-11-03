"""Pydantic v2 schemas for every JSON artifact (§3, §9). Extra fields forbidden on export."""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict

TopologyType = Literal["SINGLE_SHOT", "ANTHOLOGY", "EPISODIC_SERIES", "CONTINUOUS_SERIAL"]
ResetMode = Literal["FULL_FLUSH", "FULL_RESET", "KEEP_MAIN_CAST_FLUSH_PLOT", "PERSIST"]
CTAType = Literal["cliffhanger_subscription", "engagement_verdict", "loop_back", "story_complete"]
DecisionType = Literal["PASS", "RELEASE", "NEEDS_HUMAN", "FORCED_PASS_WITH_WARNING", "REJECT", "REJECT_SAFETY", "FAST_FORWARD_MERGE", "BUDGET_STOP"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceRaw(Strict):
    series_id: str
    title: str
    normalized_text: str
    paragraphs: list[dict]
    source_url_or_path: str
    source_sha256: str
    raw_bytes_sha256: str
    rights_status: str = "unknown"
    language: str = "en"


class TopologyMeta(Strict):
    series_id: str
    topology: TopologyType
    confidence: float = 1.0
    source_language: str = "en"
    genre: str = ""
    genre_tags: list[str] = Field(default_factory=list)
    word_equiv: int = 0
    num_chunks: int = 0
    notes: str = ""


class ChunkMeta(Strict):
    chunk_id: str
    series_id: str
    chunk_index: int
    topology_type: TopologyType
    word_count: int
    word_equiv: int
    is_arc_start: bool = False
    is_hard_boundary_end: bool = False
    state_reset_instruction: ResetMode = "PERSIST"
    raw_text_content: str
    paragraph_ids: list[int] = Field(default_factory=list)
    clean_cut: bool = True
    inputs_sha256: dict = Field(default_factory=dict)


class BeatItem(Strict):
    beat_id: int
    summary_en: str
    characters: list[str] = Field(default_factory=list)
    source_paragraph_ids: list[int] = Field(default_factory=list)
    tension: int = 3
    is_twist: bool = False
    is_cliffhanger_candidate: bool = False
    bizarre_detail: str | None = None


class NewEntity(Strict):
    model_config = ConfigDict(extra="allow")
    original_name: str
    proposed_us_name: str
    role: str = ""
    gender: str = "unknown"
    main_cast: bool = False
    age_or_stage: str = "unknown"
    age_citation: list[int] = Field(default_factory=list)


class BeatsFile(Strict):
    chunk_id: str
    series_id: str
    beats: list[BeatItem]
    new_entities: list[NewEntity] = Field(default_factory=list)
    glossary_additions: dict = Field(default_factory=dict)
    genre_tags: list[str] = Field(default_factory=list)
    inputs_sha256: dict = Field(default_factory=dict)
    name_reproposals: list[dict] = Field(default_factory=list)


class CharacterEntry(Strict):
    model_config = ConfigDict(extra="allow")
    original_name: str
    us_name: str
    role: str = ""
    tts_voice_id: str = ""
    current_status: str = ""
    main_cast: bool = False
    age_or_stage: str = "unknown"


class SeriesState(Strict):
    model_config = ConfigDict(extra="allow")
    series_id: str
    current_episode: int = 0
    narrative_cursor: dict = Field(default_factory=dict)
    anti_repetition_tracker: dict = Field(default_factory=dict)
    character_registry: list[CharacterEntry] = Field(default_factory=list)
    localized_glossary: dict = Field(default_factory=dict)
    knowledge_and_plot_state: dict = Field(default_factory=dict)
    handover_bridge: dict = Field(default_factory=dict)
    narration: dict = Field(default_factory=dict)


class SceneBeat(Strict):
    beat_id: int
    block_type: str
    timestamp_est: str = ""
    narration_text: str
    tts_emotion_prompt: str = ""
    screen_text_overlay: str = ""
    b_roll_vibe: str = ""
    sfx_cue: str = ""
    bgm_directive: str = ""


class CallToAction(Strict):
    type: CTAType
    outro_narration: str
    loop_connector_word: str = ""


class ScriptMeta(Strict):
    series_id: str
    episode_number: int = 0
    chunk_id: str = ""
    pacing_template: str = ""
    hook_option: str = ""
    hook_payoff_beat_id: int = 4
    target_market: str = "US"
    estimated_duration_sec: int = 0
    total_word_count: int = 0
    provenance: dict = Field(default_factory=dict)


class ScriptOutput(Strict):
    script_metadata: ScriptMeta
    scene_beats: list[SceneBeat]
    call_to_action: CallToAction


class Tier1Receipt(Strict):
    model_config = ConfigDict(extra="allow")
    script_sha256: str
    checks: list[dict] = Field(default_factory=list)
    passed: bool = True
    warnings: list[str] = Field(default_factory=list)


class AuditResult(Strict):
    model_config = ConfigDict(extra="allow")
    scores: dict
    overall: float = 0.0
    payoff_resolved: bool = True
    knowledge_violations: list[str] = Field(default_factory=list)
    failed_beat_ids: list[int] = Field(default_factory=list)
    rewrite_instructions: list[dict] = Field(default_factory=list)


class Decision(Strict):
    model_config = ConfigDict(extra="allow")
    chunk_id: str
    decision: DecisionType
    episode_number: int | None = None
    overall_score: float | None = None
    tier1_passed: bool = False
    rewrite_count: int = 0
    reason: str = ""
