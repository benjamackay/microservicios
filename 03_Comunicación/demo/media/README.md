# Media Service

Extensión del chat en el puerto **8004**: adjuntos por canal, galería y previews
OpenGraph automáticos. FastAPI, MongoDB propio (`chat_media`), volumen binario
`media-storage` y RabbitMQ. No accede a las bases de datos del núcleo.

## Ejecutar

Desde `03_Comunicación/demo`:

```powershell
docker compose up -d --build
```

- Swagger: http://localhost:8004/docs
- Salud de Media y su MongoDB: http://localhost:8004/health
- RabbitMQ: http://localhost:15672 (usuario `chat`, contraseña `chat`, solo demo).

El Compose conserva las bases de datos, archivos y cola en volúmenes persistentes.
`docker compose down` conserva esos volúmenes; `down -v` los elimina.
Messages reintenta conexiones al broker en segundo plano; Media espera a que su
MongoDB y RabbitMQ estén saludables. Si el núcleo está caído, las rutas que
validan referencias responden 503.

## Contrato REST

Las rutas de Media, excepto `/health`, requieren `X-User-Id`, con el UUID de un
usuario creado en `users`. Las rutas por canal también validan su existencia.

| Método | Ruta | Resultado |
| --- | --- | --- |
| POST | `/api/v1/channels/{id}/attachments` | 201, multipart con campo `file` |
| GET | `/api/v1/channels/{id}/attachments` | 200, galería con `items`, `limit`, `offset` |
| GET | `/api/v1/attachments/{id}/download` | 200, descarga binaria |
| DELETE | `/api/v1/attachments/{id}` | 204, solo el uploader |
| GET | `/api/v1/channels/{id}/quota` | 200, cuota usada y disponible |
| GET | `/api/v1/links/preview?url=...` | 200, preview vigente en caché; 404 si no existe/expiró |
| GET | `/api/v1/links/jobs?channel_id=...` | 200, estado y error de trabajos de previews |
| GET | `/api/v1/events?limit=20` | 200, últimos eventos persistidos |

Galería: `category=IMAGE|DOCUMENT|LINK`, `limit=1..100` (20 por defecto),
`offset>=0`. Sin categoría combina archivos y enlaces. Los enlaces se asocian al
mensaje y canal originales; los archivos se asocian al canal, no a un mensaje.

Archivos permitidos: PNG, JPEG, GIF, WebP, PDF y texto UTF-8. Se inspecciona la
cabecera binaria (hasta 8 KiB), se contrasta con el MIME declarado y se calcula
SHA-256. Esto no sustituye un antivirus ni una validación completa del formato.
No se sirven HTML/SVG ejecutables. Las descargas usan `Content-Disposition:
attachment` y `nosniff`. Las rutas de disco usan UUID generado por el servidor.

Errores: 404 referencia inexistente; 403 borrado por otro uploader; 413 archivo,
cuota o cantidad excedida; 415 tipo no permitido; 422 entrada inválida; 502/503
fallo del núcleo. `download_url` es relativa al servicio Media y requiere el header.

### Alcance de identidad

**X-User-Id no autentica:** el núcleo no tiene tokens ni membresía. Cualquier
persona puede declarar el ID de un usuario existente. La demo permite leer y
subir a cualquier canal existente; el control de borrado compara el uploader
declarado. Para acceso privado real se necesita autenticación verificable y un
contrato de membresía en Channels. No se implementa identidad dentro de Media.

## Eventos y recuperación

Exchange `chat`, tipo `topic`, durable. Mensajes persistentes y confirmación del
broker. Cola durable `media.message.created`, binding `chat.message.created`;
los eventos inválidos van a `media.invalid` mediante dead lettering.

Messages guarda el evento en el mismo documento del mensaje y responde sin
conectarse a RabbitMQ. Su relay publica después y marca `published` solo tras la
confirmación. El contrato público de los mensajes no expone ese campo interno.
Media usa el mismo principio para los eventos de adjuntos y previews:

- `attachment.{id}.uploaded`
- `attachment.{id}.deleted`
- `link_preview.{id}.generated`

Cada evento contiene `id`, `type`, `subject_id`, `occurred_at` y `data`. La entrega
es **al menos una vez**: si se confirma una publicación pero no se logra marcar
en MongoDB, puede repetirse; consumidores externos deben deduplicar por `id`.
Los eventos publicados por Media no tienen una cola de auditoría propia; para
consumirlos hay que crear una cola y vincularla a `attachment.#` o `link_preview.#`.
El historial también se puede consultar mediante `/api/v1/events`.

El consumidor confirma el evento de mensaje después de persistir los trabajos.
Hay un trabajo determinista por mensaje/URL; una entrega duplicada no genera otra
tarjeta. Los trabajos tienen lease de 120 s y hasta tres intentos con espera
creciente; un fallo queda visible en `/links/jobs`. Se procesan hasta diez URLs
por mensaje. Una misma URL usa caché compartida durante 24 h. Una nueva mención
tras la expiración regenera el caché; GET solo consulta, no dispara scraping.

El scraper permite HTTP(S), puertos 80/443, hasta tres redirecciones y HTML de
1 MiB. Revalida cada destino, rechaza IPs privadas/reservadas y fija la conexión
a la IP pública validada, conservando Host y verificación TLS. Aplica timeouts
de socket y un presupuesto de lectura de 20 s; la resolución DNS usa los tiempos
del sistema. Devuelve la URL de miniatura, no descarga ni almacena la imagen.

Las reservas de cuota se realizan con una actualización condicional atómica de
un documento por canal; incluso subidas simultáneas no pueden superar el límite.
La liberación es idempotente. Se limita además la cantidad de archivos para
acotar el tamaño del registro de reservas. Archivo y MongoDB no comparten una
transacción: Media guarda el temporal, reserva cuota, renombra y persiste datos
con su evento; compensa errores y reconcilia reservas huérfanas de más de diez
minutos cada minuto. Un borrado interrumpido queda `deleting` y se completa al
reanudar. Se conservan tombstones y eventos para auditoría.

Esta demo ejecuta **una instancia de Media con un worker interno** y un volumen
local compartido entre API y worker. No usar múltiples réplicas con discos locales
independientes. Para producción: almacenamiento de objetos, autenticación,
política de retención de eventos/caché, cuotas de temporales y escalado de workers.
La cuota cuenta binarios confirmados/reservados, no temporales simultáneos ni
metadatos de MongoDB. Los límites multipart se aplican al procesar el archivo;
un despliegue expuesto debe limitar también el cuerpo HTTP en su proxy.

## Configuración

Variables configurables en Compose (bytes):

| Variable | Por defecto |
| --- | --- |
| `MAX_FILE_BYTES` | 10485760 (10 MiB) |
| `CHANNEL_QUOTA_BYTES` | 104857600 (100 MiB) |
| `CHANNEL_MAX_FILES` | 1000 |
| `PREVIEW_CACHE_SECONDS` | 86400 |
| `PREVIEW_MAX_ATTEMPTS` | 3 |

Además: `MONGODB_URL`, `RABBITMQ_URL`, `USERS_URL`, `CHANNELS_URL` y
`MEDIA_STORAGE`. Cambiar límites requiere recrear Media; no reducir cuotas por
debajo del uso existente. La consulta de cuota informa el uso reservado actual.

## Verificar los dos flujos

Desde esta carpeta, con Python 3.12 y la demo levantada:

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest tests -q
python smoke.py
```

`smoke.py` crea dos usuarios y un canal, sube un PDF, verifica hash, galería,
descarga por el segundo usuario, borrado por propietario y liberación de cuota.
Después publica una URL y espera el preview creado mediante RabbitMQ, comprobando
caché y galería LINK. Requiere salida a Internet; se puede elegir otra página
con `--preview-url https://...` o verificar solo archivos con `--files-only`.
Deja usuarios, canal, mensaje y metadatos para inspección; elimina el binario.

Las pruebas aisladas usan MongoDB simulado y no requieren Docker. Cubren archivos
inválidos, cuotas concurrentes, recuperación, caché, eventos duplicados, reintentos,
OpenGraph, SSRF y outbox. Para comprobar persistencia real, sube un archivo desde
Swagger, ejecuta `docker compose restart media media-mongodb rabbitmq` y descarga
el mismo ID al recuperar la salud. Para probar el outbox, detén RabbitMQ, publica
un mensaje con URL y vuelve a iniciarlo: el mensaje debe responder 201 y el preview
debe aparecer después de la reconexión.

Referencias de diseño: [confirmaciones RabbitMQ](https://www.rabbitmq.com/docs/confirms)
y [atomicidad de MongoDB](https://www.mongodb.com/docs/manual/core/write-operations-atomicity/).
