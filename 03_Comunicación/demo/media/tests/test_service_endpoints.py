"""Pruebas de servicio: dos casos por cada endpoint público de Media."""
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app import main
from app.previews import url_id
from conftest import CHANNEL, OTHER, USER


PDF = b"%PDF-1.4\nContenido de prueba\n%%EOF"


def upload(api, payload=PDF, mime="application/pdf", filename="test.pdf"):
    return api.post(
        f"/api/v1/channels/{CHANNEL}/attachments",
        files={"file": (filename, payload, mime)},
    )


def test_health_returns_ok(api):
    assert api.get("/health").json() == {"status": "ok"}


def test_health_reports_database_failure(api, monkeypatch):
    monkeypatch.setattr(main.db, "command", Mock(side_effect=RuntimeError("offline")))
    assert api.get("/health").status_code == 503


def test_upload_accepts_supported_file(api):
    response = upload(api)
    assert response.status_code == 201
    assert response.json()["filename"] == "test.pdf"


@pytest.mark.parametrize("payload,mime,status", [
    (b"", "application/pdf", 422),
    (b"not a pdf", "application/pdf", 415),
])
def test_upload_rejects_invalid_files(api, payload, mime, status):
    assert upload(api, payload, mime).status_code == status
    assert main.db.attachments.count_documents({}) == 0


def test_gallery_lists_channel_files(api, uploaded):
    response = api.get(
        f"/api/v1/channels/{CHANNEL}/attachments",
        params={"category": "DOCUMENT"},
    )
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [uploaded["id"]]


def test_gallery_rejects_invalid_channel(api):
    assert api.get("/api/v1/channels/no-es-uuid/attachments").status_code == 422


def test_download_returns_uploaded_content(api, uploaded):
    response = api.get(uploaded["download_url"])
    assert response.status_code == 200
    assert response.content == b"hola"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_download_returns_not_found_for_unknown_file(api):
    assert api.get(f"/api/v1/attachments/{uuid4()}/download").status_code == 404


def test_delete_by_owner_removes_file_and_releases_quota(api, uploaded):
    response = api.delete(f"/api/v1/attachments/{uploaded['id']}")
    assert response.status_code == 204
    assert api.get(f"/api/v1/channels/{CHANNEL}/quota").json()["used_bytes"] == 0


def test_delete_by_another_user_is_forbidden(api, uploaded):
    response = api.delete(
        f"/api/v1/attachments/{uploaded['id']}",
        headers={"X-User-Id": OTHER},
    )
    assert response.status_code == 403


def test_quota_reports_empty_channel(api):
    response = api.get(f"/api/v1/channels/{CHANNEL}/quota")
    assert response.status_code == 200
    assert response.json()["used_bytes"] == 0


def test_quota_rejects_invalid_channel(api):
    assert api.get("/api/v1/channels/not-a-uuid/quota").status_code == 422


def test_preview_returns_cached_metadata(api):
    url = "https://example.com/"
    main.db.previews.insert_one({
        "_id": url_id(url), "url": url, "title": "Ejemplo",
        "description": "Página de ejemplo", "expires_at": 9999999999,
    })
    response = api.get("/api/v1/links/preview", params={"url": url})
    assert response.status_code == 200
    assert response.json()["title"] == "Ejemplo"


def test_preview_rejects_invalid_url(api):
    assert api.get(
        "/api/v1/links/preview", params={"url": "file:///etc/passwd"}
    ).status_code == 422


def test_events_returns_persisted_events(api, monkeypatch):
    sample = {"id": str(uuid4()), "type": "attachment.uploaded"}
    monkeypatch.setattr(main.db.attachments, "aggregate", lambda _pipeline: [sample])
    assert api.get("/api/v1/events").json() == [sample]


def test_events_rejects_out_of_range_limit(api):
    assert api.get("/api/v1/events", params={"limit": 0}).status_code == 422


def test_link_jobs_lists_channel_jobs(api):
    main.db.links.insert_one({"_id": "job-1", "id": "job-1", "channel_id": CHANNEL,
                              "url": "https://example.com", "status": "pending"})
    response = api.get("/api/v1/links/jobs", params={"channel_id": CHANNEL})
    assert response.status_code == 200
    assert response.json()[0]["url"] == "https://example.com"


def test_link_jobs_rejects_invalid_channel(api):
    response = api.get("/api/v1/links/jobs", params={"channel_id": "invalid"})
    assert response.status_code == 422


def test_summary_request_forwards_user_and_payload(api, monkeypatch):
    forward = Mock(return_value={"summary_id": "s-1"})
    monkeypatch.setattr(main, "forward_to_service", forward)
    response = api.post(f"/api/v1/ui/channels/{CHANNEL}/summaries", json={})
    assert response.status_code == 202
    assert response.json() == {"summary_id": "s-1"}
    assert forward.call_args.args[1:3] == ("POST", f"/api/v1/channels/{CHANNEL}/summaries")
    assert forward.call_args.args[3]["user_id"] == USER


def test_summary_request_rejects_unexpected_fields(api, monkeypatch):
    forward = Mock()
    monkeypatch.setattr(main, "forward_to_service", forward)
    response = api.post(f"/api/v1/ui/channels/{CHANNEL}/summaries", json={"extra": True})
    assert response.status_code == 422
    forward.assert_not_called()


def test_summary_list_forwards_channel(api, monkeypatch):
    monkeypatch.setattr(main, "forward_to_service", Mock(return_value=[]))
    response = api.get(f"/api/v1/ui/channels/{CHANNEL}/summaries")
    assert response.status_code == 200
    assert response.json() == []


def test_summary_list_rejects_invalid_user(api):
    response = api.get(
        f"/api/v1/ui/channels/{CHANNEL}/summaries",
        headers={"X-User-Id": "bad-user"},
    )
    assert response.status_code == 422


def test_summary_detail_forwards_identifier(api, monkeypatch):
    summary_id = str(uuid4())
    monkeypatch.setattr(main, "forward_to_service", Mock(return_value={"id": summary_id}))
    response = api.get(f"/api/v1/ui/summaries/{summary_id}")
    assert response.status_code == 200
    assert response.json()["id"] == summary_id


def test_summary_detail_rejects_invalid_identifier(api):
    assert api.get("/api/v1/ui/summaries/nope").status_code == 422


def test_poll_creation_forwards_channel_and_author(api, monkeypatch):
    poll = {"poll_id": str(uuid4()), "status": "open"}
    forward = Mock(return_value=poll)
    monkeypatch.setattr(main, "forward_to_service", forward)
    response = api.post(
        f"/api/v1/ui/channels/{CHANNEL}/polls",
        json={"question": "¿Vamos?", "options": ["Sí", "No"], "duration_minutes": 10},
    )
    assert response.status_code == 200
    assert response.json() == poll
    assert forward.call_args.args[3]["author_id"] == USER


def test_poll_creation_rejects_unexpected_fields(api, monkeypatch):
    forward = Mock()
    monkeypatch.setattr(main, "forward_to_service", forward)
    response = api.post(
        f"/api/v1/ui/channels/{CHANNEL}/polls",
        json={"question": "¿Vamos?", "options": ["Sí", "No"], "private": True},
    )
    assert response.status_code == 422
    forward.assert_not_called()


def test_poll_detail_forwards_identifier(api, monkeypatch):
    poll_id = str(uuid4())
    monkeypatch.setattr(main, "forward_to_service", Mock(return_value={"poll_id": poll_id}))
    response = api.get(f"/api/v1/ui/polls/{poll_id}")
    assert response.status_code == 200
    assert response.json()["poll_id"] == poll_id


def test_poll_detail_rejects_invalid_identifier(api):
    assert api.get("/api/v1/ui/polls/not-a-uuid").status_code == 422


def test_poll_vote_forwards_voter_and_option(api, monkeypatch):
    poll_id, option_id = str(uuid4()), str(uuid4())
    forward = Mock(return_value={"poll_id": poll_id})
    monkeypatch.setattr(main, "forward_to_service", forward)
    response = api.post(
        f"/api/v1/ui/polls/{poll_id}/votes", json={"option_id": option_id}
    )
    assert response.status_code == 200
    assert forward.call_args.args[3] == {"option_id": option_id, "voter_id": USER}


def test_poll_vote_rejects_extra_fields(api, monkeypatch):
    forward = Mock()
    monkeypatch.setattr(main, "forward_to_service", forward)
    response = api.post(
        f"/api/v1/ui/polls/{uuid4()}/votes",
        json={"option_id": str(uuid4()), "voter_id": OTHER},
    )
    assert response.status_code == 422
    forward.assert_not_called()


def test_poll_close_forwards_author(api, monkeypatch):
    poll_id = str(uuid4())
    forward = Mock(return_value={"poll_id": poll_id, "status": "closed"})
    monkeypatch.setattr(main, "forward_to_service", forward)
    response = api.post(f"/api/v1/ui/polls/{poll_id}/close")
    assert response.status_code == 200
    assert forward.call_args.args[3] == {"author_id": USER}


def test_poll_close_rejects_invalid_identifier(api):
    assert api.post("/api/v1/ui/polls/invalid/close").status_code == 422


def test_requires_demo_user_header(api):
    response = api.get(
        f"/api/v1/channels/{CHANNEL}/quota", headers={"X-User-Id": ""}
    )
    assert response.status_code == 422
