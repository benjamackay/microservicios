"""Fixtures compartidas para las pruebas de servicio de Media."""
from pathlib import Path
import sys
from uuid import uuid4

import mongomock
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import main

USER, OTHER, CHANNEL, MESSAGE = [str(uuid4()) for _ in range(4)]


@pytest.fixture
def api(monkeypatch, tmp_path):
    """Cada caso usa Mongo simulado y archivos temporales, sin tocar recursos reales."""
    mongo = mongomock.MongoClient()
    monkeypatch.setattr(main, "db", mongo.chat_media)
    monkeypatch.setattr(main, "STORAGE", tmp_path)
    monkeypatch.setattr(main, "validate_resource", lambda *args: None)
    return TestClient(main.app, headers={"X-User-Id": USER})


@pytest.fixture
def uploaded(api):
    response = api.post(
        f"/api/v1/channels/{CHANNEL}/attachments",
        files={"file": ("sample.txt", b"hola", "text/plain")},
    )
    assert response.status_code == 201, response.text
    return response.json()
