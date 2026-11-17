"""Contrato público Messages -> Media para el evento chat.message.created."""
import importlib
import sys
import types
from pathlib import Path

import mongomock
import pytest
from fastapi.testclient import TestClient

from app import main, worker
from conftest import USER, CHANNEL



def test_messages_public_event_is_accepted_by_media(api, monkeypatch):
    messages_app_dir = Path(__file__).resolve().parents[2] / "messages" / "app"
    monkeypatch.setenv("MONGODB_URL", "mongodb://localhost:27017")
    monkeypatch.setenv("RABBITMQ_URL", "amqp://chat:chat@localhost:5672/%2F")
    package = types.ModuleType("chat_contract_messages")
    package.__path__ = [str(messages_app_dir)]
    monkeypatch.setitem(sys.modules, "chat_contract_messages", package)
    messages_service = importlib.import_module("chat_contract_messages.main")
    monkeypatch.setattr(
        messages_service, "messages", mongomock.MongoClient().chat_messages.messages
    )
    monkeypatch.setattr(messages_service, "resource_exists", lambda *_: True)

    messages_api = TestClient(messages_service.app)
    response = messages_api.post(
        f"/api/v1/channels/{CHANNEL}/messages",
        json={"author_id": USER, "content": "Revisar https://contract.example/info"},
    )
    assert response.status_code == 201
    message = response.json()

    # Se usa la API pública de eventos de Messages; el test nunca lee su colección.
    event_response = messages_api.get("/api/v1/events")
    assert event_response.status_code == 200
    event = next(item for item in event_response.json() if item["subject_id"] == message["id"])
    event = {key: value for key, value in event.items() if key != "published"}
    assert event["type"] == "chat.message.created"
    assert event["data"] == message

    worker.enqueue(event)
    job = main.db.links.find_one({"message_id": message["id"]}, {"_id": 0})
    assert job["channel_id"] == CHANNEL
    assert job["url"] == "https://contract.example/info"
    assert job["status"] == "pending"


def test_media_rejects_event_outside_published_contract():
    with pytest.raises(ValueError, match="Tipo de evento inválido"):
        worker.enqueue({"type": "chat.message.updated", "data": {}})
