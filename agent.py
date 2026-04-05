"""
Core agent logic: system prompt construction, skill selection, Zhipu AI calls,
background signal extraction and crystallization.
"""

import json
import asyncio
import os
from datetime import datetime
from typing import AsyncGenerator, Dict, List, Optional

import openai

from database import Database

ZHIPU_BASE = os.getenv("ZHIPU_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/")
ZHIPU_KEY = os.getenv("ZHIPU_API_KEY")
MODEL_MAIN = os.getenv("ZHIPU_MODEL_MAIN", "glm-4.5")
MODEL_FAST = os.getenv("ZHIPU_MODEL_FAST", "glm-4.5-air")

RECOMMENDATION_KEYWORDS = {
    "想读", "推荐", "作家", "读点什么", "看点什么",
    "recommend", "suggest", "book", "author",
}

PERSONA = """你是一个文学沙龙的主理人，有品味，有温度，有独立判断。
你了解这里的每一位作家 agent，更在意的是眼前这个人现在的状态。
你推荐作家，不是因为"类型匹配"，而是因为"时机对了"。
你说话有自己的腔调——不急，不媚，偶尔有点自己的小固执。
用中文回复。"""

EXTRACT_SIGNALS_TOOL = {
    "type": "function",
    "function": {
        "name": "extract_signals",
        "description": "Extract structured signals from a conversation turn for the episodic memory log.",
        "parameters": {
            "type": "object",
            "properties": {
                "emotion_tag": {
                    "type": "string",
                    "description": "用户情绪状态，如：疲惫/兴奋/迷茫/平静/愤怒/低落/满足",
                },
                "life_event_tag": {
                    "type": "string",
                    "description": "用户提到的具体生活事件，无则填 null",
                },
                "value_signal_tag": {
                    "type": "string",
                    "description": "用户表达的价值观或态度信号，无则填 null",
                },
                "content_summary": {
                    "type": "string",
                    "description": "一句话描述用户在本次对话中说了什么重要的事",
                },
                "writer_signal": {
                    "type": "string",
                    "description": "对话内容让你联想到哪位作家的 writer_id，无则填 null",
                },
            },
            "required": ["emotion_tag", "content_summary"],
        },
    },
}

CRYSTALLIZE_TOOL = {
    "type": "function",
    "function": {
        "name": "crystallize_memory",
        "description": "Compress 20 episodic entries into a warm tier pattern summary.",
        "parameters": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "description": "这批条目覆盖的时间段，格式 YYYY-MM",
                },
                "pattern_summary": {
                    "type": "string",
                    "description": "2-3句话描述这段时间用户的状态模式和情绪特征",
                },
                "recommendation_hint": {
                    "type": "string",
                    "description": "基于这批条目，推荐方向有何调整？有无强匹配作家信号？",
                },
                "writer_signal": {
                    "type": "string",
                    "description": "如果有强匹配作家，填 writer_id；否则填 null",
                },
            },
            "required": ["period", "pattern_summary"],
        },
    },
}


class RecommendationAgent:
    def __init__(self, db: Database):
        self.db = db
        self.client = openai.AsyncOpenAI(api_key=ZHIPU_KEY, base_url=ZHIPU_BASE)

    # ── System prompt builder ────────────────────────────────────────────────

    def _build_system_prompt(
        self,
        skill_mode: str,
        profile: Dict,
        hot_tier: List[Dict],
        writers: List[Dict],
        rec_history: List[Dict],
    ) -> str:
        parts = [PERSONA, ""]

        # Cold tier
        cold = profile.get("cold_tier", {})
        if cold:
            parts.append("【用户画像 — 核心人格】")
            for k, v in cold.items():
                parts.append(f"- {k}：{v}")
            parts.append("")

        # Warm tier (last 2 summaries)
        warm = profile.get("warm_tier", [])
        if warm:
            parts.append("【近期模式摘要】")
            for summary in warm[-2:]:
                parts.append(f"- {summary.get('period', '')}：{summary.get('pattern_summary', '')}")
                if summary.get("recommendation_hint"):
                    parts.append(f"  推荐方向：{summary['recommendation_hint']}")
            parts.append("")

        # Hot tier (recent episodic entries)
        if hot_tier:
            parts.append("【近期对话记录】")
            for entry in hot_tier[-10:]:
                ts = entry.get("timestamp", "")[:16]
                tags = " ".join(
                    f"[{v}]"
                    for k, v in entry.items()
                    if k in ("emotion_tag", "life_event_tag", "value_signal_tag") and v
                )
                parts.append(f"- {ts} {tags} {entry.get('content', '')}")
            parts.append("")

        # Skill-specific context
        if skill_mode == "onboard":
            parts.append("【当前任务：初次引导】")
            parts.append(
                "这是你和这位用户的第一次对话。以闲聊的方式，在对话中自然问出 3-4 个问题，"
                "了解用户的生活节奏、性格基调和价值观信号。不要连续发问，一次问一个，"
                "根据对话节奏灵活插入。不要问「你喜欢什么类型的书」，问用户的状态和感受。"
            )
        elif skill_mode == "recommend":
            writer_db_text = _format_writer_db(writers)
            parts.append("【作家数据库】")
            parts.append(writer_db_text)
            parts.append("")

            # Reflexion: rejected writers
            rejected = {r["writer_id"] for r in rec_history if r.get("user_reaction") == "negative"}
            if rejected:
                parts.append(f"【Reflexion 降权】以下作家曾被拒绝，匹配权重 -0.2：{', '.join(rejected)}")
                parts.append("")

            parts.append("【当前任务：推荐作家】")
            parts.append(
                "根据用户当前画像，按匹配流程选出 1-2 位作家。"
                "先用「不适合」字段排除，再按性格/状态/价值观/时机各 0.25 打分，选最高分。"
                "生成 2-4 句推荐词：包含用户当前状态 + 为什么是这位作家 + 为什么是现在。"
                "不用「你会喜欢」句式。有自己的腔调。"
                "回复末尾加一行 JSON：{\"recommended_writer\": \"<writer_id>\"}"
            )
        else:  # chat
            parts.append("【当前任务：日常陪伴对话】")
            parts.append(
                "主线是陪伴聊天，画像采集是副产品，用户无感知。"
                "识别情绪、生活事件、价值观信号，但不要打断对话流。"
            )

        return "\n".join(parts)

    # ── Skill selector ───────────────────────────────────────────────────────

    def _determine_skill(self, message: str, user: Dict) -> str:
        if not user.get("is_onboarded"):
            return "onboard"
        msg_lower = message.lower()
        if any(kw in message for kw in RECOMMENDATION_KEYWORDS):
            return "recommend"
        # Auto-recommend if ≥15 dialogs since last recommendation
        dialogs_since_rec = user.get("dialog_count", 0) - user.get("last_rec_dialog_count", 0)
        if dialogs_since_rec >= 15:
            return "recommend"
        return "chat"

    # ── Main chat (streaming) ─────────────────────────────────────────────────

    async def generate_feed(self) -> List[Dict]:
        """Generate fresh feed posts from all writers in their own voice."""
        cached = self.db.get_fresh_feed()
        if cached:
            return cached

        writers = self.db.get_all_writers()
        posts = []
        for writer in writers:
            try:
                post_content = await self._generate_writer_post(writer)
                posts.append({"writer_id": writer["writer_id"], "content": post_content})
            except Exception:
                pass

        if posts:
            self.db.save_feed_posts(posts)
        return posts

    async def _generate_writer_post(self, writer: Dict) -> str:
        prompt = (
            f"你是{writer['name']}（{writer.get('name_en', '')}）。"
            f"请用你自己的文字风格，写一条今天的「朋友圈动态」。"
            f"要求：3-5句话，像日记片段或随想，体现你的性格气质：{', '.join(writer.get('personality_tags', []))}。"
            f"不要提书名，不要自我介绍，就像在记录此刻的感受或观察。用中文。"
        )
        response = await self.client.chat.completions.create(
            model=MODEL_FAST,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content

    async def stream_chat(
        self, user_id: str, message: str, agent_id: str = "recommendation"
    ) -> AsyncGenerator[str, None]:
        user = self.db.get_user(user_id)
        if not user:
            yield "data: " + json.dumps({"error": "User not found"}) + "\n\n"
            return

        skill_mode = self._determine_skill(message, user)
        profile = self.db.get_profile(user_id)
        hot_tier = self.db.get_hot_tier(user_id)
        writers = self.db.get_all_writers()
        rec_history = self.db.get_recommendation_history(user_id)
        conversation_history = self.db.get_recent_turns(user_id, agent_id)

        system_prompt = self._build_system_prompt(
            skill_mode, profile, hot_tier, writers, rec_history
        )

        messages = conversation_history + [{"role": "user", "content": message}]

        full_response = []
        recommended_writer_id = None

        stream = await self.client.chat.completions.create(
            model=MODEL_MAIN,
            max_tokens=4000,
            messages=[{"role": "system", "content": system_prompt}] + messages,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                full_response.append(delta.content)
                yield "data: " + json.dumps({"type": "text", "content": delta.content}) + "\n\n"

        response_text = "".join(full_response)

        # Extract recommended_writer from response if recommend mode
        if skill_mode == "recommend":
            recommended_writer_id = _parse_recommended_writer(response_text)
            if recommended_writer_id:
                rec_id = self.db.add_recommendation(user_id, recommended_writer_id, response_text)
                self.db.update_last_rec_dialog_count(user_id)
                yield "data: " + json.dumps({
                    "type": "recommendation",
                    "writer_id": recommended_writer_id,
                    "recommendation_id": rec_id,
                }) + "\n\n"

        yield "data: " + json.dumps({"type": "done"}) + "\n\n"

        # Persist conversation turn
        self.db.save_turn(user_id, "user", message, agent_id)
        self.db.save_turn(user_id, "assistant", response_text, agent_id)
        self.db.increment_dialog_count(user_id)

        # Mark onboarded after first successful exchange
        if skill_mode == "onboard":
            self.db.mark_onboarded(user_id)

        # Background: extract signals + maybe crystallize
        asyncio.create_task(
            self._background_update(user_id, message, response_text)
        )

    # ── Background: signal extraction + crystallization ──────────────────────

    async def _background_update(self, user_id: str, user_message: str, assistant_response: str):
        try:
            signals = await self._extract_signals(user_message, assistant_response)
            entry = {
                "timestamp": datetime.now().isoformat(),
                "emotion_tag": signals.get("emotion_tag"),
                "life_event_tag": signals.get("life_event_tag"),
                "value_signal_tag": signals.get("value_signal_tag"),
                "content": signals.get("content_summary", user_message[:100]),
                "writer_signal": signals.get("writer_signal"),
            }
            self.db.add_episodic_entry(user_id, entry)

            if self.db.count_uncrystallized(user_id) >= 20:
                await self._crystallize(user_id)
        except Exception:
            pass  # background task failure should not surface to user

    async def _extract_signals(self, user_message: str, assistant_response: str) -> Dict:
        prompt = (
            f"用户说：{user_message}\n\n"
            f"助手回复：{assistant_response[:300]}\n\n"
            "请提取用户本次对话的情绪和生活信号。"
        )
        response = await self.client.chat.completions.create(
            model=MODEL_FAST,
            max_tokens=2000,
            tools=[EXTRACT_SIGNALS_TOOL],
            tool_choice={"type": "function", "function": {"name": "extract_signals"}},
            messages=[{"role": "user", "content": prompt}],
        )
        tool_calls = response.choices[0].message.tool_calls
        if tool_calls and tool_calls[0].function.name == "extract_signals":
            return json.loads(tool_calls[0].function.arguments)
        return {"content_summary": user_message[:100]}

    async def _crystallize(self, user_id: str):
        entries = self.db.get_uncrystallized(user_id, limit=20)
        if len(entries) < 20:
            return

        entries_text = "\n".join(
            f"- [{e.get('timestamp', '')[:10]}] "
            f"[情绪:{e.get('emotion_tag', '')}] "
            f"[生活:{e.get('life_event_tag', '') or '无'}] "
            f"[价值观:{e.get('value_signal_tag', '') or '无'}] "
            f"{e.get('content', '')}"
            for e in entries
        )
        prompt = f"以下是用户最近 20 条对话记录：\n\n{entries_text}\n\n请提炼模式摘要。"

        response = await self.client.chat.completions.create(
            model=MODEL_FAST,
            max_tokens=2000,
            tools=[CRYSTALLIZE_TOOL],
            tool_choice={"type": "function", "function": {"name": "crystallize_memory"}},
            messages=[{"role": "user", "content": prompt}],
        )
        tool_calls = response.choices[0].message.tool_calls
        if tool_calls and tool_calls[0].function.name == "crystallize_memory":
            summary = json.loads(tool_calls[0].function.arguments)
            self.db.add_warm_tier_summary(user_id, summary)
            self.db.mark_crystallized([e["id"] for e in entries])
            return


# ── Helpers ───────────────────────────────────────────────────────────────────

def _format_writer_db(writers: List[Dict]) -> str:
    parts = []
    for w in writers:
        parts.append(f"**{w['name']}（{w.get('name_en', '')}）** [id: {w['writer_id']}]")
        parts.append(f"  适合性格：{w.get('suitable_personality', '')}")
        parts.append(f"  适合状态：{w.get('suitable_life_state', '')}")
        parts.append(f"  价值观共鸣：{w.get('value_resonance', '')}")
        parts.append(f"  适合时机：{w.get('suitable_timing', '')}")
        parts.append(f"  不适合：{w.get('contra_indicators', '')}")
        parts.append(f"  推荐风格：{w.get('recommendation_style', '')}")
        parts.append("")
    return "\n".join(parts)


def _parse_recommended_writer(response_text: str) -> Optional[str]:
    """Extract writer_id from trailing JSON in recommend response."""
    import re
    match = re.search(r'\{"recommended_writer":\s*"([^"]+)"\}', response_text)
    return match.group(1) if match else None
