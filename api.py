import requests, datetime

url = "https://finnhub.io/api/v1/news?category=general&token=YOUR_TOKEN"
resp = requests.get(url)

# 헤더 확인
print(resp.headers)

# 뉴스 데이터
data = resp.json()
for n in data[:3]:
    ts = datetime.datetime.utcfromtimestamp(n["datetime"])
    print(f"{ts} | {n['source']} | {n['headline']}")
