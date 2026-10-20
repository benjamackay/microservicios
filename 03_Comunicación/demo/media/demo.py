"""Demostración narrada de los flujos de las diapositivas 10 y 11.

Ejecutar con la demo levantada: python demo.py
Conserva usuarios, canal, archivo y preview para explorarlos en Swagger.
"""
import hashlib
import json
import time

import httpx


def main():
    with httpx.Client(timeout=15, trust_env=False) as client:
        def call(method, port, path, expected=200, **kwargs):
            response = client.request(method, f"http://localhost:{port}{path}", **kwargs)
            assert response.status_code == expected, f"{method} {path}: {response.status_code} {response.text}"
            print(f"  {method} :{port}{path} -> {response.status_code}", flush=True)
            return response

        print("Preparacion: Benjamin, Manuel y un canal compartido", flush=True)
        benjamin = call("POST", 8001, "/api/v1/users", 201, json={"display_name": "Benjamin - presentacion"}).json()["id"]
        manuel = call("POST", 8001, "/api/v1/users", 201, json={"display_name": "Manuel - presentacion"}).json()["id"]
        channel = call("POST", 8002, "/api/v1/channels", 201, json={"name": "Presentacion Media - slides 10 y 11"}).json()["id"]
        benjamin_headers, manuel_headers = {"X-User-Id": benjamin}, {"X-User-Id": manuel}
        path = f"/api/v1/channels/{channel}"

        print("\nSlide 10: Benjamin sube un reporte; Manuel lo encuentra y descarga", flush=True)
        content = b"Reporte de demostracion\nAutor: Benjamin\nDestinatario: Manuel\nMedia Service almacena y entrega este archivo.\n"
        attachment = call("POST", 8004, path + "/attachments", 201, headers=benjamin_headers,
                          files={"file": ("reporte-demo.txt", content, "text/plain")}).json()
        gallery = call("GET", 8004, path + "/attachments?category=DOCUMENT", headers=manuel_headers).json()
        assert any(item["id"] == attachment["id"] for item in gallery["items"])
        downloaded = call("GET", 8004, attachment["download_url"], headers=manuel_headers)
        assert downloaded.content == content
        assert hashlib.sha256(downloaded.content).hexdigest() == attachment["sha256"]
        print(f"  Manuel recibio {len(content)} bytes identicos; SHA-256 verificado.", flush=True)

        print("\nSlide 11: Benjamin envia una URL; Media genera el preview via RabbitMQ", flush=True)
        url = "https://www.python.org/"
        message = call("POST", 8003, path + "/messages", 201,
                       json={"author_id": benjamin, "content": f"Manuel, revisa esta pagina: {url}"}).json()
        print("  El mensaje ya fue aceptado. Esperando el trabajo asincrono...", flush=True)
        deadline = time.monotonic() + 120
        while True:
            response = client.get("http://localhost:8004/api/v1/links/jobs",
                                  headers=manuel_headers, params={"channel_id": channel})
            response.raise_for_status()
            job = next((item for item in response.json() if item["message_id"] == message["id"]), None)
            if job and job["status"] == "ready":
                break
            if job and job["status"] == "failed":
                raise AssertionError(job["last_error"])
            if time.monotonic() >= deadline:
                raise AssertionError("Se agoto la espera del preview")
            time.sleep(1)
        preview = call("GET", 8004, "/api/v1/links/preview", headers=manuel_headers, params={"url": url}).json()
        links = call("GET", 8004, path + "/attachments?category=LINK", headers=manuel_headers).json()
        assert any(item["message_id"] == message["id"] for item in links["items"])
        print(f"  Manuel ve la tarjeta: {preview['title']}", flush=True)
        print(f"  Descripcion: {preview['description'][:180]}", flush=True)
        print("\nDatos para repetir las consultas en Swagger (archivo conservado):", flush=True)
        print(json.dumps({"benjamin_user_id": benjamin, "manuel_user_id": manuel,
                          "channel_id": channel, "attachment_id": attachment["id"],
                          "message_id": message["id"], "preview_url": url}, indent=2), flush=True)


if __name__ == "__main__":
    main()
