import http from "k6/http";
import { check, sleep } from "k6";

export const options = {
  vus: 10,
  duration: "120s",
  summaryTrendStats: ["avg", "min", "med", "max", "p(90)", "p(95)", "p(99)"],
};

const BASE_URL = __ENV.BASE_URL || "http://127.0.0.1:8000";

export function setup() {
  // ✅ 여기 계정/패스워드만 맞추면, k6가 매번 토큰을 스스로 받아서 씁니다.
  const payload = JSON.stringify({
    email: __ENV.EMAIL,       // 또는 username 필드면 username으로 변경
    password: __ENV.PASSWORD,
  });

  const res = http.post(`${BASE_URL}/api/auth/token/`, payload, {
    headers: { "Content-Type": "application/json" },
  });

  check(res, {
    "token issued (200)": (r) => r.status === 200,
  });

  const data = res.json();
  return { access: data.access };
}

export default function (data) {
  const res = http.get(`${BASE_URL}/api/stocks/summaries/`, {
    headers: {
      Authorization: `Bearer ${data.access}`,
    },
  });

  check(res, {
    "summaries 200": (r) => r.status === 200,
  });

  sleep(1);
}
