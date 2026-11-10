# Base común: Chat en microservicios

Desde esta carpeta (`03_Comunicación/demo`), con Docker iniciado y GNU Make instalado:

```sh
make up    # Construye y levanta la demo en segundo plano
make down  # Detiene y elimina los contenedores; conserva los datos
```

Estos comandos administran la demo completa: Users, Channels, Messages, Media,
Summaries, Polls, el API Gateway, sus bases de datos y RabbitMQ. `make down`
conserva los volúmenes persistentes.

La interfaz tipo chat está en <http://localhost:8004>. El API Gateway está en
<http://localhost:8007>; su raíz abre Swagger en `/docs`, GraphQL está en
`/api/v1/graphql` y su estado en `/health`. La UI obtiene y modifica los datos de
Users, Channels, Messages, Media, Summaries y Polls a través del Gateway; no
conecta directamente con esos servicios.

| Servicio | Puerto | Responsabilidad |
| --- | --- | --- |
| `users` | 8001 | Crear y consultar usuarios |
| `channels` | 8002 | Crear y consultar canales |
| `messages` | 8003 | Crear y listar mensajes |
| `media` | 8004 | Interfaz, adjuntos, galería, cuotas y previews OpenGraph |
| `summaries` | 8005 | Solicitud y consulta asíncrona de resúmenes |
| `polls-api` | 8006 | Crear encuestas, votar y consultar resultados |
| `api-gateway` | 8007 | Acceso de la UI a los servicios integrados |

La interfaz permite conversar, compartir archivos y enlaces, consultar previews
y cuota, crear encuestas, votar y solicitar resúmenes. Los mensajes, usuarios,
canales, archivos, encuestas y resúmenes se consultan mediante el API Gateway.

Summaries y Polls se mantienen como repositorios Git independientes en
`peers/`. Al clonar este repositorio, `make up` inicializa ambos submódulos
antes de construir los contenedores. Summaries necesita `LLM_API_KEY` en el
entorno para generar resultados con Gemini: copia `.env.example` a `.env` y
agrega allí la llave. Sin ella, `make up` omite Summaries pero levanta los demás
servicios. Polls usa PostgreSQL y publica su API en `http://localhost:8006`.

La extensión Media agrega MongoDB, almacenamiento binario persistente y
RabbitMQ. Messages publica eventos mediante un outbox persistente y Media
consume `chat.message.created` en segundo plano. Cada servicio conserva su
propia base de datos y contrato.

Consulta [Media: ejecución, contratos, límites y pruebas](media/README.md).
Swagger de Media: <http://localhost:8004/docs>; Summaries:
<http://localhost:8005/docs>; Polls: <http://localhost:8006/docs>.

## Cómo extenderla

Cada grupo crea un servicio propio y lo agrega a `docker-compose.yaml` con la
red `microsvcs`. La integración de la UI con servicios debe realizarse desde el
API Gateway; la interfaz consume sus contratos y no llama a los servicios de
otros equipos directamente.
