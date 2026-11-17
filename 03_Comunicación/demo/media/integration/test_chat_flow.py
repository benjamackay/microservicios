"""Flujo real de Users, Channels, Messages y Media en una red Compose aislada."""
import os
import time
from uuid import uuid4

import httpx

USERS = os.getenv("USERS_URL", "http://users:8000")
CHANNELS = os.getenv("CHANNELS_URL", "http://channels:8000")
MESSAGES = os.getenv("MESSAGES_URL", "http://messages:8000")
MEDIA = os.getenv("MEDIA_URL", "http://media:8000")


def wait_until(description, operation, timeout=75):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            result = operation()
            if result:
                return result
        except (httpx.HTTPError, AssertionError) as error:
            last_error = error
        time.sleep(1)
    raise AssertionError(f"No estuvo disponible {description} en {timeout}s: {last_error}")


def get_json(client, url, **kwargs):
    response = client.get(url, timeout=3, **kwargs)
    response.raise_for_status()
    return response.json()


def test_chat_message_creates_media_preview_and_attachment():
    with httpx.Client(trust_env=False) as client:
        # Consultas que llegan a MongoDB comprueban disponibilidad real de cada API.
        wait_until("Users", lambda: get_json(client, f"{USERS}/api/v1/users") is not None)
        wait_until("Channels", lambda: get_json(client, f"{CHANNELS}/api/v1/channels") is not None)
        wait_until("Messages", lambda: get_json(client, f"{MESSAGES}/api/v1/events") is not None)

        user_response = client.post(
            f"{USERS}/api/v1/users",
            json={"display_name": f"Media Integration {uuid4().hex[:8]}"},
            timeout=5,
        )
        assert user_response.status_code == 201, user_response.text
        user_id = user_response.json()["id"]

        channel_response = client.post(
            f"{CHANNELS}/api/v1/channels",
            json={"name": f"media-integration-{uuid4().hex[:8]}"},
            timeout=5,
        )
        assert channel_response.status_code == 201, channel_response.text
        channel_id = channel_response.json()["id"]

        url = "https://compose-contract.invalid/media-flow"
        message_response = client.post(
            f"{MESSAGES}/api/v1/channels/{channel_id}/messages",
            json={"author_id": user_id, "content": f"Revisar {url}"},
            timeout=10,
        )
        assert message_response.status_code == 201, message_response.text
        message = message_response.json()

        # La API pública de eventos demuestra el contrato que Messages publica.
        event = wait_until(
            "evento de mensaje",
            lambda: next(
                (
                    item
                    for item in get_json(client, f"{MESSAGES}/api/v1/events")
                    if item.get("subject_id") == message["id"]
                    and item.get("type") == "chat.message.created"
                ),
                None,
            ),
        )
        assert event["data"] == message

        headers = {"X-User-Id": user_id}

        def find_media_job():
            page = get_json(
                client,
                f"{MEDIA}/api/v1/links/jobs",
                params={"channel_id": channel_id, "limit": 100},
                headers=headers,
            )
            return next(
                (
                    job for job in page
                    if job["message_id"] == message["id"] and job["url"] == url
                ),
                None,
            )

        job = wait_until("Media consumiendo chat.message.created", find_media_job)
        assert job["channel_id"] == channel_id

        upload = client.post(
            f"{MEDIA}/api/v1/channels/{channel_id}/attachments",
            headers=headers,
            files={"file": ("evidence.txt", b"attachment from compose", "text/plain")},
            timeout=10,
        )
        assert upload.status_code == 201, upload.text
        attachment = upload.json()
        gallery = get_json(
            client,
            f"{MEDIA}/api/v1/channels/{channel_id}/attachments",
            params={"category": "DOCUMENT"},
            headers=headers,
        )
        assert any(item["id"] == attachment["id"] for item in gallery["items"])
