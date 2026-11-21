import http from 'k6/http';
import { check } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://host.docker.internal:8001';
const SKUS = (__ENV.SKUS || 'P-001,P-002,P-003,P-004,P-005').split(',');

export const options = {
  vus: Number(__ENV.VUS || 50),
  duration: __ENV.DURATION || '60s',
  summaryTrendStats: ['avg', 'med', 'p(95)', 'p(99)', 'max'],
};

export default function () {
  const sku = SKUS[Math.floor(Math.random() * SKUS.length)];
  const r = Math.random();
  const headers = { 'Content-Type': 'application/json', 'X-Correlation-ID': `COR-K6-${__VU}-${__ITER}` };
  if (r < 0.8) {
    const res = http.get(`${BASE_URL}/inventory/by-sku/${sku}`, { headers, tags: { name: 'read' } });
    check(res, { 'read 200': (x) => x.status === 200 });
  } else {
    const type = r < 0.95 ? 'RECEIPT' : 'SHIPMENT';
    const res = http.post(`${BASE_URL}/inventory/movements`,
      JSON.stringify({ sku, type, quantity: 1, reference: `k6-${__VU}-${__ITER}` }), { headers, tags: { name: type } });

    check(res, { 'write ok o rechazo de negocio': (x) => x.status === 201 || x.status === 400 });
  }
}
