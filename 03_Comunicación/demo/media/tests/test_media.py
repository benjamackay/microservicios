import io
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4
from unittest.mock import Mock

import mongomock
import pytest
from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import main, previews, worker

USER, OTHER, CHANNEL, MESSAGE = [str(uuid4()) for _ in range(4)]
PDF = b"%PDF-1.4\nTest PDF content\n%%EOF"


@pytest.fixture
def api(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "db", mongomock.MongoClient().chat_media)
    monkeypatch.setattr(main, "STORAGE", tmp_path)
    monkeypatch.setattr(main, "validate_resource", lambda *args: None)
    # Sin lifespan: estas pruebas aíslan APIs y trabajos del broker real.
    return TestClient(main.app, headers={"X-User-Id": USER})


def upload(api, payload=PDF, mime="application/pdf"):
    return api.post(f"/api/v1/channels/{CHANNEL}/attachments",
                    files={"file": ("../informe.pdf", payload, mime)})


def test_file_roundtrip_owner_and_quota(api):
    response = upload(api)
    assert response.status_code == 201, response.text
    doc = response.json()
    assert doc["filename"] == "informe.pdf"
    assert api.get(doc["download_url"]).content == PDF
    assert api.get(f"/api/v1/channels/{CHANNEL}/quota").json()["used_bytes"] == len(PDF)
    path = f"/api/v1/attachments/{doc['id']}"
    assert api.delete(path, headers={"X-User-Id": OTHER}).status_code == 403
    assert api.delete(path).status_code == 204
    assert api.get(doc["download_url"]).status_code == 404
    assert api.get(f"/api/v1/channels/{CHANNEL}/quota").json()["used_bytes"] == 0
    events = main.db.attachments.find_one()["outbox"]
    assert len(events) == 2 and all(not e["published"] for e in events)


@pytest.mark.parametrize("payload,mime,code", [
    (b"", "application/pdf", 422), (b"fake pdf", "application/pdf", 415),
    (PDF, "image/png", 415), (b"<script>alert(1)</script>", "text/html", 415)])
def test_rejected_files_leave_no_data(api, payload, mime, code):
    assert upload(api, payload, mime).status_code == code
    assert main.db.attachments.count_documents({}) == 0
    assert list(main.STORAGE.iterdir()) == []


def test_size_and_quota_limits(api, monkeypatch):
    monkeypatch.setattr(main, "MAX_FILE", 5)
    assert upload(api).status_code == 413
    monkeypatch.setattr(main, "MAX_FILE", 1000)
    monkeypatch.setattr(main, "QUOTA", len(PDF))
    assert upload(api).status_code == 201
    assert upload(api).status_code == 413
    assert len(list(main.STORAGE.iterdir())) == 1


def test_concurrent_quota_reservation_and_idempotent_release(api, monkeypatch):
    monkeypatch.setattr(main, "QUOTA", 10)
    def attempt(index):
        try:
            main.reserve(CHANNEL, str(index), 6)
            return True
        except HTTPException:
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, range(20))) == 1
    ledger = main.db.quotas.find_one()
    winner = next(iter(ledger["reservations"]))
    main.release(CHANNEL, winner)
    main.release(CHANNEL, winner)
    assert main.db.quotas.find_one()["used_bytes"] == 0


def test_failed_metadata_write_rolls_back(api, monkeypatch):
    monkeypatch.setattr(main.db.attachments, "insert_one", Mock(side_effect=RuntimeError("write failed")))
    with pytest.raises(RuntimeError):
        upload(api)
    assert list(main.STORAGE.iterdir()) == []
    assert main.db.quotas.find_one()["used_bytes"] == 0


def test_reconcile_interrupted_upload_and_delete(api):
    main.reserve(CHANNEL, "orphan", 3)
    main.db.quotas.update_one({"_id": CHANNEL}, {"$set": {"reservations.orphan.at": 0}})
    (main.STORAGE / "orphan").write_bytes(b"abc")
    doc = upload(api).json()
    main.db.attachments.update_one({"_id": doc["id"]}, {"$set": {"status": "deleting"}})
    main.reconcile()
    main.reconcile()
    assert list(main.STORAGE.iterdir()) == []
    assert main.db.quotas.find_one()["used_bytes"] == 0
    assert len(main.db.attachments.find_one()["outbox"]) == 2


def message_event(url="https://example.com/"):
    return {"type": "chat.message.created", "data": {
        "id": MESSAGE, "channel_id": CHANNEL, "content": f"Mira {url} {url}"}}


def test_duplicate_event_cache_and_channel_association(api, monkeypatch):
    generate = Mock(return_value={"url": "https://example.com/", "title": "Ejemplo", "image_url": None})
    monkeypatch.setattr(worker, "generate_preview", generate)
    worker.enqueue(message_event())
    worker.enqueue(message_event())
    assert main.db.links.count_documents({}) == 1
    worker.process_job()
    assert main.db.links.find_one()["status"] == "ready"
    worker.process_job()
    assert len(main.db.links.find_one()["outbox"]) == 1
    assert api.get("/api/v1/links/preview", params={"url": "https://example.com"}).status_code == 200
    event = message_event()
    event["data"]["id"] = str(uuid4())
    worker.enqueue(event)
    worker.process_job()
    assert generate.call_count == 1
    assert main.db.links.count_documents({"status": "ready"}) == 2


def test_preview_retries_are_bounded(api, monkeypatch):
    monkeypatch.setattr(worker, "generate_preview", Mock(side_effect=ValueError("blocked")))
    worker.enqueue(message_event())
    for _ in range(worker.MAX_ATTEMPTS + 2):
        main.db.links.update_many({}, {"$set": {"next_attempt": 0}})
        worker.process_job()
    job = main.db.links.find_one()
    assert job["status"] == "failed" and job["attempts"] == worker.MAX_ATTEMPTS


def test_missing_expired_and_invalid_preview(api):
    assert api.get("/api/v1/links/preview", params={"url": "file:///etc/passwd"}).status_code == 422
    assert api.get("/api/v1/links/preview", params={"url": "https://example.com"}).status_code == 404
    main.db.previews.insert_one({"_id": previews.url_id("https://example.com/"), "expires_at": 0})
    assert api.get("/api/v1/links/preview", params={"url": "https://example.com"}).status_code == 404


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "0.0.0.0", "224.0.0.1"])
def test_ssrf_private_dns_rejected(monkeypatch, address):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", (address, 80))])
    with pytest.raises(ValueError):
        previews.public_address("example.com", 80)


def test_redirect_cannot_reach_private_address(monkeypatch):
    addresses = Mock(side_effect=["93.184.216.34", ValueError("private")])
    monkeypatch.setattr(previews, "public_address", addresses)
    response = Mock(status=302)
    response.getheader.return_value = "http://127.0.0.1/private"
    connection = Mock()
    connection.getresponse.return_value = response
    monkeypatch.setattr(previews.http.client, "HTTPConnection", Mock(return_value=connection))
    with pytest.raises(ValueError):
        previews.fetch_html("http://example.com/")
    assert connection.request.call_count == 1


def test_public_dns_address_accepted(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])
    assert previews.public_address("example.com", 443) == "93.184.216.34"


def test_recovered_lease_cannot_exceed_max_attempts(api):
    worker.enqueue(message_event())
    main.db.links.update_many({}, {"$set": {"status": "processing", "attempts": worker.MAX_ATTEMPTS}})
    worker.process_job()
    assert main.db.links.find_one()["status"] == "failed"


@pytest.mark.parametrize("value", [[], None, 1, {"type": "chat.message.created", "data": []}])
def test_invalid_event_shape_rejected(value):
    with pytest.raises(ValueError):
        worker.enqueue(value)


def test_open_graph_and_fallback(monkeypatch):
    monkeypatch.setattr(previews, "fetch_html", lambda url: (
        '<title>Fallback</title><meta property="og:title" content="Hola &amp; mundo">'
        '<meta name="description" content="Descripción">', url))
    result = previews.generate_preview("https://example.com/")
    assert result["title"] == "Hola & mundo"
    assert result["description"] == "Descripción"


def test_outbox_retained_when_broker_fails(api, monkeypatch):
    upload(api)
    monkeypatch.setattr(worker, "broker", Mock(side_effect=ConnectionError("offline")))
    with pytest.raises(ConnectionError):
        worker.publish_pending()
    assert main.db.attachments.find_one()["outbox"][0]["published"] is False
    channel, connection = Mock(), Mock()
    monkeypatch.setattr(worker, "broker", lambda: (connection, channel))
    worker.publish_pending()
    assert main.db.attachments.find_one()["outbox"][0]["published"] is True
    worker.publish_pending()
    assert channel.basic_publish.call_count == 1


def test_missing_user_and_invalid_channel(api):
    assert api.get(f"/api/v1/channels/{CHANNEL}/quota", headers={"X-User-Id": "invalid"}).status_code == 422
    assert api.get("/api/v1/channels/invalid/quota").status_code == 422
    assert TestClient(main.app).get(f"/api/v1/channels/{CHANNEL}/quota").status_code == 422
