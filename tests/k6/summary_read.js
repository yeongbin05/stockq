import http from "k6/http";
import { check, sleep } from "k6";

const BASE_URL = __ENV.BASE_URL || "https://stockqapp.com";
const PATH = __ENV.PATH || "/api/stocks/summaries/"; // ✅ 나중에 실제 엔드포인트로만 바꾸면 됨
const TOKEN = __ENV.TOKEN || ""; // 선택: 인증 필요하면 Bearer 토큰 넣기

export const options = {
  scenarios: {
    read_p95: {
      executor: "constant-vus",
      vus: Number(__ENV.VUS || 30),
      duration: __ENV.DURATION || "60s",
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.01"],
    http_req_duration: [
      "p(95)<500",  // ✅ 목표 p95(ms) - 필요하면 조정
      "p(99)<1000",
    ],
  },
};

export default function () {
  const url = `${BASE_URL}${PATH}`;

  const params = {
    headers: {
      ...(TOKEN ? { Authorization: `Bearer ${TOKEN}` } : {}),
    },
    tags: { name: "summary_read" },
  };

  const res = http.get(url, params);

  check(res, {
    "status 200": (r) => r.status === 200,
  });

  sleep(0.2);
}
