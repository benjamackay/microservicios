"""Media: datos propios, APIs del núcleo y trabajos persistentes."""
import hashlib
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

from .previews import normalize_url, url_id

log = logging.getLogger(__name__)
client = MongoClient(os.getenv("MONGODB_URL", "mongodb://localhost:27017"), serverSelectionTimeoutMS=3000)
db = client.chat_media
STORAGE = Path(os.getenv("MEDIA_STORAGE", "/media-storage"))
MAX_FILE = int(os.getenv("MAX_FILE_BYTES", "10485760"))
QUOTA = int(os.getenv("CHANNEL_QUOTA_BYTES", "104857600"))
MAX_ITEMS = int(os.getenv("CHANNEL_MAX_FILES", "1000"))
USERS_URL = os.getenv("USERS_URL", "http://users:8000")
CHANNELS_URL = os.getenv("CHANNELS_URL", "http://channels:8000")
stop = threading.Event()


def now():
    return datetime.now(timezone.utc).isoformat()


def event(kind, subject, data):
    return {"id": str(uuid4()), "type": kind, "subject_id": subject,
            "occurred_at": now(), "data": data, "published": False}


def validate_resource(base, path):
    try:
        response = httpx.get(base + path, timeout=3, follow_redirects=False)
    except httpx.HTTPError:
        raise HTTPException(503, "Servicio del núcleo no disponible")
    if response.status_code == 404:
        raise HTTPException(404, "Usuario o canal inexistente")
    if response.status_code != 200:
        raise HTTPException(502, "Falló la validación con el núcleo")


def identifier(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError:
        raise HTTPException(422, "Identificador UUID inválido")


def current_user(x_user_id: Annotated[str, Header()]) -> str:
    user_id = identifier(x_user_id)
    validate_resource(USERS_URL, f"/api/v1/users/{user_id}")
    return user_id


User = Annotated[str, Depends(current_user)]


def validate_channel(channel_id):
    channel_id = identifier(channel_id)
    validate_resource(CHANNELS_URL, f"/api/v1/channels/{channel_id}")
    return channel_id


def reserve(channel_id, attachment_id, size):
    try:
        db.quotas.update_one({"_id": channel_id}, {"$setOnInsert": {
            "used_bytes": 0, "count": 0, "reservations": {}}}, upsert=True)
    except DuplicateKeyError:
        pass
    result = db.quotas.update_one({"_id": channel_id,
        "used_bytes": {"$lte": QUOTA - size}, "count": {"$lt": MAX_ITEMS}}, {
        "$inc": {"used_bytes": size, "count": 1},
        "$set": {f"reservations.{attachment_id}": {"size": size, "at": time.time()}}})
    if not result.modified_count:
        raise HTTPException(413, "Cuota de almacenamiento o cantidad de archivos excedida")


def release(channel_id, attachment_id):
    ledger = db.quotas.find_one({"_id": channel_id}) or {}
    reservation = ledger.get("reservations", {}).get(attachment_id)
    if reservation:
        # La condición hace que dos eliminaciones concurrentes liberen una sola vez.
        db.quotas.update_one({"_id": channel_id, f"reservations.{attachment_id}": {"$exists": True}}, {
            "$inc": {"used_bytes": -reservation["size"], "count": -1},
            "$unset": {f"reservations.{attachment_id}": ""}})


def detect_type(head: bytes, declared: str | None):
    signatures = [(b"\x89PNG\r\n\x1a\n", "image/png"),
                  (b"\xff\xd8\xff", "image/jpeg"), (b"GIF87a", "image/gif"),
                  (b"GIF89a", "image/gif"), (b"%PDF-", "application/pdf")]
    actual = next((mime for prefix, mime in signatures if head.startswith(prefix)), None)
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        actual = "image/webp"
    if actual is None and declared == "text/plain":
        try:
            head.decode("utf-8")
            if not any(b < 32 and b not in {9, 10, 13} for b in head):
                actual = "text/plain"
        except UnicodeDecodeError:
            pass
    if actual is None or declared not in {actual, "application/octet-stream", None}:
        raise HTTPException(415, "Tipo no permitido o MIME no coincide con el contenido")
    return actual


def public_attachment(doc):
    keys = ("id", "channel_id", "uploader_id", "filename", "mime_type", "size_bytes",
            "sha256", "category", "created_at", "download_url")
    return {key: doc[key] for key in keys}


def finish_delete(doc):
    attachment_id = doc["id"]
    (STORAGE / attachment_id).unlink(missing_ok=True)
    release(doc["channel_id"], attachment_id)
    data = {"attachment_id": attachment_id, "channel_id": doc["channel_id"]}
    db.attachments.update_one({"_id": attachment_id, "status": "deleting"}, {
        "$set": {"status": "deleted", "deleted_at": now()},
        "$push": {"outbox": event(f"attachment.{attachment_id}.deleted", attachment_id, data)}})


def reconcile():
    for doc in db.attachments.find({"status": "deleting"}):
        finish_delete(doc)
    # Operaciones interrumpidas antes de guardar los metadatos. No toca subidas recientes.
    cutoff = time.time() - 600
    for ledger in db.quotas.find():
        for attachment_id, reservation in ledger.get("reservations", {}).items():
            if reservation["at"] < cutoff and not db.attachments.find_one({"_id": attachment_id}):
                (STORAGE / attachment_id).unlink(missing_ok=True)
                release(ledger["_id"], attachment_id)
    for path in STORAGE.glob("*.part"):
        if path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)


@asynccontextmanager
async def lifespan(app):
    from .worker import run, consume
    STORAGE.mkdir(parents=True, exist_ok=True)
    db.command("ping")
    db.attachments.create_index([("channel_id", 1), ("status", 1), ("created_at", -1)])
    db.links.create_index([("channel_id", 1), ("status", 1), ("created_at", -1)])
    stop.clear()
    threads = [threading.Thread(target=target, daemon=True) for target in (run, consume)]
    for thread in threads:
        thread.start()
    yield
    stop.set()
    for thread in threads:
        thread.join(timeout=6)


app = FastAPI(title="Chat Media", version="1.0.0", lifespan=lifespan,
    description="Adjuntos y previews. X-User-Id identifica un usuario de la demo; no es autenticación.")


@app.get("/health")
def health():
    try:
        db.command("ping")
    except Exception:
        raise HTTPException(503, "MongoDB no disponible")
    return {"status": "ok"}


@app.post("/api/v1/channels/{channel_id}/attachments", status_code=201)
def upload(channel_id: str, file: UploadFile, user: User):
    channel_id = validate_channel(channel_id)
    attachment_id = str(uuid4())
    temporary = STORAGE / f"{attachment_id}.part"
    destination = STORAGE / attachment_id
    size, head, digest = 0, b"", hashlib.sha256()
    reserved = False
    try:
        with temporary.open("wb") as output:
            while chunk := file.file.read(65536):
                size += len(chunk)
                if size > MAX_FILE:
                    raise HTTPException(413, "Archivo demasiado grande")
                head = (head + chunk)[:8192]
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if size == 0:
            raise HTTPException(422, "Archivo vacío")
        mime = detect_type(head, file.content_type)
        reserve(channel_id, attachment_id, size)
        reserved = True
        temporary.replace(destination)
        filename = (file.filename or "archivo").replace("\\", "/").split("/")[-1]
        filename = "".join(c for c in filename if c.isprintable())[:200] or "archivo"
        doc = {"_id": attachment_id, "id": attachment_id, "channel_id": channel_id,
               "uploader_id": user, "filename": filename, "mime_type": mime,
               "size_bytes": size, "sha256": digest.hexdigest(),
               "category": "IMAGE" if mime.startswith("image/") else "DOCUMENT",
               "created_at": now(), "status": "ready",
               "download_url": f"/api/v1/attachments/{attachment_id}/download"}
        doc["outbox"] = [event(f"attachment.{attachment_id}.uploaded", attachment_id, public_attachment(doc))]
        db.attachments.insert_one(doc)
        return public_attachment(doc)
    except Exception:
        # Si una escritura tuvo resultado ambiguo, conservar el archivo para reconciliar.
        if reserved and not db.attachments.find_one({"_id": attachment_id}):
            destination.unlink(missing_ok=True)
            release(channel_id, attachment_id)
        raise
    finally:
        temporary.unlink(missing_ok=True)
        file.file.close()


@app.get("/api/v1/channels/{channel_id}/attachments")
def gallery(channel_id: str, user: User,
            category: Literal["IMAGE", "DOCUMENT", "LINK"] | None = None,
            limit: Annotated[int, Query(ge=1, le=100)] = 20,
            offset: Annotated[int, Query(ge=0)] = 0):
    channel_id = validate_channel(channel_id)
    query = {"channel_id": channel_id, "status": "ready"}
    if category:
        query["category"] = category
    pipeline = [{"$match": query}]
    if category is None:
        pipeline.append({"$unionWith": {"coll": "links", "pipeline": [{"$match": query}]}})
    pipeline.extend([{"$sort": {"created_at": -1, "id": 1}}, {"$skip": offset}, {"$limit": limit},
                     {"$project": {"_id": 0, "outbox": 0, "attempts": 0, "next_attempt": 0}}])
    collection = db.links if category == "LINK" else db.attachments
    return {"items": list(collection.aggregate(pipeline)), "limit": limit, "offset": offset}


def get_attachment(attachment_id):
    attachment_id = identifier(attachment_id)
    doc = db.attachments.find_one({"_id": attachment_id, "status": "ready"})
    if not doc:
        raise HTTPException(404, "Adjunto inexistente")
    validate_channel(doc["channel_id"])
    return doc


@app.get("/api/v1/attachments/{attachment_id}/download")
def download(attachment_id: str, user: User):
    doc = get_attachment(attachment_id)
    path = STORAGE / doc["id"]
    if not path.is_file():
        raise HTTPException(404, "Binario no disponible")
    return FileResponse(path, media_type=doc["mime_type"], filename=doc["filename"],
                        headers={"X-Content-Type-Options": "nosniff"})


@app.delete("/api/v1/attachments/{attachment_id}", status_code=204)
def delete(attachment_id: str, user: User):
    doc = get_attachment(attachment_id)
    if doc["uploader_id"] != user:
        raise HTTPException(403, "Solo el usuario que subió el archivo puede eliminarlo")
    db.attachments.update_one({"_id": doc["id"], "status": "ready"}, {"$set": {"status": "deleting"}})
    finish_delete(doc)


@app.get("/api/v1/channels/{channel_id}/quota")
def quota(channel_id: str, user: User):
    channel_id = validate_channel(channel_id)
    ledger = db.quotas.find_one({"_id": channel_id}) or {}
    used = ledger.get("used_bytes", 0)
    return {"channel_id": channel_id, "used_bytes": used, "limit_bytes": QUOTA,
            "available_bytes": max(0, QUOTA - used), "file_count": ledger.get("count", 0),
            "max_files": MAX_ITEMS, "max_file_bytes": MAX_FILE}


@app.get("/api/v1/links/preview")
def preview(user: User, url: Annotated[str, Query(max_length=2048)]):
    try:
        url = normalize_url(url)
    except ValueError as error:
        raise HTTPException(422, str(error))
    doc = db.previews.find_one({"_id": url_id(url), "expires_at": {"$gt": time.time()}}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Preview no disponible: publica un mensaje con esta URL y espera su procesamiento")
    return doc


@app.get("/api/v1/events")
def events(user: User, limit: Annotated[int, Query(ge=1, le=100)] = 20):
    pipeline = [{"$unwind": "$outbox"}, {"$replaceRoot": {"newRoot": "$outbox"}}]
    pipeline += [{"$unionWith": {"coll": "links", "pipeline": pipeline.copy()}},
                 {"$sort": {"occurred_at": -1}}, {"$limit": limit}]
    return list(db.attachments.aggregate(pipeline))


@app.get("/api/v1/links/jobs")
def jobs(user: User, channel_id: str, limit: Annotated[int, Query(ge=1, le=100)] = 20):
    channel_id = validate_channel(channel_id)
    return list(db.links.find({"channel_id": channel_id}, {"_id": 0, "outbox": 0}).sort("created_at", -1).limit(limit))
