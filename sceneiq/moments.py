"""Tubi Moments ingestion: scene-by-scene VLM data for a title.

When Moments data is supplied, anchor discovery stops guessing the film's
structure — anchors attach to real scenes, timecodes and runtime fractions
come from the VLM record (code-assigned, not model-claimed), and the
validation judge sees the actual scene contents.

Schema matches the Tubi Moments export used by the scene-sense prototype:
top-level {title, duration_sec, scenes[]}, each scene carrying start/end
times, scene_type, summary, songs, and content_desc.structured_data with
characters/celebrities/setting/key_objects/key_actions/dialogue_highlights.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


def _hms_to_seconds(s: str) -> float:
    if not s:
        return 0.0
    try:
        h, m, rest = s.split(":")
        return int(h) * 3600 + int(m) * 60 + float(rest)
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


def load_moments(path: str | Path) -> TitleMoments:
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
