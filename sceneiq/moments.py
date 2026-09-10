"""Tubi Moments ingestion: scene-by-scene VLM data for a title.

When Moments data is supplied, anchor discovery stops guessing the film's
structure — anchors attach to real scenes, timecodes and runtime fractions
come from the VLM record (code-assigned, not model-claimed), and the
validation judge sees the actual scene contents.

Two supported formats, dispatched by file extension:
  .json — the scene-sense prototype export: {title, duration_sec, scenes[]}
          with content_desc.structured_data per scene.
  .csv  — a Databricks export of core_dev.tubidw.tubi_moments_scene_catalog:
          one row per scene (scene_start_ts/scene_end_ts in float seconds,
          description, cast_list, sentiment_list, IAB tiers, GARM labels).
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path


def _hms_to_seconds(s: str) -> float:
    """Handles both H:MM:SS.mmm and MM:SS.mmm — Moments exports mix them."""
    if not s:
        return 0.0
    try:
        parts = s.split(":")
        if len(parts) == 3:
            h, m, rest = parts
            return int(h) * 3600 + int(m) * 60 + float(rest)
        if len(parts) == 2:
            m, rest = parts
            return int(m) * 60 + float(rest)
        return float(parts[0])
    except ValueError:
        return 0.0


@dataclass
class MomentScene:
    scene_index: int
    scene_type: str
    start_time: str
    end_time: str
    start_seconds: float
    runtime_fraction: float
    summary: str
    setting: str = ""
    characters: list = field(default_factory=list)
    celebrities: list = field(default_factory=list)
    key_objects: list = field(default_factory=list)
    key_actions: list = field(default_factory=list)
    dialogue_highlights: list = field(default_factory=list)
    songs: list = field(default_factory=list)
    themes: list = field(default_factory=list)

    def as_context(self, max_chars: int = 700) -> str:
        bits = [f"[scene {self.scene_index}] {self.start_time}–{self.end_time}"]
        if self.summary:
            bits.append(f"summary: {self.summary}")
        if self.setting:
            bits.append(f"setting: {self.setting}")
        if self.key_objects:
            bits.append("objects: " + ", ".join(map(str, self.key_objects[:6])))
        if self.key_actions:
            bits.append("actions: " + "; ".join(map(str, self.key_actions[:4])))
        if self.songs:
            bits.append("songs: " + ", ".join(map(str, self.songs[:3])))
        if self.celebrities:
            bits.append("cast on screen: " + ", ".join(map(str, self.celebrities[:5])))
        dh = [d for d in self.dialogue_highlights if d][:3]
        if dh:
            bits.append("dialogue: " + " / ".join(f'"{d}"' for d in dh))
        return "\n".join(bits)[:max_chars]


@dataclass
class TitleMoments:
    title: str
    duration_sec: float
    scenes: list  # list[MomentScene]

    @property
    def runtime_minutes(self) -> int:
        return round(self.duration_sec / 60)

    def content_scenes(self) -> list:
        return [s for s in self.scenes if s.scene_type == "content"]

    def scene(self, index: int) -> MomentScene | None:
        for s in self.scenes:
            if s.scene_index == index:
                return s
        return None

    def sampled_scenes(self, max_scenes: int = 40) -> list:
        """Content scenes sampled evenly across the WHOLE runtime — the PRD
        explicitly calls out the opening-act bias of reading only the first
        N scenes."""
        scenes = self.content_scenes()
        if len(scenes) <= max_scenes:
            return scenes
        step = len(scenes) / max_scenes
        return [scenes[int(i * step)] for i in range(max_scenes)]


def _seconds_to_hms(sec: float) -> str:
    sec = max(0, int(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _json_list(raw: str) -> list:
    """Parse warehouse list columns: '[\"A\",\"B\"]', 'null', or ''."""
    raw = (raw or "").strip()
    if not raw or raw == "null":
        return []
    try:
        val = json.loads(raw)
        return val if isinstance(val, list) else [val]
    except json.JSONDecodeError:
        return [raw]


def load_moments(path: str | Path) -> TitleMoments:
    if str(path).lower().endswith(".csv"):
        return load_moments_csv(path)
    return load_moments_json(path)


def load_moments_csv(path: str | Path) -> TitleMoments:
    """Databricks tubi_moments_scene_catalog export: one row per scene."""
    rows = [r for r in csv.DictReader(Path(path).open()) if r.get("is_active", "true") == "true"]
    rows.sort(key=lambda r: float(r.get("scene_start_ts") or 0.0))
    duration = max((float(r.get("scene_end_ts") or 0.0) for r in rows), default=0.0)
    title = rows[0].get("program_name") or rows[0].get("content_name", "") if rows else ""
    scenes = []
    for i, r in enumerate(rows):
        start = float(r.get("scene_start_ts") or 0.0)
        themes = _json_list(r.get("iab_tier1_list", "")) + _json_list(r.get("iab_tier2_list", ""))
        scenes.append(
            MomentScene(
                scene_index=i,
                scene_type="content",
                start_time=_seconds_to_hms(start),
                end_time=_seconds_to_hms(float(r.get("scene_end_ts") or 0.0)),
                start_seconds=start,
                runtime_fraction=min(1.0, start / duration) if duration else 0.0,
                summary=r.get("description", "") or "",
                celebrities=_json_list(r.get("cast_list", "")),
                themes=themes + _json_list(r.get("sentiment_list", "")),
            )
        )
    return TitleMoments(title=title, duration_sec=duration, scenes=scenes)


def load_moments_json(path: str | Path) -> TitleMoments:
    data = json.loads(Path(path).read_text())
    duration = float(data.get("duration_sec") or 0.0)
    scenes = []
    for s in data.get("scenes", []):
        sd = (s.get("content_desc") or {}).get("structured_data") or {}
        start_s = _hms_to_seconds(s.get("start_time", ""))
        scenes.append(
            MomentScene(
                scene_index=int(s.get("scene_index", len(scenes))),
                scene_type=s.get("scene_type", "content"),
                start_time=s.get("start_time", ""),
                end_time=s.get("end_time", ""),
                start_seconds=start_s,
                runtime_fraction=min(1.0, start_s / duration) if duration else 0.0,
                summary=s.get("summary") or sd.get("scene_summary", "") or "",
                setting=str(sd.get("setting", "") or ""),
                characters=list(sd.get("characters") or []),
                celebrities=list(sd.get("celebrities") or []),
                key_objects=list(sd.get("key_objects") or []),
                key_actions=list(sd.get("key_actions") or []),
                dialogue_highlights=list(sd.get("dialogue_highlights") or []),
                songs=list(s.get("songs") or []),
                themes=list(sd.get("themes_and_concepts") or []),
            )
        )
    return TitleMoments(
        title=data.get("title", ""),
        duration_sec=duration,
        scenes=scenes,
    )
