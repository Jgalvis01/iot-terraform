import http from 'k6/http';
import { check } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://host.docker.internal:8001';
const SKUS = (__ENV.SKUS || 'P-001,P-002,P-003,P-004,P-005').split(',');

export const options = {
  scenarios: {
    lectura: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '15s', target: Number(__ENV.VUS || 100) },
        { duration: __ENV.DURATION || '60s', target: Number(__ENV.VUS || 100) },
        { duration: '10s', target: 0 },
      ],
    },
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
  thresholds: { http_req_failed: ['rate<0.05'] },
};

export default function () {
  const sku = SKUS[Math.floor(Math.random() * SKUS.length)];
  const res = http.get(`${BASE_URL}/inventory/by-sku/${sku}`, {
    headers: { 'X-Correlation-ID': `COR-K6-${__VU}-${__ITER}` },
    tags: { name: 'GET /inventory/by-sku' },
  });
  check(res, { 'status 200': (r) => r.status === 200 });
}

export function handleSummary(data) {
  const name = __ENV.RESULT_NAME || 'inventory_read';
  return {
    stdout: textSummary(data),
    [`/results/${name}.json`]: JSON.stringify(data, null, 2),
  };
}

function textSummary(data) {
  const d = data.metrics.http_req_duration.values;
  const f = data.metrics.http_req_failed.values.rate;
  const rps = data.metrics.http_reqs.values.rate;
  return `\n=== ${__ENV.RESULT_NAME || 'inventory_read'} ===\nRPS=${rps.toFixed(1)}  P50=${d.med.toFixed(1)}ms  P95=${d['p(95)'].toFixed(1)}ms  P99=${d['p(99)'].toFixed(1)}ms  error_rate=${(f * 100).toFixed(2)}%\n`;
}
