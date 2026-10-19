"""Publicación fuera del request; el evento vive en el mismo documento del mensaje."""
import json
import logging
import os

import pika

log = logging.getLogger(__name__)


def relay(messages, stop):
    while not stop.is_set():
        connection = None
        try:
            pending = list(messages.find({"event.published": False}).limit(50))
            if pending and os.getenv("RABBITMQ_URL"):
                parameters = pika.URLParameters(os.environ["RABBITMQ_URL"])
                parameters.socket_timeout = 3
                parameters.blocked_connection_timeout = 3
                parameters.connection_attempts = 1
                connection = pika.BlockingConnection(parameters)
                channel = connection.channel()
                channel.exchange_declare(exchange="chat", exchange_type="topic", durable=True)
                # La cola existe antes de publicar, aunque Media todavía no haya iniciado.
                channel.queue_declare(queue="media.invalid", durable=True)
                channel.queue_declare(queue="media.message.created", durable=True, arguments={
                    "x-dead-letter-exchange": "", "x-dead-letter-routing-key": "media.invalid"})
                channel.queue_bind(queue="media.message.created", exchange="chat", routing_key="chat.message.created")
                channel.confirm_delivery()
                for doc in pending:
                    event = {key: value for key, value in doc["event"].items() if key != "published"}
                    channel.basic_publish(exchange="chat", routing_key=event["type"],
                        body=json.dumps(event), mandatory=True,
                        properties=pika.BasicProperties(content_type="application/json", delivery_mode=2, message_id=event["id"]))
                    messages.update_one({"_id": doc["_id"]}, {"$set": {"event.published": True}})
        except Exception:
            log.exception("Broker no disponible; los eventos siguen pendientes en MongoDB")
        finally:
            if connection and connection.is_open:
                connection.close()
        stop.wait(2)
