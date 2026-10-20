# Base común: Chat en microservicios

Desde esta carpeta (`03_Comunicación/demo`), con Docker iniciado y GNU Make instalado:

```sh
make up    # Construye y levanta la demo en segundo plano
make down  # Detiene y elimina los contenedores; conserva los datos
```

Estos comandos administran la demo completa: Users, Channels, Messages, Media,
sus bases de datos y RabbitMQ. `make down` conserva los volúmenes persistentes.

Esta es la base funcional entregada al inicio de la Unidad 3. Los equipos la
levantan con `docker compose up --build`. Está formada por tres microservicios
independientes, cada uno dueño de sus datos:

| Servicio | Puerto | Responsabilidad |
| --- | --- | --- |
| `users` | 8001 | Crear y consultar usuarios |
| `channels` | 8002 | Crear y consultar canales |
| `messages` | 8003 | Crear y listar mensajes |
| `media` | 8004 | Adjuntos, galería por canal, cuotas y previews OpenGraph |

La documentación OpenAPI de cada servicio está disponible en
`http://localhost:{8001,8002,8003}/docs`. El servicio `messages` consulta las
APIs de `users` y `channels` para validar las referencias antes de crear un
mensaje. Ningún servicio accede a los datos internos de otro.

Cada servicio usa su propia instancia MongoDB y volumen Docker: `users-data`,
`channels-data` y `messages-data`.

La extensión Media agrega `media-data`, el volumen binario `media-storage` y
RabbitMQ con su volumen `rabbitmq-data`. Messages publica eventos mediante un
outbox persistente y Media consume `chat.message.created` en segundo plano.
Las rutas originales de usuarios, canales y mensajes conservan sus contratos.

Consulta [Media: ejecución, contratos, límites y pruebas](media/README.md).
Swagger de Media: http://localhost:8004/docs. Sus operaciones requieren el header
`X-User-Id` de un usuario existente (identificación de demo, no autenticación).

## Cómo extenderla

Cada grupo crea un servicio propio y lo agrega al `docker-compose.yaml` con la
red `microsvcs`. La capacidad puede publicar rutas propias y consumir los
eventos de los servicios base; no debe leer ni escribir el estado de `users`,
`channels` o `messages`.

Cada servicio expone `GET /api/v1/events` solo para explorar su contrato
localmente. En las unidades de implementación se sustituye por un consumidor de
broker.
