# Guía completa del proyecto: Warehouse Cloud Platform

Esta guía explica **todo el proyecto desde cero**: qué problema resuelve, cada tecnología (qué es, cómo
funciona y cómo se programa aquí), cada decisión de diseño (por qué se tomó y qué necesidad cubre, con
ejemplos) y cómo se despliega y verifica en AWS. Está escrita para alguien que **no conoce** las
tecnologías: cada concepto se explica antes de usarlo.

> Cómo leerla: las secciones 1-3 dan el mapa general. Las 4-7 explican cada pieza en detalle. La 8
> recorre flujos completos de punta a punta. Las 9-13 cubren aislamiento, rendimiento, pruebas,
> operación y preguntas de defensa. Al final hay un glosario.

## Índice

1. [El problema que resolvemos](#1-el-problema-que-resolvemos)
2. [Visión general de la solución](#2-visión-general-de-la-solución)
3. [Conceptos base (antes de empezar)](#3-conceptos-base-antes-de-empezar)
4. [Tecnologías del software, una por una](#4-tecnologías-del-software-una-por-una)
5. [Patrones de diseño y por qué los usamos](#5-patrones-de-diseño-y-por-qué-los-usamos)
6. [AWS: cada servicio de la nube explicado](#6-aws-cada-servicio-de-la-nube-explicado)
7. [Terraform: la infraestructura como código](#7-terraform-la-infraestructura-como-código)
8. [Recorridos completos de punta a punta](#8-recorridos-completos-de-punta-a-punta)
9. [Aislamiento entre bodegas: cómo se garantiza](#9-aislamiento-entre-bodegas-cómo-se-garantiza)
10. [Rendimiento: 1000-2000 peticiones por segundo](#10-rendimiento-1000-2000-peticiones-por-segundo)
11. [Pruebas y verificación](#11-pruebas-y-verificación)
12. [Operación: desplegar, verificar, destruir](#12-operación-desplegar-verificar-destruir)
13. [Preguntas de defensa](#13-preguntas-de-defensa)
14. [Glosario](#14-glosario)

---

## 1. El problema que resolvemos

Una empresa tiene **varias bodegas** (almacenes) y una **bodega central** que las coordina. Cada bodega
registra sus productos y su inventario (entradas, salidas, reservas). La central necesita:

- saber **cuánto hay de cada producto en todas las bodegas** (inventario global);
- **mover mercancía** de una bodega a otra (transferencias) sin perder ni duplicar unidades;
- generar **reportes** del estado de las bodegas;
- seguir funcionando aunque **algo falle** (una bodega, la red, el servidor de mensajes).

### Requisitos concretos del enunciado (las 3 peticiones)

| # | Requisito | Necesidad real que hay detrás |
|---|---|---|
| R1 | 3 EC2, cada una es una bodega `WH-001..003` | Cada bodega es independiente: si una se cae, las demás siguen |
| R2 | Cada EC2 con su propia VPC (red), con parte pública y privada; 4 VPCs en total | Aislar cada bodega en su propia red, como si fueran sedes físicas distintas |
| R3 | La central es un ECS con su propia VPC pública y privada | La central ejecuta varios servicios que deben escalar sin administrar servidores |
| R4 | Un grupo de seguridad (firewall) por bodega | Controlar quién puede hablar con cada bodega |
| R5 | Docker instalado en cada EC2 | Empaquetar y ejecutar el software igual en cualquier máquina |
| R6 | La central accede a todas las bodegas; las bodegas **no** se ven entre sí | Seguridad: comprometer una bodega no debe permitir atacar otra; todo pasa por la central |
| R7 | Un broker (servidor de mensajes) para muchos eventos: 1000-2000 peticiones/s | Las bodegas generan muchísimos eventos; no se pueden procesar uno a uno de forma síncrona |
| R8 | Si se cae el broker, al volver deben seguir los mensajes | No perder información por una caída |
| R9 | Una cola de respaldo si falla el broker, para no parar el servicio | Las bodegas y la central deben seguir atendiendo aunque el broker esté caído |
| R10 | Si se cae la conexión, al reanudarse enviar pendientes y sincronizar la bodega | La central debe quedar con los datos correctos después de cualquier corte |
| R11 | Sistema de reportes sobre las bodegas | Ver el estado general: qué bodegas están en línea, inventario, transferencias |

### Dónde se cumple cada uno (resumen)

| Req. | Dónde | Sección |
|---|---|---|
| R1, R5 | `infrastructure/terraform/modules/warehouse` + `infrastructure/aws/user_data.sh.tftpl` | 6.8, 6.9 |
| R2 | `modules/vpc` (una instancia del módulo por bodega) | 6.2-6.5 |
| R3 | `modules/vpc` (central) + `modules/central` (ECS) | 6.11 |
| R4 | `modules/warehouse/main.tf` (`aws_security_group`) | 6.7 |
| R6 | `modules/peering` + SG + NACL | 9 |
| R7 | RabbitMQ (`modules/central/broker.tf`) | 4.5, 10 |
| R8 | Colas durables + mensajes persistentes + EFS | 5.10, 6.14 |
| R9 | `outbox` en cada bodega + `command_outbox` en la central | 5.2, 5.11 |
| R10 | Outbox relay + `INVENTORY_SNAPSHOT` | 5.10 |
| R11 | `GET /reports/warehouses` + reporte en S3 | 5.12 |

---

## 2. Visión general de la solución

### 2.1 El dibujo completo

```text
                               Internet (clientes, navegador, pruebas)
                                          │
                     ALB público de la central (balanceador de carga)
        :80 → API central     :8001/:8002/:8003 → bodegas     :15672 → consola del broker
                                          │
 ┌──────────────────────────── VPC CENTRAL 10.0.0.0/16 ─────────────────────────────┐
 │  Subred pública: ALB, NAT                                                         │
 │  Subred privada:                                                                  │
 │    ECS Fargate: central-api ×2 · command-relay · transfer-orchestrator ·         │
 │                 projection-worker ×2 · audit-worker · report-worker               │
 │    RabbitMQ (broker) con datos en EFS ← NLB interno :5672                         │
 │    RDS PostgreSQL (base de datos de la central)                                   │
 └───────────────┬───────────────────────┬───────────────────────┬───────────────────┘
          peering│                peering│                peering│     (NO hay peering entre bodegas)
 ┌───────────────┴──────┐  ┌─────────────┴────────┐  ┌───────────┴──────────┐
 │ VPC WH-001 10.1/16   │  │ VPC WH-002 10.2/16   │  │ VPC WH-003 10.3/16   │
 │ pública: NAT         │  │ pública: NAT         │  │ pública: NAT         │
 │ privada: EC2         │  │ privada: EC2         │  │ privada: EC2         │
 │  Docker:             │  │  (igual)             │  │  (igual)             │
 │  api, postgres,      │  │                      │  │                      │
 │  redis, outbox ×2,   │  │                      │  │                      │
 │  cache-worker        │  │                      │  │                      │
 │ SG propio + NACL     │  │ SG propio + NACL     │  │ SG propio + NACL     │
 └──────────────────────┘  └──────────────────────┘  └──────────────────────┘

 S3 (reportes y archivos) → Lambda (procesa archivos subidos)
 ECR (imágenes Docker) · CloudWatch (logs) · SSM (acceso a las EC2 sin SSH)
```

### 2.2 Las piezas de software

| Pieza | Qué hace | Dónde vive |
|---|---|---|
| `warehouse-api` | API de una bodega: productos, inventario, movimientos, pasos de transferencias | EC2 de cada bodega |
| PostgreSQL de bodega | Guarda los datos de **esa** bodega | EC2 de cada bodega |
| Redis de bodega | Caché de lecturas de inventario | EC2 de cada bodega |
| `outbox relay` | Envía al broker los eventos guardados en la bodega | EC2 de cada bodega |
| `cache-worker` | Borra entradas de caché cuando cambia el inventario | EC2 de cada bodega |
| RabbitMQ | Broker: recibe mensajes y los reparte a quien los necesita | ECS central |
| `central-api` | API de la central: inventario global, transferencias, reportes, estado de bodegas | ECS central |
| `command-relay` | Envía al broker los comandos que la central guardó mientras el broker estaba caído | ECS central |
| `transfer-orchestrator` | Coordina las transferencias paso a paso (saga) | ECS central |
| `projection-worker` | Mantiene el inventario global de la central a partir de los eventos | ECS central |
| `audit-worker` | Guarda una copia de todos los eventos (auditoría) | ECS central |
| `report-worker` | Genera reportes y los sube a S3 | ECS central |
| RDS PostgreSQL | Base de datos de la central (`central_read`) | VPC central |
| Lambda `s3_processor` | Procesa archivos que se suben a `exports/` en S3 | AWS Lambda |

### 2.3 La idea central en una frase

**Cada bodega es dueña de sus datos y trabaja sola; cuando algo cambia, lo anuncia como un *evento* a
través del broker; la central escucha esos eventos y construye su propia vista global.** Así ninguna
bodega depende de la central para atender, y la central no tiene que preguntar a cada bodega cada vez.

**Ejemplo:** el operador de WH-002 registra una entrada de 10 cajas. WH-002 guarda el cambio en *su*
base de datos y responde de inmediato "OK". En paralelo anuncia "el SKU P-001 en WH-002 ahora tiene 27".
Unos milisegundos después la central lo recibe y actualiza su inventario global. Si en ese momento la
central o el broker estuvieran caídos, WH-002 **igualmente** habría respondido "OK" y el anuncio se
enviaría cuando todo vuelva.

---

## 3. Conceptos base (antes de empezar)

### 3.1 Cliente, servidor y API

- Un **servidor** es un programa que espera peticiones y responde. Un **cliente** es quien pregunta
  (un navegador, otro programa, una prueba).
- Una **API** (Application Programming Interface) es el "menú" de operaciones que ofrece un servidor.
  Ejemplo: `POST /products` = "crea un producto"; `GET /inventory/by-sku/P-001` = "dame el inventario de P-001".

### 3.2 HTTP y REST

**HTTP** es el idioma en el que hablan navegador y servidor. Cada petición tiene:

- un **método**: `GET` (leer), `POST` (crear/ejecutar), `PUT` (reemplazar), `DELETE` (borrar);
- una **ruta**: `/products/5`;
- **cabeceras**: datos extra, p. ej. `Content-Type: application/json`, `X-Correlation-ID: COR-...`;
- un **cuerpo** (opcional): los datos, normalmente en JSON.

La respuesta trae un **código de estado**:

| Código | Significa | Ejemplo en el proyecto |
|---|---|---|
| 200 | OK | `GET /health` cuando todo va bien |
| 201 | Creado | `POST /products` |
| 202 | Aceptado (se procesará después) | `POST /transfers` (la transferencia es asíncrona) |
| 404 | No existe | SKU desconocido |
| 409 | Conflicto | Stock insuficiente para reservar |
| 422 | Datos inválidos | `quantity: 0` |
| 503 | Servicio no disponible | Base de datos caída |

**REST** es un estilo de diseñar APIs donde las rutas representan "cosas" (recursos: productos,
transferencias) y los métodos HTTP representan acciones sobre ellas.

### 3.3 JSON

Formato de texto para datos estructurados. Ejemplo real de un evento del proyecto:

```json
{
  "event_id": "EVT-3f1c...",
  "event_type": "INVENTORY_UPDATED",
  "occurred_at": "2026-11-23T00:29:35.974269Z",
  "source": "warehouse-002",
  "payload": { "warehouse_id": "WH-002", "sku": "P-001", "quantity": 27, "reserved_quantity": 0 }
}
```

### 3.4 Base de datos relacional y transacción

Una **base de datos relacional** guarda datos en **tablas** (filas y columnas), como hojas de cálculo
con reglas. Se consulta con **SQL** (`SELECT * FROM inventory WHERE sku = 'P-001'`).

Una **transacción** agrupa varias operaciones para que ocurran **todas o ninguna**. Ejemplo: registrar un
movimiento implica (1) cambiar la cantidad, (2) guardar el movimiento en el historial y (3) guardar el
evento a enviar. Si el paso 3 fallara sin transacción, la cantidad cambiaría pero nadie se enteraría.
Con transacción, o se guardan los tres, o ninguno. Esta propiedad es la base del patrón *outbox* (5.2).

### 3.5 Síncrono vs. asíncrono

- **Síncrono:** el cliente pide y **espera** la respuesta completa. Como una llamada telefónica.
- **Asíncrono:** el cliente deja el encargo y sigue; el trabajo se hace después. Como dejar un mensaje
  de voz o un correo.

**Por qué importa aquí:** si cada movimiento de inventario tuviera que esperar a que la central lo
procese, una central lenta o caída **frenaría a todas las bodegas**. Con mensajes asíncronos, la bodega
responde enseguida y la central procesa a su ritmo.

### 3.6 Consistencia eventual

Como la central se entera de los cambios "un poco después", durante unos milisegundos (o minutos si
hay una caída) su vista puede estar **desactualizada**. Eso se llama **consistencia eventual**: los datos
**terminan** coincidiendo. Es un precio aceptado a cambio de disponibilidad. El proyecto lo hace visible:
la central marca una bodega como `stale` u `OFFLINE` si hace más de 60 s que no sabe de ella.

### 3.7 Redes: IP, CIDR, puerto

- **IP:** dirección de una máquina en la red, p. ej. `10.1.10.10`.
- **Puerto:** "puerta" dentro de la máquina para un programa concreto: `8000` la API, `5432` PostgreSQL,
  `5672` RabbitMQ, `6379` Redis.
- **CIDR:** forma de escribir un **rango** de IPs. `10.1.0.0/16` = todas las que empiezan por `10.1.`
  (65 536 direcciones). `10.1.10.0/24` = las que empiezan por `10.1.10.` (256). El número tras `/`
  indica cuántos bits son fijos: más grande = rango más pequeño.

| Red | CIDR | Uso |
|---|---|---|
| VPC central | `10.0.0.0/16` | Toda la central |
| VPC WH-001 | `10.1.0.0/16` | Toda la bodega 1 |
| Subred privada WH-001 | `10.1.10.0/24` | Donde vive la EC2 (`10.1.10.10`) |

Que cada VPC tenga un rango **distinto** es obligatorio para poder conectarlas (peering): si dos redes
usaran las mismas IPs, no se sabría a cuál enviar un paquete.

### 3.8 Contenedor e imagen

Una **imagen** es un paquete con el programa y todo lo que necesita (Python, librerías, código). Un
**contenedor** es esa imagen **ejecutándose**. Se explica en detalle en 4.7.

---

## 4. Tecnologías del software, una por una

Formato de cada apartado: **qué es** → **cómo funciona** → **cómo se programa en este proyecto** →
**por qué lo elegimos** → **ejemplo**.

### 4.1 Python

**Qué es:** lenguaje de programación de propósito general, fácil de leer. Todo el backend está escrito
en Python 3.11/3.12.

**Por qué:** tiene librerías maduras para todo lo que necesitamos (APIs web, bases de datos, RabbitMQ,
AWS) y es rápido de escribir y de leer en un proyecto académico en grupo.

**Organización del código:**

```text
libs/wcommon/            código compartido por todos los servicios (logs, eventos, RabbitMQ, idempotencia, reportes)
services/warehouse-api/  la API de bodega
services/central-api/    la API central
services/workers/        los procesos que consumen mensajes (proyección, saga, auditoría, reportes, caché, IoT)
services/batch/          proceso por lotes diario
services/lambda/         función Lambda
services/iot/            simulador de sensores e ingesta MQTT
```

`wcommon` es una **librería propia** que se instala en cada imagen (`pip install libs/wcommon`), para no
repetir el mismo código en cada servicio.

### 4.2 FastAPI, Pydantic y Uvicorn (APIs web)

**Qué es:**
- **FastAPI**: framework para construir APIs HTTP en Python.
- **Pydantic**: valida y convierte los datos de entrada/salida según un "esquema".
- **Uvicorn**: el servidor que ejecuta la aplicación FastAPI y escucha en un puerto.

**Cómo funciona:** se declara una función y se "decora" con la ruta y el método. FastAPI se encarga de
leer el JSON, validarlo con Pydantic, llamar a la función y convertir el resultado a JSON.

**Cómo se programa aquí** (ejemplo real, `services/warehouse-api/app/modules/sync/sync_controller.py`):

```python
router = APIRouter(prefix="/sync", tags=["Sync"])

@router.get("/status")                                   # GET /sync/status
def sync_status(db: Session = Depends(get_db)):          # FastAPI inyecta una sesión de BD
    return {"warehouse_id": settings.WAREHOUSE_ID, **outbox_stats(db)}   # dict → JSON automáticamente
```

Validación con Pydantic (`inventory/schemas.py`):

```python
class MovementCreate(BaseModel):
    product_id: Optional[int] = None
    sku: Optional[str] = None
    type: PublicMovementType            # solo acepta RECEIPT, SHIPMENT, ADJUSTMENT, ...
    quantity: int = Field(..., ge=0)    # entero >= 0; si no, FastAPI responde 422 solo
```

Si alguien envía `{"type": "ROBO", "quantity": -5}`, FastAPI responde **422** sin que escribamos una
línea de validación manual.

**Estructura por capas** (warehouse-api):

| Capa | Archivo | Responsabilidad |
|---|---|---|
| Controller | `*_controller.py` | Recibe la petición HTTP y devuelve la respuesta |
| Service | `*_service.py` | Reglas de negocio (p. ej. no reservar más de lo disponible) |
| Repository | `*_repository.py` | Consultas a la base de datos |
| Rules | `inventory/rules.py` | Cálculo puro de un movimiento (fácil de probar) |

**Por qué:** separa "cómo llega la petición" de "qué regla se aplica" y de "cómo se guarda". Si mañana
cambia la base de datos, solo cambia el repositorio.

**Extras útiles:** FastAPI genera documentación interactiva en `/docs` (Swagger) automáticamente.
Uvicorn se arranca con `--workers 2` para usar 2 procesos (más peticiones en paralelo).

### 4.3 PostgreSQL y SQLAlchemy (datos)

**Qué es:**
- **PostgreSQL**: base de datos relacional de código abierto, robusta y con transacciones.
- **SQLAlchemy**: librería Python que permite trabajar con tablas como si fueran **clases** (ORM).

**Cómo se programa** (modelo real de la central, `services/central-api/app/models.py`):

```python
class GlobalInventory(Base):
    __tablename__ = "global_inventory"
    sku = Column(String(64), primary_key=True)
    warehouse_id = Column(String(16), primary_key=True)   # clave primaria compuesta: (sku, bodega)
    quantity = Column(Integer, nullable=False, default=0)
    reserved_quantity = Column(Integer, nullable=False, default=0)
    last_event_at = Column(DateTime(timezone=True))       # versión: momento del último cambio aplicado
```

Leer y bloquear una fila para modificarla sin que otro proceso la pise al mismo tiempo:

```python
row = session.execute(select(GlobalInventory)
        .where(GlobalInventory.sku == sku, GlobalInventory.warehouse_id == wh)
        .with_for_update()).scalar_one_or_none()      # SELECT ... FOR UPDATE
```

**Por qué PostgreSQL:** transacciones fiables (imprescindibles para outbox e idempotencia), `JSONB`
para guardar eventos, `FOR UPDATE SKIP LOCKED` para que varios procesos repartan trabajo sin chocar.

**Dónde hay bases de datos:**

| Base | Dónde | Tablas principales |
|---|---|---|
| Una por bodega (`wh_001`, ...) | Contenedor `postgres` en la EC2 de la bodega | `products`, `inventory`, `inventory_movements`, `outbox`, `processed_commands` |
| `central_read` | RDS en la VPC central | `global_inventory`, `warehouse_status`, `warehouse_sync`, `transfers`, `command_outbox`, `audit_log`, `report_jobs`, `processed_events` |

### 4.4 Redis (caché)

**Qué es:** base de datos **en memoria** (RAM), extremadamente rápida, de tipo clave → valor.

**Cómo funciona:** `SET clave valor` y `GET clave`. Se puede poner una caducidad (TTL): a los 30 s la
clave desaparece sola.

**Patrón usado: Cache Aside** (`services/warehouse-api/app/cache.py`):

1. Llega `GET /inventory/by-sku/P-001`.
2. Se busca en Redis la clave `warehouse:WH-001:inventory:sku:P-001`.
3. Si está (**hit**) → se responde desde memoria (~1 ms).
4. Si no está (**miss**) → se lee de PostgreSQL, se guarda en Redis con TTL 30 s y se responde.
5. Cuando el inventario **cambia**, se **borra** la clave (invalidación) para no servir datos viejos.

```python
def get(self, key):
    try:
        raw = self.client.get(key)
    except Exception:                 # Redis caído → NO fallamos: devolvemos None y se lee de PostgreSQL
        CACHE_ERRORS.inc()
        return None
```

**Dos formas de invalidar** (ADR-002):
- **A (síncrona):** la API borra la clave justo después de guardar el cambio.
- **B (asíncrona):** el `cache-worker` escucha el evento `INVENTORY_UPDATED` en el broker y borra la
  clave. Sirve si hay varias réplicas de la API o si la invalidación A falló.

**Por qué:** las lecturas de inventario son mucho más frecuentes que las escrituras. Redis absorbe la
mayoría y PostgreSQL queda libre para las escrituras. **Ejemplo:** 1000 lecturas/s del mismo SKU →
con caché, PostgreSQL recibe ~1 lectura cada 30 s para ese SKU en vez de 1000/s.

**Si Redis se cae** la bodega sigue funcionando (más lenta), porque cada error de Redis cae a PostgreSQL.

### 4.5 RabbitMQ (el broker de mensajes)

**Qué es:** un **intermediario de mensajes**. Los programas no se hablan directamente: uno **publica** un
mensaje en RabbitMQ y otro lo **consume** cuando puede. Analogía: una oficina de correos con buzones.

**Conceptos clave:**

| Concepto | Qué es | Analogía |
|---|---|---|
| **Exchange** | Punto donde se publican los mensajes; decide a qué colas copiarlos | La clasificadora de correos |
| **Queue (cola)** | Lista ordenada de mensajes esperando a ser consumidos | Un buzón |
| **Routing key** | Etiqueta del mensaje usada para decidir a qué colas va | La dirección del sobre |
| **Binding** | Regla "esta cola quiere los mensajes con esta etiqueta" | Suscripción del buzón |
| **Consumer** | Programa que lee de una cola | El destinatario |
| **Ack** | Confirmación del consumidor: "ya lo procesé, bórralo" | Firmar el acuse de recibo |
| **Prefetch** | Cuántos mensajes sin confirmar puede tener un consumidor | Cuántas cartas en la mano a la vez |
| **Durable** | La cola sobrevive a un reinicio de RabbitMQ | Buzón de acero, no de cartón |
| **Persistent** (`delivery_mode=2`) | El mensaje se escribe en disco | Carta certificada |
| **Publisher confirms** | RabbitMQ confirma al publicador que guardó el mensaje | Recibo de envío |

**Exchanges del proyecto** (`libs/wcommon/wcommon/rabbit.py`):

- `warehouse.events` (tipo **topic**): **hechos** que ocurrieron ("se actualizó inventario"). Varios
  interesados pueden recibir copia. Las routing keys tienen formato `tipo.subtipo.bodega`
  (`inventory.updated.wh-002`) y los bindings aceptan comodines: `*` = una palabra, `#` = cualquier cosa.
- `platform.commands` (tipo **direct**): **órdenes** para un único destinatario ("procesa esta
  transferencia"). La routing key debe coincidir exactamente.

**Colas y quién las consume:**

```python
QUEUES = {
  "q.cache.wh-001": (EVENTS_EXCHANGE, ["inventory.updated.wh-001"]),        # cache-worker de WH-001
  "q.audit":        (EVENTS_EXCHANGE, ["#"]),                                # TODOS los eventos
  "q.projection":   (EVENTS_EXCHANGE, ["inventory.updated.*", "product.created.*",
                                       "warehouse.heartbeat.*", "inventory.snapshot.*"]),
  "q.transfers":    (COMMANDS_EXCHANGE, ["transfer.process"]),               # orquestador
  "q.reports":      (COMMANDS_EXCHANGE, ["report.generate"]),                # report-worker
  ...
}
```

**Ejemplo:** WH-002 publica `inventory.updated.wh-002`. RabbitMQ copia el mensaje a `q.cache.wh-002`
(su caché), `q.audit` (`#` acepta todo) y `q.projection` (`inventory.updated.*`). Tres consumidores
distintos reciben el mismo hecho, cada uno a su ritmo, sin que la bodega sepa quiénes son.

**Cómo se consume** (simplificado de `rabbit.consume`):

```python
def on_message(ch, method, properties, body):
    msg = json.loads(body)
    try:
        handler(msg)                                   # lógica del worker
    except TransientError:                             # fallo temporal → reintentar más tarde
        ch.basic_publish("", f"{queue}.retry", body, ...)
    except PermanentError:                             # mensaje imposible de procesar → DLQ
        ch.basic_publish("", f"{queue}.dlq", body, ...)
    ch.basic_ack(method.delivery_tag)                  # confirmar: RabbitMQ lo borra de la cola
```

**Por qué RabbitMQ (y no llamadas HTTP directas):**
1. **Desacopla:** la bodega no necesita saber quién escucha ni que esté disponible.
2. **Amortigua picos:** si llegan 2000 eventos/s y la central procesa 800/s, el resto espera en la cola
   en vez de perderse o tumbar la central.
3. **Reparte trabajo:** dos `projection-worker` leen de la misma cola y RabbitMQ alterna los mensajes
   (consumidores competidores).
4. **Reintentos y DLQ** integrados con colas auxiliares.

### 4.6 Reintentos, TTL y DLQ (dentro de RabbitMQ)

Cada cola `Q` tiene dos colas auxiliares:

- `Q.retry`: con TTL de 5 s (`x-message-ttl=5000`). Al caducar, el mensaje **vuelve** a `Q`
  (`x-dead-letter-routing-key=Q`). Es una "sala de espera" de 5 s.
- `Q.dlq` (*dead letter queue*): mensajes que fallaron demasiadas veces (3) o que son imposibles de
  procesar. Se inspeccionan a mano.

**Ejemplo:** el `report-worker` intenta subir un reporte a S3 y S3 responde 503. Es un error
**transitorio**: el mensaje va a `q.reports.retry`, espera 5 s y se reintenta. Si un mensaje llega con
JSON inválido, es **permanente**: va directo a `q.reports.dlq`, porque reintentarlo no lo arreglará.

### 4.7 Docker (contenedores)

**Qué es:** herramienta para empaquetar un programa con **todo** lo que necesita en una **imagen**, y
ejecutarla como **contenedor** aislado en cualquier máquina con Docker.

**Problema que resuelve:** "en mi PC funciona". Con Docker, la misma imagen corre igual en el portátil,
en la EC2 de la bodega y en ECS.

**Cómo se programa: el `Dockerfile`** (receta para construir la imagen) de `services/warehouse-api/Dockerfile`:

```dockerfile
FROM python:3.11-slim                                   # base: un Linux mínimo con Python
WORKDIR /app
COPY libs/wcommon /tmp/wcommon
COPY services/warehouse-api/requirements.txt requirements.txt
RUN pip install /tmp/wcommon -r requirements.txt        # instalar dependencias
COPY services/warehouse-api/app ./app                   # copiar el código
RUN useradd --system --uid 10001 appuser
USER appuser                                            # no ejecutar como root (seguridad)
EXPOSE 8000
HEALTHCHECK CMD python -c "...urlopen('http://127.0.0.1:8000/health')..."
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]   # qué ejecutar al arrancar
```

**Imágenes del proyecto:** `warehouse-api` (también ejecuta el outbox relay), `central-api` (también
el command-relay), `workers` (todos los workers, cambia el comando), `batch`, `iot`, `frontend`.
**Por qué una imagen para varios procesos:** comparten código; solo cambia el comando de arranque
(`python -m workers.projection` vs `python -m workers.audit`).

### 4.8 Docker Compose

**Qué es:** herramienta para definir y arrancar **varios contenedores juntos** con un archivo YAML.

**Dónde se usa:**
1. **Local** (`infrastructure/compose/docker-compose.yml`): toda la plataforma en un PC, con *profiles*
   para levantar solo partes (`events`, `central`, `lb`, `obs`, `iot`...).
2. **En cada EC2 de bodega** (`infrastructure/aws/warehouse-compose.yml.tftpl`): los 5 servicios de la bodega.

Fragmento de la bodega en AWS:

```yaml
services:
  postgres:
    image: public.ecr.aws/docker/library/postgres:15-alpine
    volumes: [ "/opt/warehouse/pgdata:/var/lib/postgresql/data" ]   # datos en el disco de la EC2
    healthcheck: { test: ["CMD-SHELL", "pg_isready -U warehouse"] }
  api:
    image: "<cuenta>.dkr.ecr.us-east-1.amazonaws.com/warehouse-api:latest"
    command: ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
    ports: [ "8000:8000" ]                                          # puerto de la EC2 → contenedor
    depends_on: { postgres: { condition: service_healthy } }        # no arrancar hasta que la BD esté lista
  outbox:
    command: ["python", "-m", "app.outbox_relay"]
    deploy: { replicas: 2 }                                         # 2 copias en paralelo
```

`restart: unless-stopped` hace que, si la EC2 se reinicia, Docker vuelva a levantar todo solo.

### 4.9 Nginx (balanceador local)

Servidor web usado **solo en local** como balanceador: reparte las peticiones de WH-002 entre 3 réplicas
de la API (`infrastructure/compose/nginx/wh-002-lb.conf`). Demuestra que la API es *stateless* (no guarda
estado en memoria) y por eso puede escalar en horizontal. En AWS ese papel lo hace el **ALB** (6.12).

### 4.10 Amazon S3 y AWS Lambda (archivos y funciones)

- **S3:** almacenamiento de **archivos** (objetos) ilimitado. Aquí guarda los reportes
  (`reports/...json`) y los archivos de exportación (`exports/`).
- **Lambda:** ejecuta una **función** cuando ocurre algo, sin servidor que administrar. Aquí: cuando se
  sube un archivo a `exports/`, la Lambda `s3_processor` lo valida y lo mueve a `processed/` o `rejected/`.

**Ejemplo:** subes `exports/inventario.csv` con tres filas: `P-001,WH-001,10`, `P-002,BODEGA1,5` y
`P-003,WH-002,-3`. La Lambda se dispara sola, valida cada fila y escribe:
- `processed/inventario.json` con la única fila válida (`P-001`);
- `processed/inventario.meta.json` con el resumen: 3 filas, 1 válida, 2 inválidas, y los motivos
  ("warehouse_id con formato invalido (WH-NNN)", "quantity debe ser entero >= 0").

Si **ninguna** fila es válida, el archivo entero va a `rejected/`. Solo se paga por los milisegundos que corre.

**Por qué Lambda y no un worker:** el procesamiento de archivos es esporádico; tener un proceso encendido
24/7 esperando archivos sería desperdicio (ADR-011).

### 4.11 MQTT y Mosquitto (IoT)

**MQTT** es un protocolo de mensajes ligero para dispositivos (sensores). **Mosquitto** es el servidor
MQTT. El simulador publica lecturas en `warehouses/WH-001/sensors/temp-01`; el servicio de ingesta las
valida y las reenvía a RabbitMQ (`iot.reading.wh-001`). Los workers IoT las guardan y generan alertas
si un valor supera un umbral. Cada lectura trae `sequence_number` para descartar duplicadas y fuera de
orden. *(Se ejecuta en local; no está desplegado en AWS.)*

### 4.12 Observabilidad: logs JSON, correlation id, Prometheus, Grafana, CloudWatch

**Problema:** con muchos servicios, ¿cómo sigues una operación que pasa por 5 procesos?

- **Logs en JSON** (`wcommon/logging.py`): cada línea es un objeto con `timestamp`, `service`, `level`,
  `event` y datos. Se pueden filtrar por campo.
- **Correlation id** (`wcommon/correlation.py`): un identificador `COR-xxxx` que nace en la primera
  petición HTTP (o se recibe en la cabecera `X-Correlation-ID`) y **viaja** dentro de cada evento y
  llamada. Buscando ese id en los logs ves todo el recorrido de una transferencia en todos los servicios.

```python
class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        cid = set_correlation_id(request.headers.get("X-Correlation-ID"))   # usar el recibido o crear uno
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = cid                         # devolverlo al cliente
        return response
```

- **Prometheus:** recolecta **métricas** numéricas (peticiones/s, aciertos de caché, mensajes en DLQ)
  que cada servicio expone en `/metrics`. **Grafana** las dibuja en paneles. *(Local.)*
- **CloudWatch Logs** (AWS): cada contenedor envía sus logs a un grupo, p. ej.
  `/warehouse-platform/wh-002-outbox`. Se consultan con `aws logs tail`.

### 4.13 Pruebas: pytest, k6

- **pytest:** framework de pruebas de Python. 131 pruebas unitarias (sin servicios externos: usan SQLite
  en memoria, Redis falso, publicadores falsos) y pruebas de integración contra PostgreSQL y RabbitMQ reales.
- **k6:** herramienta de pruebas de **carga** (simula muchos usuarios). Scripts en `tests/load/`.

---

## 5. Patrones de diseño y por qué los usamos

Un **patrón de diseño** es una solución conocida a un problema que se repite. Cada uno se explica con:
**problema → idea → ejemplo → código → qué pasaría sin él**.

### 5.1 Una base de datos por bodega (ADR-001)

- **Problema:** si todas las bodegas compartieran una base de datos, una consulta pesada de WH-001
  ralentizaría a WH-002, y si esa base cae, **todas** caen.
- **Idea:** cada bodega tiene su propio PostgreSQL, en su propia EC2 y su propia VPC.
- **Ejemplo:** WH-003 hace un inventario masivo que satura su disco. WH-001 y WH-002 ni se enteran.
- **Coste:** la central ya no puede hacer un `JOIN` entre bodegas. Por eso existe CQRS (5.6).

### 5.2 Transactional Outbox: la "cola local" de cada bodega (ADR-004)

- **Problema (el "doble escrito"):** al registrar un movimiento hay que (1) guardar en la base de datos y
  (2) avisar por el broker. Si guardas y el broker está caído, la central **nunca se entera**. Si avisas
  primero y luego falla el guardado, la central cree algo **falso**.
- **Idea:** no publicar directamente. Guardar el evento en una tabla `outbox` **dentro de la misma
  transacción** que el cambio: o se guardan ambos o ninguno. Un proceso aparte (el **outbox relay**) lee
  esa tabla y publica en el broker; cuando el broker confirma, marca la fila como publicada.

```python
# inventory_service.py — dentro de la transacción del movimiento
changed_at = schemas_utils.current_utc_time()
inventory.updated_at = changed_at
self.repo.update_stock(inventory, new_qty, new_reserved)          # (1) cambio de inventario
self.repo.register_movement(...)                                  # (2) historial
enqueue_event(self.db, "INVENTORY_UPDATED", "inventory.updated.wh-002",
              {...}, occurred_at=changed_at)                      # (3) evento a la tabla outbox
# el commit guarda (1), (2) y (3) juntos
```

```python
# outbox_relay.py — proceso aparte
rows = (select(OutboxEntity).where(OutboxEntity.published_at.is_(None))
        .order_by(OutboxEntity.id).limit(100).with_for_update(skip_locked=True))
for row in rows:
    publish_fn(row.routing_key, row.payload)   # si el broker falla, lanza excepción y la fila sigue pendiente
    row.published_at = now()                   # solo se marca cuando el broker confirmó
```

- **`SKIP LOCKED`:** permite **2 réplicas del relay** a la vez: cada una salta las filas que la otra ya
  bloqueó. Más velocidad hacia el broker sin enviar dos veces la misma fila.
- **Ejemplo:** el broker se cae 5 minutos. WH-002 sigue aceptando movimientos (`201`) y la tabla `outbox`
  acumula, por ejemplo, 300 eventos. `GET /sync/status` muestra `outbox_pending: 300`. Cuando el broker
  vuelve, el relay los envía **en orden** y el contador baja a 0. **No se pierde nada.**
- **Sin él:** cada caída del broker supondría pérdida de datos o errores al usuario.

### 5.3 Idempotencia (ADR-006)

- **Problema:** con reintentos, un mensaje puede llegar **dos veces** (por ejemplo, el consumidor lo
  procesó pero se cayó antes de confirmarlo y RabbitMQ lo reentrega). Si "sumar 10 unidades" se aplica
  dos veces, el inventario queda mal.
- **Idea:** cada evento tiene un `event_id` único. El consumidor anota `(event_id, consumidor)` en
  `processed_events` **en la misma transacción** en que aplica el efecto. Si el evento vuelve a llegar,
  lo ignora.

```python
with session.begin():
    if already_processed(session, msg["event_id"], "projection"):
        return "duplicate"                                   # ya aplicado: no hacer nada
    aplicar_cambio(...)
    mark_processed(session, msg["event_id"], "projection")   # se guarda junto con el cambio
```

- En las bodegas, los pasos de una transferencia (`reserve`, `commit-out`, `receive`, `release`) usan la
  tabla `processed_commands` con el id `transfer_id:paso`. Si el orquestador repite "reserva 30 de TR-1",
  la bodega responde lo mismo que la primera vez y **no reserva otras 30**.
- **Garantía resultante:** entrega "al menos una vez" + idempotencia = efecto "exactamente una vez".

### 5.4 Reintentos: errores transitorios y permanentes (ADR-005)

- **Transitorio** (`TransientError`): puede arreglarse solo (la BD está reiniciando, S3 va lento).
  Se reintenta con una espera de 5 s, hasta 3 veces.
- **Permanente** (`PermanentError`): no se arreglará nunca (JSON inválido, transferencia inexistente).
  Va directo a la DLQ, sin malgastar reintentos.
- **Ejemplo:** el orquestador llama a WH-003 para recibir mercancía y WH-003 está reiniciando. Es un
  error transitorio, así que reintenta 3 veces; si sigue caída, la saga **compensa** (5.5).

### 5.5 Saga orquestada: transferencias entre bodegas (ADR-007)

- **Problema:** una transferencia toca **dos bases de datos distintas** (origen y destino), y no existe
  una transacción que abarque ambas. ¿Cómo evitar que se descuente en el origen y nunca se sume en el destino?
- **Idea:** dividir el proceso en **pasos** pequeños, cada uno con su transacción local, y tener
  **compensaciones** (pasos que deshacen) por si algo falla a mitad. Un **orquestador** central
  (`transfer-orchestrator`) guarda el estado en la tabla `transfers` y avanza paso a paso.

```text
REQUESTED → RESERVING_SOURCE → SOURCE_RESERVED → IN_TRANSIT → DESTINATION_PENDING
          → DESTINATION_RECEIVED → COMPLETED
                         │ si falla después de reservar
                         └──────────→ COMPENSATING (libera la reserva en origen) → CANCELLED
  si el origen no tiene stock → FAILED (no hay nada que deshacer)
```

| Paso | Llamada HTTP a la bodega | Qué hace |
|---|---|---|
| Reservar en origen | `POST /transfers/{id}/reserve` (origen) | Aparta las unidades (ya no se pueden vender) |
| Recibir en destino | `POST /transfers/{id}/receive` (destino) | Suma las unidades en el destino |
| Confirmar salida | `POST /transfers/{id}/commit-out` (origen) | Descuenta definitivamente lo reservado |
| Compensar | `POST /transfers/{id}/release` (origen) | Devuelve la reserva si algo falló |

- **Ejemplo 1 (éxito):** mover 30 unidades de WH-001 a WH-002 termina en `COMPLETED`: WH-001 −30, WH-002 +30.
- **Ejemplo 2 (destino caído):** se reservan 30 en WH-001, pero WH-002 no responde tras 3 reintentos.
  La saga pasa a `COMPENSATING`, libera las 30 en WH-001 y termina en `CANCELLED`. Nadie pierde mercancía.
- **Por qué orquestada y no coreografiada:** con un orquestador el estado está en **un solo sitio**
  (`GET /transfers/{id}` muestra todo el historial). En una coreografía cada bodega reaccionaría a los
  eventos de las otras... y **las bodegas no pueden hablar entre sí** (R6). El orquestador está en la
  central, que es la única que ve a todas.
- La saga es **reanudable**: si el orquestador se reinicia, lee el estado guardado y sigue desde ahí.

### 5.6 CQRS: el inventario global de la central (ADR-008)

- **Problema:** para saber "¿cuánto P-001 hay en total?" sin datos centrales, habría que preguntar a las
  3 bodegas cada vez (**fan-out**): es lento, y si una está caída la respuesta sale incompleta.
- **Idea (CQRS = separar escritura y lectura):** las bodegas **escriben**; la central mantiene una
  **copia de lectura** (`global_inventory`, el *read model*) que el `projection-worker` actualiza con
  cada evento `INVENTORY_UPDATED`.
- **Ejemplo:** `GET /global/inventory/P-001` responde desde la central en milisegundos:
  `{"total": 57, "warehouses": [{"WH-001": 30}, {"WH-002": 27}], "stale_warehouses": []}`. Si WH-003
  lleva más de 60 s sin dar señales, aparece en `stale_warehouses` y el usuario sabe que ese dato puede
  estar desactualizado.
- Se conserva el modo **fan-out** (`?mode=fanout`, consulta en vivo a cada bodega) para comparar ambos enfoques.

### 5.7 Orden de eventos: "gana el más reciente" (last-write-wins)

- **Problema:** con 2 `projection-worker` en paralelo y reintentos, los eventos pueden llegar
  **desordenados**: primero "cantidad 27" (de las 10:00:05) y después "cantidad 20" (de las 10:00:01).
- **Idea:** cada fila del read model guarda `last_event_at`. Un evento **más viejo** que lo guardado se descarta.

```python
if last is not None and occurred_at < last:
    return "out_of_order_discarded"          # llegó tarde: no pisar un dato más nuevo
```

### 5.8 Heartbeat: saber si una bodega está viva

- Cada 15 s el outbox relay publica `WAREHOUSE_HEARTBEAT` con `{warehouse_id, outbox_pending, oldest_pending_at}`.
- La central guarda la hora en `warehouse_status` y el backlog en `warehouse_sync`.
- **Para qué:** distinguir "la bodega no tuvo movimientos" de "la bodega no da señales", y saber cuántos
  eventos tiene pendientes de enviar (`outbox_pending`).

### 5.9 Consumidores competidores

Varias copias del mismo worker leen **la misma cola**, y RabbitMQ reparte los mensajes entre ellas. Así
se duplica la capacidad sin cambiar código. Aquí se usa con `projection-worker ×2` en la central y
`outbox relay ×2` en cada bodega. Es seguro gracias a la idempotencia (5.3), al orden por timestamp
(5.7) y a `SKIP LOCKED` (5.2).

### 5.10 Resincronización con snapshot (R10, ADR-016)

- **Problema:** el outbox garantiza que se envía lo pendiente. Pero ¿y si la central perdió un evento por
  otro motivo (acabó en la DLQ, alguien borró datos)? La central quedaría desincronizada **para siempre**
  con ese SKU.
- **Idea:** al arrancar y cada vez que **reconecta** con el broker, el relay encola un
  `INVENTORY_SNAPSHOT` con **todo** el inventario de la bodega (en trozos de 500 SKU), **detrás** de los
  pendientes. La central aplica cada ítem con last-write-wins: corrige lo que esté mal y no pisa lo más nuevo.

```python
# outbox_relay.py — al (re)conectar
if resync_reason and RESYNC_ON_RECONNECT:
    resync_after_reconnect(session, resync_reason)   # encola el snapshot (sin duplicarlo si ya hay uno pendiente)
```

- **Detalle importante (bug real encontrado en las pruebas):** la versión de cada ítem del snapshot es el
  `updated_at` de la fila. Al principio la fila y su evento se marcaban con instantes **distintos** (por
  microsegundos), y la central descartaba **siempre** el snapshot como "viejo". Se corrigió haciendo que
  la fila y el evento compartan el **mismo instante** (`changed_at`), y hay un test que lo vigila.
- **Ejemplo (verificado en AWS y en local):** se fuerza en la central `WH-002 / SKU-B = 999` (dato
  corrupto; el real es 7). Se cae el broker y vuelve; la bodega envía el snapshot y la central queda en 7.
- También se puede pedir a mano: `POST /warehouses/WH-002/resync` (desde la central) o
  `POST /sync/snapshot` (en la bodega).

### 5.11 Cola de respaldo de la central: `command_outbox` (R9, ADR-016)

- **Problema:** la central también publica comandos (`PROCESS_TRANSFER` y `GENERATE_REPORT`). Antes, si
  el broker estaba caído, respondía **503** y el usuario tenía que reintentar: el servicio se "paraba".
- **Idea:** si publicar falla, guardar el comando en la tabla `command_outbox` y responder **202** con
  `"delivery": "queued"`. El servicio `command-relay` lo publica cuando el broker vuelve.

```python
def publish_or_queue(db, publisher, routing_key, message):
    try:
        publisher.publish_command(routing_key, message)
        return "published"
    except PublishError:
        db.add(CommandOutbox(event_id=message["event_id"], routing_key=routing_key, payload=message))
        db.commit()
        return "queued"                 # la API responde 202 igualmente
```

- **Ejemplo (verificado en AWS):** con el broker detenido, `POST /transfers` devuelve
  `202 {"delivery":"queued"}` y `GET /health` muestra `pending_commands: 1`. Al levantar el broker, el
  relay lo publica y la transferencia termina en `COMPLETED`.
- Solo responde 503 si **también** falla la base de datos (entonces ya no hay dónde guardar el comando).

### 5.12 Reporte general de bodegas (R11)

- **Problema:** el operador quiere ver de un vistazo el estado de **las bodegas** (no del código): cuáles
  están en línea, si están sincronizadas, cuánto inventario tienen, qué productos se están agotando y
  cómo van las transferencias.
- **Implementación:** una función pura, `warehouses_summary` (`libs/wcommon/wcommon/reports.py`), que usan:
  - `GET /reports/warehouses`, que responde al momento;
  - `POST /reports {"report_type": "warehouses_summary"}`: el `report-worker` lo genera y lo guarda en S3
    (`reports/warehouses/AAAA/MM/JOB-xxxx.json`), útil como histórico.

Contenido por bodega:

| Bloque | Campos | Para qué |
|---|---|---|
| Estado | `status` (`ONLINE` / `OFFLINE` / `NEVER_SEEN`), `last_event_at`, `seconds_since_last_event` | ¿Está viva? |
| Sincronización | `in_sync`, `outbox_pending`, `oldest_pending_at`, `last_snapshot_at` | ¿Tiene datos sin enviar? |
| Inventario | `sku_count`, `total_units`, `reserved_units`, `available_units`, `low_stock_skus` | ¿Qué tiene y qué se agota? (umbral `LOW_STOCK_THRESHOLD`, por defecto 5) |
| Transferencias | `outgoing` / `incoming` por estado, unidades completadas | ¿Cómo va el movimiento entre bodegas? |

Además incluye totales globales: bodegas en línea, unidades, productos distintos, transferencias por
estado y comandos pendientes en la central.

### 5.13 Correlation id de punta a punta

Ya se explicó en 4.12. **Ejemplo:** un usuario dice "mi transferencia tardó". Con el `X-Correlation-ID`
de la respuesta se busca en CloudWatch, y aparecen en orden los logs de central-api, del orquestador, de
WH-001 y de WH-002 para esa operación concreta.

---

## 6. AWS: cada servicio de la nube explicado

**AWS (Amazon Web Services)** alquila infraestructura por horas: máquinas, redes, bases de datos,
almacenamiento. En vez de comprar servidores, se piden por API y se pagan mientras existen. Usamos
**AWS Academy Learner Lab**, una cuenta educativa con restricciones (ver 6.18).

### 6.1 Región y zona de disponibilidad (AZ)

- **Región:** un conjunto de centros de datos en una zona geográfica. Usamos `us-east-1` (Virginia).
- **Zona de disponibilidad (AZ):** uno o varios centros de datos **independientes** dentro de la región
  (`us-east-1a`, `us-east-1b`...). Si se inunda uno, el otro sigue.
- **Decisión:** la central usa **2 AZ** porque el ALB, el RDS y el EFS lo exigen (y da tolerancia a
  fallos). Cada bodega usa **1 AZ** (más simple y barato; la bodega es una sola EC2 de todas formas).

### 6.2 VPC (Virtual Private Cloud)

- **Qué es:** una **red privada virtual** tuya dentro de AWS, aislada de las de otros clientes y de tus
  otras VPCs. Tiene un rango de IPs (CIDR).
- **Analogía:** cada VPC es un **edificio con su propia red interna**. Por defecto un edificio no tiene
  pasillo hacia otro.
- **En el proyecto:** 4 VPCs (R2/R3): central `10.0.0.0/16`, WH-001 `10.1.0.0/16`, WH-002 `10.2.0.0/16`,
  WH-003 `10.3.0.0/16`. Todas las crea el **mismo módulo** `modules/vpc`, llamado 4 veces.
- **Por qué una VPC por bodega:** es el aislamiento más fuerte que da AWS a nivel de red. Sin una
  conexión explícita, dos VPCs **no pueden** hablarse.

### 6.3 Subred pública y subred privada

- **Subred (subnet):** un trozo del rango de la VPC, en una AZ concreta.
- **Pública:** su tabla de rutas envía `0.0.0.0/0` (todo Internet) al **Internet Gateway**. Lo que está
  aquí puede ser alcanzable desde Internet si tiene IP pública.
- **Privada:** **no** tiene ruta de entrada desde Internet. Sus máquinas pueden **salir** a Internet a
  través del NAT, pero nadie de fuera puede iniciar una conexión hacia ellas.

| VPC | Pública (qué hay) | Privada (qué hay) |
|---|---|---|
| Central | ALB, NAT | Tareas ECS, NLB del broker, EFS, RDS |
| Cada bodega | NAT | La EC2 de la bodega |

- **Por qué la EC2 en la privada:** la bodega **no debe ser accesible desde Internet**. Los clientes
  entran por el ALB de la central; los administradores, por SSM (6.10). Reduce la superficie de ataque.

### 6.4 Internet Gateway, NAT Gateway y tablas de rutas

- **Tabla de rutas:** lista de reglas "para ir a la red X, sal por Y". Cada subred usa una.
- **Internet Gateway (IGW):** puerta de la VPC hacia Internet (en ambos sentidos) para subredes públicas.
- **NAT Gateway:** permite que máquinas de la subred **privada** **salgan** a Internet (para descargar
  Docker, imágenes de ECR, enviar logs) **sin** ser alcanzables desde fuera. Vive en la subred pública.
  Analogía: la recepción de un hotel que hace llamadas al exterior por ti, pero no da tu número.

```text
Tabla de rutas privada de WH-001:
  10.1.0.0/16  → local          (dentro de la propia VPC)
  0.0.0.0/0    → NAT Gateway    (salida a Internet)
  10.0.0.0/16  → peering        (hacia la central)       ← NO hay ruta hacia 10.2/16 ni 10.3/16
```

- **Coste:** el NAT es el recurso más caro (~0,045 USD/h cada uno, 4 en total). Alternativa en
  producción: *VPC endpoints* para los servicios de AWS.

### 6.5 VPC Peering: el "pasillo" entre la central y cada bodega

- **Qué es:** una conexión privada entre **dos** VPCs. Las máquinas se hablan con IPs privadas, como si
  estuvieran en la misma red, sin pasar por Internet.
- **Propiedad clave: NO es transitivo.** Si A↔C y B↔C, **A no puede llegar a B a través de C**. Esto es
  exactamente R6: con 3 peerings (central↔WH-001, central↔WH-002, central↔WH-003), la central ve a todas y
  ninguna bodega ve a otra. Topología **hub-and-spoke** (centro y radios).
- **Cómo se programa** (`modules/peering/main.tf`):

```hcl
resource "aws_vpc_peering_connection" "this" {
  vpc_id      = var.hub_vpc_id      # la central
  peer_vpc_id = var.spoke_vpc_id    # la bodega
  auto_accept = true                # misma cuenta: se acepta sola
}
resource "aws_route" "spoke_to_hub" {                  # la bodega solo aprende la ruta hacia la central
  for_each                  = var.spoke_route_table_ids
  route_table_id            = each.value
  destination_cidr_block    = var.hub_cidr
  vpc_peering_connection_id = aws_vpc_peering_connection.this.id
}
```

- **Alternativa descartada:** Transit Gateway (un "router central" gestionado). Más flexible para muchas
  VPCs, pero más caro y complejo para 4 (ADR-015).

### 6.6 Security Group y Network ACL: dos firewalls distintos

| | **Security Group (SG)** | **Network ACL (NACL)** |
|---|---|---|
| Se aplica a | Una máquina (su interfaz de red) | Una subred entera |
| Reglas | Solo "permitir" | "Permitir" y "denegar", con número de orden |
| Estado | **Con estado**: si permites la entrada, la respuesta sale sola | **Sin estado**: hay que permitir ida y vuelta |
| Uso aquí | "Solo la central puede llamar a la API de la bodega" | "Prohibido hablar con las otras bodegas" |

### 6.7 El Security Group de cada bodega (R4)

```text
SG warehouse-platform-wh-002-sg
  ENTRADA:  TCP 8000 (API) desde 10.0.0.0/16 (central)      ← nadie más
            ICMP (ping)    desde 10.0.0.0/16 (central)
  SALIDA:   TCP 443/80 a Internet (vía NAT: ECR, CloudWatch, SSM, paquetes)
            TCP 5672 hacia 10.0.0.0/16 (el broker)
            ICMP hacia la central
```

**Ejemplo:** aunque alguien añadiera por error una ruta de WH-001 a WH-002, el SG de WH-002 seguiría
rechazando el tráfico, porque `10.1.x.x` no está en su lista de entrada. Y la NACL lo bloquearía antes.

**Detalle aprendido en el despliegue real:** AWS solo acepta ciertos caracteres en la descripción de
una regla; `ALB -> ...` falló por el `>`. Se cambió por `ALB hacia ...`.

### 6.8 EC2 (las máquinas de las bodegas) (R1)

- **Qué es:** una **máquina virtual** (servidor) que alquilas. Eliges tamaño (`t3.small` = 2 vCPU, 2 GB),
  sistema operativo (imagen **AMI**) y red.
- **En el proyecto:** 3 EC2 con **Amazon Linux 2023**, cada una en la subred privada de su VPC, con IP
  privada **fija** (`10.X.10.10`) y disco cifrado de 20 GB.
- **Por qué IP fija:** la central llama a cada bodega por su IP (`WAREHOUSES=WH-001=http://10.1.10.10:8000,...`).
  Si la EC2 se para y arranca (el lab las para al terminar la sesión), la IP privada **no cambia**.
- **Por qué EC2 y no ECS para las bodegas:** lo pide el enunciado y además encaja: cada bodega es una
  "sede" con su base de datos local en disco; una máquina por bodega lo representa directamente.

### 6.9 user_data: cómo se instala Docker solo (R5)

**user_data** es un script que la EC2 ejecuta **una vez al nacer**. Terraform lo genera desde la
plantilla `infrastructure/aws/user_data.sh.tftpl`:

```bash
dnf install -y docker nmap-ncat jq             # instala Docker (y nc para pruebas de red)
systemctl enable --now docker                  # arranca Docker y lo deja activo tras reinicios
curl -fsSL .../docker-compose-linux-x86_64 -o /usr/local/lib/docker/cli-plugins/docker-compose
cat > /opt/warehouse/docker-compose.yml <<'COMPOSE_EOF'
${compose}                                     # Terraform inserta aquí el compose de ESTA bodega
COMPOSE_EOF
aws ecr get-login-password | docker login ...  # credenciales para descargar imágenes privadas
docker compose pull && docker compose up -d    # descarga y arranca los 5 contenedores
```

Todo tiene reintentos (el NAT puede tardar en estar listo). El log queda en `/var/log/warehouse-bootstrap.log`.
Para redesplegar tras publicar imágenes nuevas: `sudo /opt/warehouse/redeploy.sh`.

### 6.10 SSM Session Manager (acceso sin SSH)

- **Qué es:** servicio de AWS para ejecutar comandos o abrir una consola en una EC2 **sin abrir el puerto
  22 (SSH) ni darle IP pública**. El agente SSM de la EC2 se conecta hacia afuera (vía NAT, puerto 443).
- **Para qué aquí:** las bodegas no tienen IP pública ni SSH. El script de verificación usa
  `aws ssm send-command` para ejecutar dentro de cada bodega pruebas como `nc -z 10.2.10.10 8000`
  (¿puedo llegar a WH-002?) y demostrar el aislamiento **desde dentro**.

### 6.11 ECS y Fargate (la bodega central) (R3)

- **ECS (Elastic Container Service):** servicio que **ejecuta y mantiene vivos contenedores** por ti.
- **Fargate:** modo de ECS en el que **no administras máquinas**: dices "quiero este contenedor con
  0,5 vCPU y 1 GB" y AWS lo ejecuta.
- **Conceptos:**

| Concepto | Qué es | En el proyecto |
|---|---|---|
| **Cluster** | Agrupación lógica de servicios | `warehouse-platform-central` |
| **Task definition** | La "receta": imagen, comando, CPU, memoria, variables, logs | Una por servicio (`central-api`, `projection-worker`, ...) |
| **Task** | Un contenedor (o grupo) ejecutándose | p. ej. 2 tasks de `central-api` |
| **Service** | Mantiene N tasks vivas; si una muere, crea otra; las registra en el balanceador | 7 services |

Fragmento de `modules/central/ecs.tf`:

```hcl
"projection-worker" = {
  image   = var.images.workers
  command = ["python", "-m", "workers.projection"]
  cpu     = 256          # 0,25 vCPU
  memory  = 512          # MB
  count   = var.projection_workers     # 2 por defecto
}
```

- **Por qué ECS para la central:** son **muchos servicios pequeños** que deben escalar por separado
  (más `projection-worker` si hay muchos eventos, más `central-api` si hay muchas consultas), reiniciarse
  solos si fallan y desplegarse sin entrar a ninguna máquina.
- **Roles IAM:** cada tarea necesita permisos (descargar de ECR, escribir logs, subir a S3). En Learner Lab
  no se pueden crear roles, así que se usa el rol existente **`LabRole`**.

### 6.12 ALB (Application Load Balancer): la puerta de entrada

- **Qué es:** balanceador de carga HTTP. Recibe peticiones y las reparte entre varios destinos
  (*targets*) sanos, comprobando su salud (`GET /health` cada 15 s).
- **Listener:** un puerto en el que escucha el ALB. **Target group:** el grupo de destinos de un listener.

| Listener | Target group | Destinos |
|---|---|---|
| `:80` | `warehouse-platform-api` | Las 2 tareas de `central-api` |
| `:8001` | `warehouse-platform-wh-001` | `10.1.10.10:8000` (en otra VPC, por peering) |
| `:8002` | `warehouse-platform-wh-002` | `10.2.10.10:8000` |
| `:8003` | `warehouse-platform-wh-003` | `10.3.10.10:8000` |
| `:15672` | `warehouse-platform-mq-ui` | Consola de RabbitMQ (solo desde tu IP) |

- **Detalle técnico:** las IPs de las bodegas están en **otras** VPCs, así que el target group es de tipo
  `ip` con `availability_zone = "all"` (así AWS acepta IPs alcanzables por peering).
- **Por qué los clientes llegan a las bodegas "a través de la central":** cumple "todo pasa por la
  central" y permite que las bodegas sigan sin IP pública.
- **Solo HTTP, no HTTPS:** HTTPS requiere un certificado para un dominio propio (ACM), algo que el Learner
  Lab no da. Por eso hay que escribir `http://` en el navegador (Brave intenta pasar a HTTPS y se queda esperando).

### 6.13 NLB (Network Load Balancer) del broker

- **Qué es:** balanceador de **TCP** (no entiende HTTP); muy rápido. Aquí es **interno** (sin acceso
  desde Internet).
- **Para qué:** el broker es una tarea ECS cuya IP **cambia** cada vez que se reinicia. El NLB da un
  **nombre DNS fijo** (`warehouse-platform-mq-....elb.us-east-1.amazonaws.com:5672`) que las bodegas y
  los workers usan siempre, sin importar dónde esté corriendo RabbitMQ.

### 6.14 EFS (disco compartido del broker) (R8)

- **Qué es:** sistema de archivos de red (NFS) gestionado; sobrevive aunque desaparezca el contenedor que lo usa.
- **Problema que resuelve:** en Fargate, el disco de un contenedor **se borra** cuando la tarea termina.
  Si RabbitMQ guardara sus colas ahí, un reinicio del broker **perdería** los mensajes pendientes.
- **Solución:** montar EFS en `/var/lib/rabbitmq`. Además se fija el nombre del nodo
  (`RABBITMQ_NODENAME=rabbit@localhost`), porque RabbitMQ guarda los datos en una carpeta con el nombre
  del nodo; si cambiara en cada reinicio, no encontraría los datos anteriores.
- **Despliegue `mínimo 0 % / máximo 100 %`:** nunca hay dos brokers a la vez escribiendo en el mismo directorio.
- **Verificado en AWS:** con el `projection-worker` detenido se encolaron 3 eventos, se **reinició** el
  broker y, al reactivar el worker, los 3 se procesaron (WH-003 = 6).

### 6.15 RDS (base de datos gestionada de la central)

- **Qué es:** PostgreSQL administrado por AWS (instalación, parches, disco). `db.t3.micro`, 20 GB,
  cifrado, en subredes privadas, solo accesible desde las tareas ECS.
- **Por qué no un contenedor más:** los datos de la central deben sobrevivir a reinicios y despliegues;
  RDS se encarga de eso sin trabajo extra.

### 6.16 ECR (registro de imágenes Docker)

Almacén privado de imágenes Docker en tu cuenta. `scripts/build_push_ecr.sh` construye y sube
`warehouse-api`, `central-api` y `workers`. Las EC2 y ECS descargan de ahí. Las imágenes públicas
(PostgreSQL, Redis, RabbitMQ) se descargan de **ECR Public** (`public.ecr.aws/docker/library/...`) para
evitar los límites de descarga de Docker Hub.

### 6.17 CloudWatch Logs, S3 y Lambda

- **CloudWatch Logs:** cada contenedor envía su salida a un grupo de logs (`/warehouse-platform/<servicio>`),
  con retención de 7 días. `aws logs tail /warehouse-platform/wh-002-outbox --follow`.
- **S3:** bucket `warehouse-platform-<sufijo>`, versionado, cifrado y sin acceso público.
  **Detalle aprendido en el despliegue real:** el Learner Lab tiene una política (SCP) que prohíbe leer la
  configuración *Object Lock* de los buckets, algo que Terraform hace siempre tras crear uno. Solución: el
  bucket se crea con AWS CLI (`create_with_cli = true`) y Terraform lo lee con un `data` source.
- **Lambda:** ver 4.10.

### 6.18 IAM y las restricciones del Learner Lab

- **IAM** controla **quién puede hacer qué** en AWS. Un **rol** es un conjunto de permisos que asume un
  servicio (una EC2, una tarea ECS, una Lambda).
- **Learner Lab no permite crear roles** → se usan los que ya existen: `LabRole` (ECS y Lambda) y
  `LabInstanceProfile` (EC2).
- **Otras restricciones:** credenciales que caducan (~4 h), presupuesto de ~100 USD y algunas acciones
  bloqueadas (por ejemplo, la de S3 anterior o consultar cuotas de servicio).

---

## 7. Terraform: la infraestructura como código

### 7.1 Qué es y por qué

**Terraform** permite **describir la infraestructura en archivos de texto** (lenguaje **HCL**) y crearla,
modificarla o destruirla con un comando. En vez de hacer 200 clics en la consola de AWS:

- es **reproducible**: `terraform apply` crea lo mismo en cualquier cuenta;
- es **revisable**: los cambios van en Git como el código;
- es **destruible**: `terraform destroy` lo borra todo y deja de cobrar.

### 7.2 Conceptos del lenguaje

| Concepto | Qué es | Ejemplo |
|---|---|---|
| **provider** | Plugin que sabe hablar con un servicio (AWS) | `provider "aws" { region = "us-east-1" }` |
| **resource** | Algo que Terraform **crea** | `resource "aws_vpc" "this" { cidr_block = var.cidr }` |
| **data** | Algo que ya existe y Terraform **lee** | `data "aws_iam_role" "lab" { name = "LabRole" }` |
| **variable** | Parámetro de entrada | `variable "warehouse_instance_type" { default = "t3.small" }` |
| **output** | Valor que se muestra al terminar | `output "alb_dns" { value = ... }` |
| **module** | Carpeta reutilizable de recursos, como una función | `module "vpc_warehouse" { source = "./modules/vpc" ... }` |
| **for_each** | Crear una copia por cada elemento de un mapa | Una VPC, una EC2 y un peering por bodega |
| **locals** | Valores calculados para reutilizar | `warehouse_ips = { WH-001 = "10.1.10.10", ... }` |
| **templatefile** | Rellenar una plantilla con variables | Generar el `docker-compose.yml` de cada bodega |
| **state** | Archivo `terraform.tfstate` donde Terraform recuerda qué creó | Si se borra, Terraform "olvida" la infraestructura |

### 7.3 Cómo se usa (ciclo de vida)

```text
terraform init      → descarga el provider de AWS y prepara los módulos
terraform validate  → revisa la sintaxis
terraform test      → ejecuta pruebas (aquí, con AWS simulado)
terraform plan      → muestra qué va a crear, cambiar o borrar, SIN hacerlo
terraform apply     → lo hace (pide confirmación)
terraform output    → muestra URLs, IDs, IPs
terraform destroy   → borra todo
```

### 7.4 Cómo está organizado el código

```text
infrastructure/terraform/
  providers.tf          provider AWS 5.x, random, archive; etiquetas comunes (Project, ManagedBy)
  backend.tf            estado local (terraform.tfstate)
  variables.tf          parámetros: CIDRs, bodegas, tamaños, réplicas, my_ip_cidr...
  main.tf               une los módulos (el "plano general")
  outputs.tf            URLs, VPCs, peerings, IDs de EC2, comandos SSM...
  terraform.tfvars      TUS valores (no va a Git): tu IP, etc.
  tests/topology.tftest.hcl   pruebas con AWS simulado
  modules/
    vpc/        VPC + subredes pública/privada + IGW + NAT + rutas
    peering/    peering central↔bodega + rutas en ambos lados
    warehouse/  SG + NACL + logs + EC2 con user_data (una bodega)
    central/    ECS (cluster, tareas, servicios), ALB, NLB, EFS, broker, SG de la central
    database/   RDS PostgreSQL
    ecr/        repositorios de imágenes
    storage/    bucket S3
    lambda/     función Lambda + disparador S3
infrastructure/aws/
  user_data.sh.tftpl          script de arranque de las EC2
  warehouse-compose.yml.tftpl docker compose de cada bodega
```

### 7.5 Cómo se generan "3 bodegas" con un solo código

```hcl
# variables.tf — añadir una bodega es añadir una línea
variable "warehouses" {
  default = {
    "WH-001" = { cidr = "10.1.0.0/16", listener_port = 8001 }
    "WH-002" = { cidr = "10.2.0.0/16", listener_port = 8002 }
    "WH-003" = { cidr = "10.3.0.0/16", listener_port = 8003 }
  }
}

# main.tf
module "vpc_warehouse" {
  source   = "./modules/vpc"
  for_each = var.warehouses              # 3 VPCs
  cidr     = each.value.cidr
  az_count = 1
}
module "peering"   { for_each = var.warehouses ... }   # 3 peerings
module "warehouse" {
  for_each     = var.warehouses                        # 3 EC2
  denied_cidrs = [for other, w in var.warehouses : w.cidr if other != each.key]   # las OTRAS bodegas → NACL
  ...
}
```

**Ejemplo:** para añadir una cuarta bodega, basta con `"WH-004" = { cidr = "10.4.0.0/16", listener_port = 8004 }`;
Terraform crea su VPC, su peering, su EC2, su SG, su NACL, su listener en el ALB y actualiza las NACL de
las otras tres para denegarla.

### 7.6 Orden de creación (dependencias)

Terraform deduce el orden a partir de las referencias entre recursos. En la práctica:

```text
VPCs → subredes → IGW/NAT → peerings y rutas
     → SG de la central → NLB del broker (su DNS va en el compose de las bodegas) → EC2 de las bodegas
     → RDS (5-10 min) → task definitions (necesitan la dirección del RDS) → servicios ECS
```

Por eso las EC2 aparecieron unos minutos después del inicio del `apply`.

### 7.7 Pruebas de Terraform sin AWS

`tests/topology.tftest.hcl` usa `mock_provider "aws"`: Terraform "finge" ser AWS y permite ejecutar
`apply` sin credenciales ni coste. Comprueba que hay 4 VPCs, 3 peerings, que el SG de una bodega solo
acepta tráfico de la central, que la NACL deniega a las otras bodegas, etc.

```hcl
assert {
  condition     = length(output.peering_connections) == 3
  error_message = "Debe haber exactamente 3 peerings (central<->cada bodega), ninguno entre bodegas."
}
```

---

## 8. Recorridos completos de punta a punta

### 8.1 Crear un producto y registrar una entrada

```text
Cliente ──POST http://ALB:8002/products {"sku":"P-001",...}──▶ ALB (VPC central)
  ALB ──peering──▶ EC2 WH-002 :8000 → contenedor api
     1. Pydantic valida el JSON
     2. Transacción: INSERT products + INSERT inventory (0) + INSERT outbox (PRODUCT_CREATED)
     3. Responde 201
Cliente ──POST :8002/inventory/movements {"sku":"P-001","type":"RECEIPT","quantity":10}──▶
     4. Transacción: inventory 0→10 + historial + outbox (INVENTORY_UPDATED, mismo instante)
     5. Borra la caché de P-001 en Redis; responde 201
outbox relay (WH-002) ──AMQP por peering──▶ NLB ──▶ RabbitMQ  (exchange warehouse.events)
     6. Lee pendientes, publica con confirmación, marca published_at
RabbitMQ copia a: q.projection, q.audit, q.cache.wh-002
     7. projection-worker: global_inventory (P-001, WH-002) = 10   (idempotente + last-write-wins)
     8. audit-worker: guarda el evento en audit_log
     9. cache-worker WH-002: borra de nuevo la caché (por si había otra réplica)
Cliente ──GET http://ALB/global/inventory/P-001──▶ central-api → {"total":10, ...}
```

### 8.2 Transferencia de 3 unidades de WH-002 a WH-003

```text
POST http://ALB/transfers {"sku":"P-001","source_warehouse":"WH-002","destination_warehouse":"WH-003","quantity":3}
  central-api: INSERT transfers (REQUESTED) → publica PROCESS_TRANSFER (o lo encola en command_outbox) → 202
  transfer-orchestrator (q.transfers):
     RESERVING_SOURCE    → POST WH-002 /transfers/TR-x/reserve  (reserved +3)
     IN_TRANSIT / DESTINATION_PENDING
                         → POST WH-003 /transfers/TR-x/receive  (WH-003 +3)
     DESTINATION_RECEIVED→ POST WH-002 /transfers/TR-x/commit-out (WH-002 −3, reserved −3)
     COMPLETED           → publica TRANSFER_STATUS_CHANGED
  Cada cambio de inventario en las bodegas genera INVENTORY_UPDATED → la central se actualiza sola.
GET http://ALB/transfers/TR-x → historial completo con cada estado y su hora.
```

Las llamadas del orquestador van **de la central a cada bodega** (permitido). WH-002 y WH-003 nunca se
hablan: la mercancía "viaja" por la central.

### 8.3 Caída del broker (lo que se probó en AWS)

```text
t0  ecs update-service rabbitmq desired=0          (broker apagado)
    WH-002: 5 movimientos → 5 × 201                 (la bodega sigue)
    WH-002: outbox_pending = 5                      (cola local)
    Central: POST /transfers → 202 "queued"         (cola de respaldo)
    Central: /health → pending_commands = 1
t1  ecs update-service rabbitmq desired=1          (vuelve con sus datos de EFS)
    relay WH-002: reconecta → publica 5 pendientes → publica INVENTORY_SNAPSHOT
    command-relay: publica PROCESS_TRANSFER → la saga avanza → COMPLETED
    projection: WH-002 = 20 + 10 − 3 = 27 ; WH-003 = 3
    reporte: WH-002 in_sync = true, last_snapshot_at = <hora>
```

### 8.4 Reporte de bodegas

```text
GET  http://ALB/reports/warehouses                     → JSON inmediato (estado, sync, inventario, transferencias)
POST http://ALB/reports {"report_type":"warehouses_summary"} → 202 {"job_id":"JOB-x"}
     report-worker (q.reports) → construye el mismo resumen → S3 reports/warehouses/AAAA/MM/JOB-x.json
GET  http://ALB/reports/jobs/JOB-x                     → {"status":"COMPLETED","s3_key":"reports/..."}
```

### 8.5 La central comprueba en vivo que llega a cada bodega

`GET http://ALB/warehouses/connectivity`: la central llama a `/health` y `/sync/status` de cada bodega por
la red privada y devuelve `reachable`, `latency_ms` y el backlog del outbox. Es la prueba funcional de
"la central accede a todas las bodegas".

---

## 9. Aislamiento entre bodegas: cómo se garantiza

Tres capas **independientes**: aunque una fallara, las otras siguen bloqueando.

| Capa | Mecanismo | Qué impide |
|---|---|---|
| 1. Rutas | No hay peering entre bodegas, y el peering no es transitivo | No existe camino de red de WH-001 a WH-002 |
| 2. Security Group | La entrada a cada bodega solo se permite desde `10.0.0.0/16` | Aunque existiera un camino, la máquina rechaza el tráfico |
| 3. NACL | Regla `deny` explícita a las CIDR de las otras bodegas (entrada y salida) | Bloqueo a nivel de toda la subred, evaluado antes del `allow` |
| Extra | La lista de bodegas (`WAREHOUSES`) solo la tiene la central | Una bodega ni siquiera sabe la dirección de las otras |

**Cómo se demostró en AWS** (`scripts/aws_verify.sh`, sección 5): desde **dentro** de cada EC2 (por SSM):

```text
✔ WH-001 -> broker central :5672 abierto
✔ WH-001 -> WH-002 (10.2.10.10:8000) BLOQUEADO
✔ WH-001 -> WH-003 (10.3.10.10:8000) BLOQUEADO
... (igual para WH-002 y WH-003)
```

Además se comprobó con la API de AWS que solo existen 3 peerings, que las tablas de rutas de cada bodega
solo apuntan a `10.0.0.0/16`, que cada SG acepta solo la central y que cada NACL deniega las otras dos.

---

## 10. Rendimiento: 1000-2000 peticiones por segundo

Qué se hizo para soportar ese volumen (las peticiones vienen de las 3 bodegas):

| Punto de presión | Medida |
|---|---|
| La API de bodega recibe muchas peticiones | Uvicorn con `--workers 2`; Redis absorbe las lecturas; la bodega responde sin esperar a la central |
| Convertir escrituras en eventos | Outbox (una inserción más en la misma transacción, barata) |
| Enviar eventos al broker | 2 réplicas del relay por bodega en paralelo (`SKIP LOCKED`), lotes de 100 |
| Absorber picos | RabbitMQ encola lo que la central aún no procesó (amortiguador) |
| Procesar en la central | 2 `projection-worker` competidores (`prefetch` 10); escalables con `projection_workers` |
| Consultas a la central | 2 tareas `central-api` detrás del ALB; autoescalado por CPU opcional (`enable_autoscaling`) |
| Broker | 1 vCPU / 2 GB por defecto; `broker_cpu` y `broker_memory` configurables |

**Ser honestos en la defensa:** la arquitectura está **preparada para escalar** (todo se ajusta con
variables de Terraform), pero en este despliegue **no se ejecutó una prueba de carga de 2000 req/s**. Para
hacerla: subir `warehouse_instance_type` (por ejemplo, a `t3.medium`), `broker_cpu`/`broker_memory`,
`projection_workers` y `central_api_count`, y ejecutar `tests/load/run_k6.sh` contra las URLs del ALB.
Un punto a vigilar es RabbitMQ sobre EFS: el disco de red tiene más latencia que un disco local.

---

## 11. Pruebas y verificación

| Nivel | Qué | Cómo se ejecuta | Resultado |
|---|---|---|---|
| Unitarias | Lógica de cada servicio sin dependencias externas (131 tests) | `scripts/run_tests.sh` | Todas pasan |
| Integración | Contra PostgreSQL y RabbitMQ reales en Docker | `scripts/run_tests.sh --all` | — |
| Fallos (local) | 8 escenarios: Redis caído, broker caído, worker parado, duplicados, bodega caída, sagas, DLQ y **caso 8: broker caído + colas + resincronización** | `tests/failure/0X_*.sh` | El caso 8 pasa completo |
| Infraestructura | Topología con AWS simulado | `terraform test` | 3/3 |
| AWS real | Red, aislamiento, Docker, ECS, funcional, caída y persistencia del broker | `scripts/aws_verify.sh --chaos` | **90/90** |

**Ejemplos de tests unitarios importantes:**

- `test_wh_sync.py::test_snapshot_version_matches_last_event`: la versión del snapshot coincide con la
  del último evento (el bug real de 5.10).
- `test_central_api.py::test_broker_down_queues_command_and_relay_flushes`: con el broker caído, la
  transferencia se encola; el relay falla mientras el broker siga caído y la publica cuando vuelve.
- `test_warehouses_sync_reports.py::test_snapshot_resyncs_read_model_without_overwriting_newer`: el
  snapshot corrige un dato divergente pero **no** pisa uno más nuevo.

**Cómo funciona una prueba con objetos falsos:** en vez de un RabbitMQ real se inyecta un
`FakeCommandPublisher` con un interruptor `down = True` que simula la caída. Así se prueba la lógica en
milisegundos y sin infraestructura.

---

## 12. Operación: desplegar, verificar, destruir

En Windows, ejecuta los scripts `.sh` con Git Bash: `& "C:\Program Files\Git\bin\bash.exe" scripts/<script>.sh`.

| Paso | Comando | Qué hace |
|---|---|---|
| 1. Credenciales | `scripts/lab_credentials.sh` | Pega el bloque de *AWS Details* del lab |
| 2. Desplegar | `scripts/aws_deploy.sh` | Crea `terraform.tfvars` con tu IP, ECR, construye y sube las imágenes, aplica todo |
| 3. Verificar | `scripts/aws_verify.sh [--chaos]` | 90 comprobaciones con AWS CLI |
| 4. Usar | `http://<ALB>/docs` (central), `http://<ALB>:8001/docs` (bodegas) | Swagger interactivo (escribe `http://`) |
| 5. Destruir | `scripts/aws_deploy.sh --destroy` | Borra todo y deja de cobrar (~0,50 USD/h encendido) |

**Problemas reales encontrados al desplegar (y cómo se resolvieron):**

1. **Descripciones de reglas SG con `>`:** AWS las rechaza → se cambió el texto.
2. **SCP del Learner Lab sobre S3 Object Lock:** el bucket se crea con AWS CLI.
3. **Pruebas que no reintentaban:** `wait_until` evaluaba la condición una sola vez → ahora la reevalúa.
4. **La prueba de caos empezaba antes de que el broker se apagara de verdad:** ECS da el servicio por
   estable antes de que la tarea termine de pararse → el script espera a que la tarea esté `STOPPED`.
5. **Snapshot siempre descartado** (local, antes de AWS): instantes distintos en la fila y en el evento → se unificaron.

Cada uno es un buen ejemplo para la defensa de por qué **verificar en el entorno real** importa.

---

## 13. Preguntas de defensa

**¿Por qué una VPC por bodega y no una sola VPC con subredes?**
Porque es el aislamiento de red más fuerte: dos VPCs no se comunican salvo que se conecten
explícitamente. Con una sola VPC, todo depende de que los SG estén bien configurados.

**¿Cómo garantizan que las bodegas no se vean entre sí?**
Con tres capas: sin rutas (el peering no es transitivo), SG que solo acepta la central y NACL con
`deny` explícito. Se demostró con `nc` desde dentro de cada EC2.

**¿Por qué la central en ECS y las bodegas en EC2?**
La central son 7 servicios que escalan por separado y deben reiniciarse solos: es justo lo que hace ECS.
Cada bodega es una "sede" con su base de datos local: una máquina por bodega lo modela directamente
(y así lo pide el enunciado).

**¿Qué pasa si se cae el broker?**
Nada se detiene: las bodegas guardan los eventos en su outbox y la central guarda los comandos en
`command_outbox`. Al volver, los relays lo envían todo en orden; cada bodega manda además un snapshot
para resincronizarse. Los mensajes que ya estaban en el broker sobreviven gracias a EFS.

**¿Y si un mensaje llega dos veces?**
Idempotencia: `processed_events` en la central y `processed_commands` en las bodegas. El segundo
mensaje se ignora.

**¿Y si llegan desordenados?**
Last-write-wins por `occurred_at`: un evento más viejo que el dato guardado se descarta.

**¿Por qué RabbitMQ y no llamar a la central por HTTP?**
Desacopla (la bodega no depende de la central), amortigua picos, reparte el trabajo entre varios
consumidores y trae reintentos y DLQ.

**¿Por qué Saga y no una transacción distribuida?**
No hay una transacción que abarque dos bases de datos en máquinas distintas. La saga usa transacciones
locales y compensaciones, y su estado está en un único sitio (el orquestador), consultable.

**¿Qué es CQRS aquí?**
Las bodegas escriben y la central mantiene una copia de lectura (`global_inventory`) alimentada por
eventos. Las consultas globales son rápidas y no dependen de que todas las bodegas respondan.

**¿Por qué las EC2 están en la subred privada?**
Para que no sean accesibles desde Internet. Los clientes entran por el ALB de la central y los
administradores por SSM. Salen a Internet solo mediante el NAT, para instalar paquetes y descargar imágenes.

**¿Cuánto cuesta y qué es lo más caro?**
Unos 0,50 USD/h. Lo más caro son los 4 NAT Gateways. En producción se usarían VPC endpoints.

**¿Qué mejorarían para producción?**
Amazon MQ en clúster multi-AZ, HTTPS con dominio y certificado, Secrets Manager para las contraseñas,
estado de Terraform remoto (S3), autoescalado por bodega, VPC endpoints y una prueba de carga formal.

---

## 14. Glosario

| Término | Significado |
|---|---|
| **Ack** | Confirmación de que un mensaje se procesó; RabbitMQ lo borra de la cola |
| **ALB / NLB** | Balanceador de carga de aplicación (HTTP) / de red (TCP) |
| **AMI** | Imagen del sistema operativo con la que arranca una EC2 |
| **AZ** | Zona de disponibilidad: centro de datos independiente dentro de una región |
| **Broker** | Servidor intermediario de mensajes (RabbitMQ) |
| **Cache Aside** | Leer de la caché; si no está, de la BD, y guardar en la caché |
| **CIDR** | Notación de rango de IPs (`10.1.0.0/16`) |
| **Compensación** | Paso de una saga que deshace uno anterior |
| **Consistencia eventual** | Los datos terminan coincidiendo, aunque no al instante |
| **Contenedor / imagen** | Programa empaquetado en ejecución / el paquete |
| **CQRS** | Separar el modelo de escritura del de lectura |
| **DLQ** | Cola de mensajes que no se pudieron procesar |
| **ECS / Fargate** | Servicio que ejecuta contenedores / modo sin administrar servidores |
| **EFS** | Disco de red gestionado (NFS) |
| **Evento / comando** | Algo que ocurrió (hecho) / algo que se ordena hacer |
| **Exchange / cola / routing key** | Clasificador de mensajes / buzón / etiqueta de destino |
| **Heartbeat** | Señal periódica de "sigo vivo" |
| **IaC** | Infraestructura como código (Terraform) |
| **Idempotencia** | Aplicar algo dos veces tiene el mismo efecto que una |
| **IGW / NAT** | Puerta a Internet / salida a Internet para subredes privadas |
| **NACL / SG** | Firewall de subred sin estado / firewall de máquina con estado |
| **Outbox** | Tabla local con los eventos pendientes de enviar, escrita en la misma transacción que el cambio |
| **Peering** | Conexión privada entre dos VPCs (no transitiva) |
| **Read model** | Copia de los datos optimizada para consultas |
| **Saga** | Proceso de varios pasos con compensaciones, sin transacción global |
| **SCP** | Política de la organización que limita lo que puede hacer una cuenta (Learner Lab) |
| **Snapshot** | Estado completo de una bodega enviado para resincronizar |
| **SSM** | Servicio para ejecutar comandos en las EC2 sin SSH |
| **Stale** | Dato posiblemente desactualizado (la bodega no da señales desde hace más de 60 s) |
| **Task definition / service (ECS)** | Receta de un contenedor / mantiene N copias vivas |
| **TTL** | Tiempo de vida antes de que algo caduque |
| **user_data** | Script que ejecuta una EC2 al arrancar por primera vez |
| **VPC / subred** | Red privada virtual / trozo de esa red en una AZ |

---

**Documentos relacionados:** decisiones formales en `docs/adr/` (ADR-001 … ADR-016), contratos de eventos
en `docs/events.md`, despliegue en `docs/aws-deployment.md` y arquitectura en `docs/architecture.md`.
