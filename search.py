import requests
FINNHUB_API_KEY = "d1b1kspr01qjhvts0mmgd1b1kspr01qjhvts0mn0"

for ex in ["KRX", "KOSDAQ"]:
    r = requests.get(
        "https://finnhub.io/api/v1/stock/symbol",
        params={"exchange": ex, "token": FINNHUB_API_KEY},
    )
    print(ex, "종목 수:", len(r.json()))