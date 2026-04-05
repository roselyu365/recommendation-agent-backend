from pydantic import BaseModel
from typing import Optional, List, Dict, Any


class UserCreate(BaseModel):
    user_id: str
    name: str


class ChatRequest(BaseModel):
    message: str


class ReactionRequest(BaseModel):
    recommendation_id: int
    reaction: str  # "positive" | "negative" | "neutral"


class WriterCreate(BaseModel):
    writer_id: str
    name: str
    name_en: str
    personality_tags: List[str]
    suitable_personality: str
    suitable_life_state: str
    value_resonance: str
    suitable_timing: str
    emotional_tone: str
    contra_indicators: str
    recommendation_style: str


class NoteCreate(BaseModel):
    content: str
    source: Optional[str] = ""


class ReminderCreate(BaseModel):
    writer_id: str
    remind_time: str  # "HH:MM"


class ReminderUpdate(BaseModel):
    remind_time: Optional[str] = None
    enabled: Optional[int] = None


class EpisodicSignals(BaseModel):
    emotion_tag: Optional[str] = None
    life_event_tag: Optional[str] = None
    value_signal_tag: Optional[str] = None
    content_summary: str
    writer_signal: Optional[str] = None  # writer_id if any
