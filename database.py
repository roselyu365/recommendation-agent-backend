import sqlite3
import json
from datetime import datetime
from typing import Optional, List, Dict, Any


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    name TEXT,
                    is_onboarded INTEGER DEFAULT 0,
                    dialog_count INTEGER DEFAULT 0,
                    last_rec_dialog_count INTEGER DEFAULT 0,
                    created_at TEXT
                );

                CREATE TABLE IF NOT EXISTS user_profiles (
                    user_id TEXT PRIMARY KEY,
                    cold_tier TEXT DEFAULT '{}',
                    warm_tier TEXT DEFAULT '[]',
                    updated_at TEXT
                );

                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT,
                    agent_id TEXT DEFAULT 'recommendation',
                    role TEXT,
                    content TEXT,
                    timestamp TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                );

                CREATE TABLE IF NOT EXISTS notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT,
                    content TEXT,
                    source TEXT,
                    created_at TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                );

                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT,
                    writer_id TEXT,
                    remind_time TEXT,
                    enabled INTEGER DEFAULT 1,
                    created_at TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                );

                CREATE TABLE IF NOT EXISTS feed_posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    writer_id TEXT,
                    content TEXT,
                    generated_at TEXT
                );

                CREATE TABLE IF NOT EXISTS episodic_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT,
                    timestamp TEXT,
                    emotion_tag TEXT,
                    life_event_tag TEXT,
                    value_signal_tag TEXT,
                    content TEXT,
                    writer_signal TEXT,
                    is_crystallized INTEGER DEFAULT 0,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                );

                CREATE TABLE IF NOT EXISTS recommendation_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT,
                    timestamp TEXT,
                    writer_id TEXT,
                    recommendation_text TEXT,
                    user_reaction TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                );

                CREATE TABLE IF NOT EXISTS writers (
                    writer_id TEXT PRIMARY KEY,
                    name TEXT,
                    name_en TEXT,
                    data TEXT
                );
            """)

    # ── Users ────────────────────────────────────────────────────────────────

    def create_user(self, user_id: str, name: str):
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO users (user_id, name, created_at) VALUES (?, ?, ?)",
                (user_id, name, datetime.now().isoformat()),
            )
            conn.execute(
                "INSERT OR IGNORE INTO user_profiles (user_id, updated_at) VALUES (?, ?)",
                (user_id, datetime.now().isoformat()),
            )

    def get_user(self, user_id: str) -> Optional[Dict]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
            return dict(row) if row else None

    def mark_onboarded(self, user_id: str):
        with self._get_conn() as conn:
            conn.execute("UPDATE users SET is_onboarded = 1 WHERE user_id = ?", (user_id,))

    def increment_dialog_count(self, user_id: str):
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE users SET dialog_count = dialog_count + 1 WHERE user_id = ?",
                (user_id,),
            )

    def update_last_rec_dialog_count(self, user_id: str):
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE users SET last_rec_dialog_count = dialog_count WHERE user_id = ?",
                (user_id,),
            )

    # ── Profile ──────────────────────────────────────────────────────────────

    def get_profile(self, user_id: str) -> Dict:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM user_profiles WHERE user_id = ?", (user_id,)
            ).fetchone()
            if not row:
                return {"cold_tier": {}, "warm_tier": []}
            return {
                "cold_tier": json.loads(row["cold_tier"]),
                "warm_tier": json.loads(row["warm_tier"]),
                "updated_at": row["updated_at"],
            }

    def update_cold_tier(self, user_id: str, cold_tier: Dict):
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE user_profiles SET cold_tier = ?, updated_at = ? WHERE user_id = ?",
                (json.dumps(cold_tier, ensure_ascii=False), datetime.now().isoformat(), user_id),
            )

    def add_warm_tier_summary(self, user_id: str, summary: Dict):
        profile = self.get_profile(user_id)
        warm_tier = profile.get("warm_tier", [])
        warm_tier.append(summary)
        warm_tier = warm_tier[-5:]  # keep last 5 summaries
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE user_profiles SET warm_tier = ?, updated_at = ? WHERE user_id = ?",
                (json.dumps(warm_tier, ensure_ascii=False), datetime.now().isoformat(), user_id),
            )

    # ── Conversations ────────────────────────────────────────────────────────

    def save_turn(self, user_id: str, role: str, content: str, agent_id: str = "recommendation"):
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO conversations (user_id, agent_id, role, content, timestamp) VALUES (?, ?, ?, ?, ?)",
                (user_id, agent_id, role, content, datetime.now().isoformat()),
            )

    def get_recent_turns(self, user_id: str, agent_id: str = "recommendation", limit: int = 10) -> List[Dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT role, content FROM conversations WHERE user_id = ? AND agent_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, agent_id, limit),
            ).fetchall()
            return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    def get_conversation_history(self, user_id: str, agent_id: str, limit: int = 30) -> List[Dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT role, content, timestamp FROM conversations
                   WHERE user_id = ? AND agent_id = ? ORDER BY id DESC LIMIT ?""",
                (user_id, agent_id, limit),
            ).fetchall()
            return [dict(r) for r in reversed(rows)]

    # ── Episodic log ──────────────────────────────────────────────────────────

    def add_episodic_entry(self, user_id: str, entry: Dict) -> int:
        with self._get_conn() as conn:
            cursor = conn.execute(
                """INSERT INTO episodic_log
                   (user_id, timestamp, emotion_tag, life_event_tag, value_signal_tag, content, writer_signal)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    user_id,
                    entry.get("timestamp", datetime.now().isoformat()),
                    entry.get("emotion_tag"),
                    entry.get("life_event_tag"),
                    entry.get("value_signal_tag"),
                    entry["content"],
                    entry.get("writer_signal"),
                ),
            )
            return cursor.lastrowid

    def get_hot_tier(self, user_id: str, limit: int = 20) -> List[Dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM episodic_log WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
            return [dict(r) for r in reversed(rows)]

    def count_uncrystallized(self, user_id: str) -> int:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM episodic_log WHERE user_id = ? AND is_crystallized = 0",
                (user_id,),
            ).fetchone()
            return row["cnt"]

    def get_uncrystallized(self, user_id: str, limit: int = 20) -> List[Dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM episodic_log WHERE user_id = ? AND is_crystallized = 0
                   ORDER BY id ASC LIMIT ?""",
                (user_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def mark_crystallized(self, entry_ids: List[int]):
        if not entry_ids:
            return
        placeholders = ",".join("?" * len(entry_ids))
        with self._get_conn() as conn:
            conn.execute(
                f"UPDATE episodic_log SET is_crystallized = 1 WHERE id IN ({placeholders})",
                entry_ids,
            )

    # ── Recommendations ───────────────────────────────────────────────────────

    def add_recommendation(self, user_id: str, writer_id: str, text: str) -> int:
        with self._get_conn() as conn:
            cursor = conn.execute(
                """INSERT INTO recommendation_history (user_id, timestamp, writer_id, recommendation_text)
                   VALUES (?, ?, ?, ?)""",
                (user_id, datetime.now().isoformat(), writer_id, text),
            )
            return cursor.lastrowid

    def update_reaction(self, rec_id: int, reaction: str):
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE recommendation_history SET user_reaction = ? WHERE id = ?",
                (reaction, rec_id),
            )

    def get_recommendation_history(self, user_id: str) -> List[Dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM recommendation_history WHERE user_id = ? ORDER BY id DESC",
                (user_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ── Writers ───────────────────────────────────────────────────────────────

    def upsert_writer(self, writer_id: str, name: str, name_en: str, data: Dict):
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO writers (writer_id, name, name_en, data) VALUES (?, ?, ?, ?)",
                (writer_id, name, name_en, json.dumps(data, ensure_ascii=False)),
            )

    def get_all_writers(self) -> List[Dict]:
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM writers").fetchall()
            result = []
            for r in rows:
                entry = {"writer_id": r["writer_id"], "name": r["name"], "name_en": r["name_en"]}
                entry.update(json.loads(r["data"]))
                result.append(entry)
            return result

    # ── Notes ─────────────────────────────────────────────────────────────────

    def add_note(self, user_id: str, content: str, source: str = "") -> int:
        with self._get_conn() as conn:
            cursor = conn.execute(
                "INSERT INTO notes (user_id, content, source, created_at) VALUES (?, ?, ?, ?)",
                (user_id, content, source, datetime.now().isoformat()),
            )
            return cursor.lastrowid

    def get_notes(self, user_id: str) -> List[Dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM notes WHERE user_id = ? ORDER BY id DESC", (user_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def delete_note(self, note_id: int):
        with self._get_conn() as conn:
            conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))

    # ── Reminders ─────────────────────────────────────────────────────────────

    def add_reminder(self, user_id: str, writer_id: str, remind_time: str) -> int:
        with self._get_conn() as conn:
            cursor = conn.execute(
                "INSERT INTO reminders (user_id, writer_id, remind_time, created_at) VALUES (?, ?, ?, ?)",
                (user_id, writer_id, remind_time, datetime.now().isoformat()),
            )
            return cursor.lastrowid

    def get_reminders(self, user_id: str) -> List[Dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM reminders WHERE user_id = ? ORDER BY id DESC", (user_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def update_reminder(self, reminder_id: int, remind_time: str = None, enabled: int = None):
        with self._get_conn() as conn:
            if remind_time is not None:
                conn.execute("UPDATE reminders SET remind_time = ? WHERE id = ?", (remind_time, reminder_id))
            if enabled is not None:
                conn.execute("UPDATE reminders SET enabled = ? WHERE id = ?", (enabled, reminder_id))

    def delete_reminder(self, reminder_id: int):
        with self._get_conn() as conn:
            conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))

    # ── Feed ──────────────────────────────────────────────────────────────────

    def get_fresh_feed(self, max_age_hours: int = 4) -> List[Dict]:
        """Return cached feed if generated within max_age_hours, else empty list."""
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM feed_posts
                   WHERE datetime(generated_at) > datetime('now', ?)
                   ORDER BY id DESC""",
                (f"-{max_age_hours} hours",),
            ).fetchall()
            return [dict(r) for r in rows]

    def save_feed_posts(self, posts: List[Dict]):
        with self._get_conn() as conn:
            conn.execute("DELETE FROM feed_posts")
            for post in posts:
                conn.execute(
                    "INSERT INTO feed_posts (writer_id, content, generated_at) VALUES (?, ?, ?)",
                    (post["writer_id"], post["content"], datetime.now().isoformat()),
                )

    def get_writer(self, writer_id: str) -> Optional[Dict]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM writers WHERE writer_id = ?", (writer_id,)
            ).fetchone()
            if not row:
                return None
            entry = {"writer_id": row["writer_id"], "name": row["name"], "name_en": row["name_en"]}
            entry.update(json.loads(row["data"]))
            return entry
