# ADR-007 — Saga orquestada (vs coreografía) con compensaciones

**Estado:** Aceptada

## Contexto

Transferir stock de WH-A a WH-B toca dos bases distintas (ADR-001): no hay transacción ACID global (README §13.1). Hay que evitar estados como "salió de A y no llegó a B" y poder compensar.

## Alternativas

1. **Two-phase commit** entre bases: complejo, bloqueante, reduce autonomía de bodegas.
2. **Saga coreografiada:** las bodegas reaccionan a eventos de la otra (sin coordinador).
3. **Saga orquestada:** un coordinador central dirige los pasos y las compensaciones.

## Decisión

Saga orquestada en `central`:

- `POST /transfers` (central-api) persiste la transferencia en `REQUESTED` y publica el comando `PROCESS_TRANSFER{transfer_id}` en `platform.commands` (`transfer.process`) **después** del commit. Si no hay broker, borra la fila y responde `503 BROKER_UNAVAILABLE`.
- `transfer-orchestrator` (`workers/saga.py`, consume `q.transfers`, `prefetch=1`) es una **máquina de estados reanudable**: carga la transferencia con `SELECT ... FOR UPDATE` y avanza desde su estado actual. Cada transición se persiste (commit) antes del siguiente paso y luego se publica `TRANSFER_STATUS_CHANGED`.
- Pasos contra las APIs de las bodegas (idempotentes, ADR-006): `reserve` (source) -> `receive` (destination) -> `commit-out` (source). Compensación: `release` (source).

Estados: `REQUESTED -> RESERVING_SOURCE -> SOURCE_RESERVED -> IN_TRANSIT -> DESTINATION_PENDING -> DESTINATION_RECEIVED -> COMPLETED`; fallo: `FAILED` (no se llegó a reservar) o `COMPENSATING -> CANCELLED`.

Reglas de error (según el código):

| Paso | Error | Resultado |
|---|---|---|
| reserve | 404/409 (stock insuficiente, producto inexistente) | `FAILED`, sin compensación |
| reserve | transitorio (timeout/5xx) | retry; al agotar `MAX_RETRIES` => `FAILED` ("source unavailable") y el error pasa a DLQ |
| receive | 404/409 | `COMPENSATING` |
| receive | transitorio | retry; al agotar => `COMPENSATING` (misma ejecución) |
| commit-out | 5xx/timeout | retry; al agotar => DLQ, queda `DESTINATION_RECEIVED` (el destino ya recibió) |
| commit-out o release | rechazo de la bodega | `PermanentError` => DLQ, intervención manual |
| release | transitorio | retry; al agotar => DLQ, queda `COMPENSATING` |

Inyección de fallos para demostración: `FAIL_INJECTION` (`none|before_reserve|after_reserve|destination_down`) y cabecera `X-Fail-Step` por transferencia (columna `fail_step`).

## Consecuencias / Trade-offs

- Lógica del flujo en un solo lugar (fácil de entender, observar y probar), con `history` JSONB por transferencia; a cambio, el orquestador es un componente central (ver impacto: si cae, las transferencias esperan en `q.transfers`; las bodegas siguen operando).
- Coreografía evitaría el coordinador pero dispersaría el flujo entre servicios y haría difícil razonar las compensaciones.
- **Consistencia eventual** y estados intermedios visibles (stock reservado). La saga no aísla: el stock reservado no está disponible para otros movimientos.
- Hay estados que requieren intervención manual (`COMPENSATING` o `DESTINATION_RECEIVED` con DLQ); no hay reanudador automático desde la DLQ.
- `TRANSFER_STATUS_CHANGED` se publica best effort tras el commit (no outbox): el estado persistido es la fuente de verdad.

## Mitigación

- Comandos idempotentes por `(transfer_id, step)`; redelivery seguro.
- `reserved_quantity` explícito en el inventario (`RESERVATION`/`RELEASE`/`TRANSFER_OUT`).
- `GET /transfers/{id}` con `history` para auditoría; logs `transfer_transition`; métrica `transfer_transitions_total{status}`.
- `tests/failure/06_saga_failures.sh` cubre éxito, stock insuficiente, fallo antes/después de reservar, destino caído y redelivery.
