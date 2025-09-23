from stocks.utils import allow_request

def test_token_bucket():
    key = "rate_limit:finnhub"

    print("=== 토큰 버킷 테스트 시작 ===")
    results = []
    for i in range(65):
        allowed = allow_request(key, capacity=60, refill_rate=1)
        results.append(allowed)
        print(f"{i+1}번째 요청 → {'허용' if allowed else '차단'}")

    allowed_count = sum(results)
    blocked_count = len(results) - allowed_count
    print(f"허용된 요청 수: {allowed_count}")
    print(f"차단된 요청 수: {blocked_count}")
    print("=== 테스트 끝 ===")

if __name__ == "__main__":
    test_token_bucket()
