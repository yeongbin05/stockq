import http from "k6/http";
import { check, sleep } from "k6";

const BASE_URL = __ENV.BASE_URL || "https://stockqapp.com";
const PATH = "/api/stocks/summaries/";

export const options = {
  scenarios: {
    read_p95: {
      executor: "constant-vus",
      vus: Number(__ENV.VUS || 10),
      duration: __ENV.DURATION || "60s",
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.01"],
    http_req_duration: [
      "p(95)<500",
      "p(99)<1000",
    ],
  },
};

export function setup() {
  const res = http.post(
    `${BASE_URL}/api/auth/token/`,
    JSON.stringify({
      email: __ENV.EMAIL,
      password: __ENV.PASSWORD,
    }),
    {
      headers: { "Content-Type": "application/json" },
    }
  );

  check(res, {
    "token issued (200)": (r) => r.status === 200,
  });

  if (res.status !== 200) {
    throw new Error(`login failed: status=${res.status}, body=${res.body}`);
  }

  const body = res.json();
  return { access: body.access };
}

export default function (data) {
  const url = `${BASE_URL}${PATH}`;

  const params = {
    headers: {
      Authorization: `Bearer ${data.access}`,
    },
    tags: { name: "summary_read" },
  };

  const res = http.get(url, params);

  check(res, {
    "status 200": (r) => r.status === 200,
  });

  sleep(0.2);
}