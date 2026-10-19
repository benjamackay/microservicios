"""Dos flujos punta a punta contra la demo levantada con Docker Compose."""
import argparse
import hashlib
import time
from uuid import uuid4

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview-url", default="https://www.python.org/")
    parser.add_argument("--files-only", action="store_true")
    args = parser.parse_args()
    with httpx.Client(timeout=15, trust_env=False) as client:
        def request(method, port, path, expected=200, **kwargs):
            response = client.request(method, f"http://localhost:{port}{path}", **kwargs)
            assert response.status_code == expected, f"{method} {path}: {response.status_code} {response.text}"
            return response

        user = request("POST", 8001, "/api/v1/users", 201, json={"display_name": "Media demo uploader"}).json()["id"]
        reader = request("POST", 8001, "/api/v1/users", 201, json={"display_name": "Media demo reader"}).json()["id"]
        channel = request("POST", 8002, "/api/v1/channels", 201, json={"name": f"media-{uuid4().hex[:8]}"}).json()["id"]
        owner_headers, reader_headers = {"X-User-Id": user}, {"X-User-Id": reader}
        channel_path = f"/api/v1/channels/{channel}"
        payload = b"%PDF-1.4\nMedia smoke test\n%%EOF"
        attachment = request("POST", 8004, channel_path + "/attachments", 201, headers=owner_headers,
                             files={"file": ("reporte.pdf", payload, "application/pdf")}).json()
        assert attachment["sha256"] == hashlib.sha256(payload).hexdigest()
        downloaded = request("GET", 8004, attachment["download_url"], headers=reader_headers)
        assert downloaded.content == payload
        gallery = request("GET", 8004, channel_path + "/attachments?category=DOCUMENT", headers=reader_headers).json()
        assert any(item["id"] == attachment["id"] for item in gallery["items"])
        request("DELETE", 8004, f"/api/v1/attachments/{attachment['id']}", 403, headers=reader_headers)
        quota = request("GET", 8004, channel_path + "/quota", headers=owner_headers).json()
        assert quota["used_bytes"] == len(payload)
        request("DELETE", 8004, f"/api/v1/attachments/{attachment['id']}", 204, headers=owner_headers)
        assert request("GET", 8004, channel_path + "/quota", headers=owner_headers).json()["used_bytes"] == 0
        request("GET", 8004, attachment["download_url"], 404, headers=reader_headers)
        print("OK: subida, SHA-256, galeria, descarga, permisos de borrado y liberacion de cuota")
        if args.files_only:
            return
        message = request("POST", 8003, channel_path + "/messages", 201,
                          json={"author_id": user, "content": f"Documentacion: {args.preview_url}"}).json()
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            jobs = request("GET", 8004, "/api/v1/links/jobs", headers=reader_headers,
                           params={"channel_id": channel}).json()
            job = next((j for j in jobs if j["message_id"] == message["id"]), None)
            if job and job["status"] == "failed":
                raise AssertionError(f"Preview fallo: {job['last_error']}")
            if job and job["status"] == "ready":
                preview = request("GET", 8004, "/api/v1/links/preview", headers=reader_headers,
                                  params={"url": args.preview_url}).json()
                assert preview["title"]
                gallery = request("GET", 8004, channel_path + "/attachments?category=LINK", headers=reader_headers).json()
                assert any(j["message_id"] == message["id"] for j in gallery["items"])
                combined = request("GET", 8004, channel_path + "/attachments", headers=reader_headers).json()
                assert any(j["category"] == "LINK" for j in combined["items"])
                print("OK: mensaje -> RabbitMQ -> Media -> OpenGraph -> cache y galeria LINK")
                return
            time.sleep(1)
        raise AssertionError("Preview no generado dentro de 120 segundos")


if __name__ == "__main__":
    main()
