# ADR-009 — `sku` como identidad global de producto

**Estado:** Aceptada

## Contexto

Cada bodega tiene su propia base (ADR-001) con claves primarias `SERIAL` locales: el producto `P-001` puede ser `id=1` en WH-001 y `id=7` en WH-002. Las transferencias, los eventos y el read model necesitan referirse al mismo producto de forma inequívoca entre bodegas.

## Alternativas

1. Usar el `id` entero local (colisiona entre bodegas).
2. UUID global asignado por una autoridad central (requiere un catálogo maestro y coordinación al crear productos).
3. Código de negocio **`sku`** (string, p. ej. `P-001`), único por bodega (`products.sku UNIQUE`) y compartido entre bodegas por convención.

## Decisión

Identidad global = `sku`. Los `id` enteros son estrictamente locales:

- Eventos (`INVENTORY_UPDATED`, `PRODUCT_CREATED`) llevan `sku` además de `product_id` (este último solo sirve para construir claves de caché locales).
- `global_inventory` tiene PK `(sku, warehouse_id)`; `transfers.sku`; las rutas de comandos de saga usan `sku` (`/transfers/{id}/reserve` con `{sku, quantity}`).
- `sku` es **inmutable** tras crearse (README del servicio) y las lecturas por sku (`/inventory/by-sku/{sku}`) tienen su propia clave de caché.
- Al recibir una transferencia, si el destino no tiene el producto lo crea con los datos (`name, unit, category`) que el orquestador obtiene de `GET /products/by-sku/{sku}` en la bodega origen (`receive` crea producto + inventario + outbox `PRODUCT_CREATED` en la misma transacción).

## Consecuencias / Trade-offs

- Simple y legible; no necesita servicio de catálogo ni coordinación para crear productos.
- No hay un catálogo maestro: nada impide que dos bodegas den el mismo `sku` a productos distintos (conflicto semántico) o que dos skus distintos nombren el mismo producto. `name/unit/category` pueden divergir entre bodegas (la central no valida).
- Cambiar un `sku` obligaría a migraciones coordinadas: por eso es inmutable.

## Mitigación

- Convención de nomenclatura documentada (`P-001`); la API solo exige `sku` no vacío (`min_length=1`) y único por bodega.
- `receive` copia los atributos del origen, así que los productos nacidos por transferencia son coherentes.
- Propuesta futura: catálogo maestro de productos en la central, con eventos `PRODUCT_CREATED` como punto de detección de conflictos (la proyección ya recibe `PRODUCT_CREATED` de todas las bodegas).
