"""Servicio dueño de mensajes; usa APIs públicas para validar referencias."""
import os
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
from uuid import uuid4

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field
from pymongo import MongoClient

from .outbox import relay

stop = threading.Event()


@asynccontextmanager
async def lifespan(app):
    database.command("ping")
    stop.clear()
    thread = threading.Thread(target=relay, args=(messages, stop), daemon=True)
    thread.start()
    yield
    stop.set()
    thread.join(timeout=6)


app = FastAPI(title="Chat Messages", version="1.0.0", lifespan=lifespan)
database = MongoClient(os.environ["MONGODB_URL"]).chat_messages
messages = database.messages
USERS_URL = os.environ.get("USERS_URL", "http://users:8000")
CHANNELS_URL = os.environ.get("CHANNELS_URL", "http://channels:8000")


class MessageCreate(BaseModel):
    author_id: str
    content: str = Field(min_length=1, max_length=4_000)


def resource_exists(base_url: str, path: str) -> bool:
    try:
        with urlopen(f"{base_url}{path}", timeout=2) as response:
            return response.status == 200
    except HTTPError as error:
        if error.code == 404:
            return False
        raise HTTPException(status_code=502, detail="Dependent service failed")
    except URLError:
        raise HTTPException(status_code=503, detail="Dependent service unavailable")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/v1/channels/{channel_id}/messages", status_code=status.HTTP_201_CREATED)
def create_message(channel_id: str, body: MessageCreate):
    if not resource_exists(CHANNELS_URL, f"/api/v1/channels/{channel_id}"):
        raise HTTPException(status_code=404, detail="Channel not found")
    if not resource_exists(USERS_URL, f"/api/v1/users/{body.author_id}"):
        raise HTTPException(status_code=422, detail="Author does not exist")
    message = {"id": str(uuid4()), "channel_id": channel_id,
               "created_at": datetime.now(timezone.utc).isoformat(),
               **body.model_dump()}
    event = {"id": str(uuid4()), "type": "chat.message.created",
             "subject_id": message["id"], "occurred_at": message["created_at"],
             "data": message.copy(), "published": False}
    # Una única escritura persiste mensaje y evento; no requiere replica set.
    messages.insert_one({**message, "event": event})
    return message


@app.get("/api/v1/channels/{channel_id}/messages")
def list_messages(channel_id: str):
    return list(messages.find({"channel_id": channel_id}, {"_id": 0, "event": 0}))


@app.get("/api/v1/events")
def list_events():
    return [doc["event"] for doc in messages.find(
        {"event": {"$exists": True}}, {"_id": 0, "event": 1}).sort("created_at", -1).limit(100)]
