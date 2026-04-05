"""
Recommendation Agent — FastAPI backend
Run: uvicorn main:app --reload --port 8000
"""

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from database import Database
from agent import RecommendationAgent
from models import ChatRequest, ReactionRequest, UserCreate, WriterCreate, NoteCreate, ReminderCreate, ReminderUpdate
from seed_writers import seed

DB_PATH = "recommendation.db"

app = FastAPI(title="Recommendation Agent API", version="0.1.0")
db = Database(DB_PATH)
agent = RecommendationAgent(db)


@app.on_event("startup")
def startup():
    seed(db)  # idempotent: INSERT OR REPLACE


# ── Users ─────────────────────────────────────────────────────────────────────

@app.post("/users", status_code=201)
def create_user(payload: UserCreate):
    db.create_user(payload.user_id, payload.name)
    return {"user_id": payload.user_id, "name": payload.name}


@app.get("/users/{user_id}")
def get_user(user_id: str):
    user = db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


# ── Chat ──────────────────────────────────────────────────────────────────────

@app.get("/users/{user_id}/conversations/{agent_id}")
def get_conversation(user_id: str, agent_id: str):
    user = db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return db.get_conversation_history(user_id, agent_id)


@app.post("/users/{user_id}/chat/{agent_id}")
async def chat(user_id: str, agent_id: str, request: ChatRequest):
    user = db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return StreamingResponse(
        agent.stream_chat(user_id, request.message, agent_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── Profile ───────────────────────────────────────────────────────────────────

@app.get("/users/{user_id}/profile")
def get_profile(user_id: str):
    user = db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    profile = db.get_profile(user_id)
    hot_tier = db.get_hot_tier(user_id)
    return {
        "profile": profile,
        "hot_tier": hot_tier,
        "dialog_count": user.get("dialog_count", 0),
    }


# ── Recommendations ───────────────────────────────────────────────────────────

@app.get("/users/{user_id}/recommendations")
def get_recommendations(user_id: str):
    user = db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return db.get_recommendation_history(user_id)


@app.post("/users/{user_id}/recommendations/{rec_id}/reaction")
def set_reaction(user_id: str, rec_id: int, payload: ReactionRequest):
    db.update_reaction(rec_id, payload.reaction)
    return {"ok": True}


# ── Writers (admin) ───────────────────────────────────────────────────────────

# ── Feed ──────────────────────────────────────────────────────────────────────

@app.get("/feed")
async def get_feed(user_id: str = "demo_user"):
    posts = await agent.generate_feed()
    writers = {w["writer_id"]: w for w in db.get_all_writers()}
    result = []
    for p in posts:
        w = writers.get(p["writer_id"], {})
        result.append({
            "id": p.get("id"),
            "writer_id": p["writer_id"],
            "writer_name": w.get("name", p["writer_id"]),
            "avatar_initial": w.get("avatar_initial", "?"),
            "content": p["content"],
            "generated_at": p.get("generated_at"),
        })
    return result


# ── Notes ──────────────────────────────────────────────────────────────────────

@app.get("/users/{user_id}/notes")
def get_notes(user_id: str):
    if not db.get_user(user_id):
        raise HTTPException(status_code=404, detail="User not found")
    return db.get_notes(user_id)


@app.post("/users/{user_id}/notes", status_code=201)
def add_note(user_id: str, payload: NoteCreate):
    if not db.get_user(user_id):
        raise HTTPException(status_code=404, detail="User not found")
    note_id = db.add_note(user_id, payload.content, payload.source)
    return {"id": note_id}


@app.delete("/users/{user_id}/notes/{note_id}")
def delete_note(user_id: str, note_id: int):
    db.delete_note(note_id)
    return {"ok": True}


# ── Reminders ──────────────────────────────────────────────────────────────────

@app.get("/users/{user_id}/reminders")
def get_reminders(user_id: str):
    if not db.get_user(user_id):
        raise HTTPException(status_code=404, detail="User not found")
    reminders = db.get_reminders(user_id)
    writers = {w["writer_id"]: w for w in db.get_all_writers()}
    for r in reminders:
        w = writers.get(r["writer_id"], {})
        r["writer_name"] = w.get("name", r["writer_id"])
        r["avatar_initial"] = w.get("avatar_initial", "?")
    return reminders


@app.post("/users/{user_id}/reminders", status_code=201)
def add_reminder(user_id: str, payload: ReminderCreate):
    if not db.get_user(user_id):
        raise HTTPException(status_code=404, detail="User not found")
    rem_id = db.add_reminder(user_id, payload.writer_id, payload.remind_time)
    return {"id": rem_id}


@app.put("/users/{user_id}/reminders/{rem_id}")
def update_reminder(user_id: str, rem_id: int, payload: ReminderUpdate):
    db.update_reminder(rem_id, payload.remind_time, payload.enabled)
    return {"ok": True}


@app.delete("/users/{user_id}/reminders/{rem_id}")
def delete_reminder(user_id: str, rem_id: int):
    db.delete_reminder(rem_id)
    return {"ok": True}


# ── Writers (admin) ───────────────────────────────────────────────────────────

@app.get("/writers")
def list_writers():
    return db.get_all_writers()


@app.post("/writers", status_code=201)
def add_writer(payload: WriterCreate):
    data = payload.model_dump()
    writer_id = data.pop("writer_id")
    name = data.pop("name")
    name_en = data.pop("name_en")
    db.upsert_writer(writer_id, name, name_en, data)
    return {"writer_id": writer_id}


@app.get("/writers/{writer_id}")
def get_writer(writer_id: str):
    writer = db.get_writer(writer_id)
    if not writer:
        raise HTTPException(status_code=404, detail="Writer not found")
    return writer
