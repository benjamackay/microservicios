"""Regresión del contrato de Messages y persistencia del evento ante fallos AMQP."""
import importlib
import sys
import types
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import mongomock
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def messages(monkeypatch):
    monkeypatch.setenv("MONGODB_URL", "mongodb://localhost:27017")
    monkeypatch.setenv("RABBITMQ_URL", "amqp://chat:chat@localhost:5672/%2F")
    package = types.ModuleType("messages_service")
    package.__path__ = [str(Path(__file__).resolve().parents[2] / "messages" / "app")]
    monkeypatch.setitem(sys.modules, "messages_service", package)
    service = importlib.import_module("messages_service.main")
    monkeypatch.setattr(service, "messages", mongomock.MongoClient().chat_messages.messages)
    monkeypatch.setattr(service, "resource_exists", lambda *a: True)
    return service


def test_message_response_and_list_hide_outbox(messages):
    api = TestClient(messages.app)
    channel, user = str(uuid4()), str(uuid4())
    response = api.post(f"/api/v1/channels/{channel}/messages", json={"author_id": user, "content": "hola"})
    assert response.status_code == 201
    assert "event" not in response.json() and "_id" not in response.json()
    persisted = messages.messages.find_one()
    assert persisted["event"]["published"] is False
    assert persisted["event"]["data"] == response.json()
    listed = api.get(f"/api/v1/channels/{channel}/messages").json()
    assert listed == [response.json()]


def test_message_relay_retries_after_broker_failure(messages, monkeypatch):
    messages.create_message(str(uuid4()), messages.MessageCreate(author_id=str(uuid4()), content="hello"))
    relay = importlib.import_module("messages_service.outbox")
    monkeypatch.setattr(relay.pika, "BlockingConnection", Mock(side_effect=ConnectionError("offline")))
    stop = Mock()
    stop.is_set.side_effect = [False, True]
    relay.relay(messages.messages, stop)
    doc = messages.messages.find_one()
    assert doc["event"]["published"] is False
    channel, connection = Mock(), Mock()
    connection.channel.return_value = channel
    monkeypatch.setattr(relay.pika, "BlockingConnection", Mock(return_value=connection))
    stop.is_set.side_effect = [False, True]
    relay.relay(messages.messages, stop)
    assert messages.messages.find_one()["event"]["published"] is True
    assert channel.basic_publish.call_count == 1
    stop.is_set.side_effect = [False, True]
    relay.relay(messages.messages, stop)
    assert channel.basic_publish.call_count == 1


@pytest.mark.parametrize("service,body", [
    ("users", {"display_name": "Demo"}), ("channels", {"name": "Demo"})])
def test_core_create_does_not_expose_mongodb_id(monkeypatch, service, body):
    monkeypatch.setenv("MONGODB_URL", "mongodb://localhost:27017")
    path = Path(__file__).resolve().parents[2] / service / "app" / "main.py"
    spec = importlib.util.spec_from_file_location(f"core_{service}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, service, mongomock.MongoClient().database[service])
    api = TestClient(module.app)
    response = api.post(f"/api/v1/{service}", json=body)
    assert response.status_code == 201
    assert "_id" not in response.json()
    assert api.get(f"/api/v1/{service}/{response.json()['id']}").json() == response.json()
