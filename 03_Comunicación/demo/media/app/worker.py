"""Inbox por URL/mensaje y outbox embebido: entrega al menos una vez."""
import json
import logging
import os
import time

import pika
from pymongo import ReturnDocument

from . import main as media
from .previews import extract_urls, generate_preview, url_id

log = logging.getLogger(__name__)
QUEUE = "media.message.created"
MAX_ATTEMPTS = int(os.getenv("PREVIEW_MAX_ATTEMPTS", "3"))
CACHE_SECONDS = int(os.getenv("PREVIEW_CACHE_SECONDS", "86400"))


def broker():
    parameters = pika.URLParameters(os.environ["RABBITMQ_URL"])
    parameters.socket_timeout = 3
    parameters.blocked_connection_timeout = 3
    parameters.connection_attempts = 1
    connection = pika.BlockingConnection(parameters)
    channel = connection.channel()
    channel.exchange_declare(exchange="chat", exchange_type="topic", durable=True)
    channel.queue_declare(queue="media.invalid", durable=True)
    channel.queue_declare(queue=QUEUE, durable=True, arguments={
        "x-dead-letter-exchange": "", "x-dead-letter-routing-key": "media.invalid"})
    channel.queue_bind(queue=QUEUE, exchange="chat", routing_key="chat.message.created")
    return connection, channel


def enqueue(event):
    if not isinstance(event, dict) or not isinstance(event.get("data"), dict):
        raise ValueError("Se requiere un objeto de evento con data")
    if event.get("type") != "chat.message.created":
        raise ValueError("Tipo de evento inválido")
    data = event["data"]
    if not isinstance(data["content"], str) or len(data["content"]) > 4000:
        raise ValueError("Contenido de mensaje inválido")
    channel_id = media.identifier(data["channel_id"])
    message_id = media.identifier(data["id"])
    for url in extract_urls(data["content"]):
        job_id = url_id(f"{message_id}:{url}")
        media.db.links.update_one({"_id": job_id}, {"$setOnInsert": {
            "id": job_id, "message_id": message_id, "channel_id": channel_id,
            "category": "LINK", "url": url, "created_at": media.now(),
            "status": "pending", "attempts": 0, "next_attempt": 0, "outbox": []}}, upsert=True)


def consume():
    while not media.stop.is_set():
        connection = None
        try:
            connection, channel = broker()
            channel.basic_qos(prefetch_count=1)
            for method, properties, body in channel.consume(QUEUE, inactivity_timeout=1):
                if media.stop.is_set():
                    break
                if method is None:
                    continue
                try:
                    if len(body) > 65536:
                        raise ValueError("Evento demasiado grande")
                    enqueue(json.loads(body))
                except (ValueError, KeyError, TypeError, media.HTTPException):
                    log.exception("Evento inválido enviado a media.invalid")
                    channel.basic_reject(method.delivery_tag, requeue=False)
                else:
                    channel.basic_ack(method.delivery_tag)
        except Exception:
            # Si falla Mongo, no hay ACK; la conexión cerrada devuelve el evento a la cola.
            log.exception("Consumidor desconectado; se reintentará")
            media.stop.wait(3)
        finally:
            if connection and connection.is_open:
                connection.close()


def process_job():
    current = time.time()
    media.db.links.update_many({"status": "processing", "attempts": {"$gte": MAX_ATTEMPTS},
        "next_attempt": {"$lte": current}}, {"$set": {"status": "failed",
        "last_error": "Se agotaron los intentos tras una interrupción del worker"}})
    doc = media.db.links.find_one_and_update({
        "status": {"$in": ["pending", "processing"]}, "next_attempt": {"$lte": current}}, {
        "$set": {"status": "processing", "next_attempt": current + 120},
        "$inc": {"attempts": 1}}, return_document=ReturnDocument.AFTER)
    if not doc:
        return
    try:
        cached = media.db.previews.find_one({"_id": url_id(doc["url"]), "expires_at": {"$gt": current}})
        if cached:
            cached.pop("_id", None)
        else:
            cached = {**generate_preview(doc["url"]), "generated_at": media.now(),
                      "expires_at": time.time() + CACHE_SECONDS}
            media.db.previews.replace_one({"_id": url_id(doc["url"])}, cached, upsert=True)
        cached.pop("_id", None)
        generated = media.event(f"link_preview.{doc['id']}.generated", doc["id"], {
            "channel_id": doc["channel_id"], "message_id": doc["message_id"], **cached})
        media.db.links.update_one({"_id": doc["id"], "status": "processing"}, {
            "$set": {"status": "ready", "preview": cached}, "$unset": {"last_error": ""},
            "$push": {"outbox": generated}})
    except Exception as error:
        log.warning("Preview falló (intento %s): %s", doc["attempts"], error)
        media.db.links.update_one({"_id": doc["id"]}, {"$set": {
            "status": "failed" if doc["attempts"] >= MAX_ATTEMPTS else "pending",
            "last_error": str(error)[:500], "next_attempt": time.time() + 2 ** doc["attempts"]}})


def publish_pending():
    connection = None
    try:
        pending = []
        for collection in (media.db.attachments, media.db.links):
            for doc in collection.find({"outbox.published": False}).limit(25):
                for index, item in enumerate(doc["outbox"]):
                    if not item["published"]:
                        pending.append((collection, doc["_id"], index, item))
        if not pending:
            return
        connection, channel = broker()
        channel.confirm_delivery()
        for collection, doc_id, index, item in pending:
            body = {key: value for key, value in item.items() if key != "published"}
            channel.basic_publish(exchange="chat", routing_key=item["type"], body=json.dumps(body),
                properties=pika.BasicProperties(content_type="application/json", delivery_mode=2, message_id=item["id"]))
            collection.update_one({"_id": doc_id}, {"$set": {f"outbox.{index}.published": True}})
    finally:
        if connection and connection.is_open:
            connection.close()


def run():
    last_reconcile = 0
    while not media.stop.is_set():
        try:
            if time.time() - last_reconcile > 60:
                media.reconcile()
                last_reconcile = time.time()
            process_job()
            try:
                publish_pending()
            except Exception:
                log.exception("Publicación pendiente: se conserva el outbox")
        except Exception:
            log.exception("Error del trabajador de Media")
        media.stop.wait(1)
