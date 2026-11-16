BEGIN;

INSERT INTO products (sku, name, description, category, unit, created_at, updated_at) VALUES
  ('P-001', 'Caja de carton 40x30',   'Caja corrugada mediana',        'Empaque',     'unidad', now(), now()),
  ('P-002', 'Cinta de embalaje',      'Cinta transparente 48mm x 100m', 'Empaque',     'rollo',  now(), now()),
  ('P-003', 'Pallet de madera',       'Pallet estandar 120x100',        'Logistica',   'unidad', now(), now()),
  ('P-004', 'Etiqueta termica 4x6',   'Rollo de 500 etiquetas',         'Etiquetado',  'rollo',  now(), now()),
  ('P-005', 'Film stretch',           'Film extensible 500mm',          'Empaque',     'rollo',  now(), now())
ON CONFLICT (sku) DO NOTHING;

INSERT INTO inventory (product_id, warehouse_id, quantity, reserved_quantity, updated_at)
SELECT p.id, 'WH-001', v.qty, 0, now()
FROM products p
JOIN (VALUES ('P-001', 120), ('P-002', 80), ('P-003', 40), ('P-004', 200), ('P-005', 60)) AS v(sku, qty)
  ON v.sku = p.sku
ON CONFLICT (product_id) DO NOTHING;

COMMIT;
