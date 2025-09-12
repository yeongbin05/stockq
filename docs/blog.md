# Django 모델 개선기: FavoriteStock 리팩터링

## 배경
`FavoriteStock` 모델은 사용자별 즐겨찾는 종목을 저장하는 핵심 테이블이다.  
처음 구현했을 때는 `unique_together`와 `__str__`에서 `user.email`을 사용했는데, 운영 관점에서 몇 가지 문제가 발견됐다.

---

## 문제점

### 1. `unique_together`는 구식
Django 2.2 이후부터는 `unique_together`가 **deprecated** 상태다. 앞으로 유지보수 시 경고가 뜨고, 장기적으로는 제거될 가능성이 있다.

### 2. FK 인덱스 부재
`user`, `stock`을 기준으로 조회하는 경우가 많다. 하지만 ForeignKey에 인덱스를 명시하지 않으면, 데이터가 커졌을 때 **조회 속도**와 **중복 체크** 모두 느려질 수 있다.

### 3. 개인정보 노출 위험
`__str__` 메서드에서 `self.user.email`을 반환하면, 관리자 페이지나 로그 출력 시 사용자의 **이메일이 그대로 노출**된다. 이는 PII(개인식별정보) 유출 리스크가 있다.

---

## 개선 방법

### ✅ `UniqueConstraint` 사용
`unique_together` 대신 `UniqueConstraint`를 사용해 **명시적이고 현대적인 제약 조건**으로 교체했다.

### ✅ 인덱스 추가
자주 조회되는 `(user, -created_at)` 조합에 인덱스를 추가해 **즐겨찾기 조회 성능**을 최적화했다.

### ✅ 안전한 `__str__`
`__str__`는 내부적으로만 쓰이므로, 개인정보를 직접 노출하지 않고 `user_id`와 `stock_id` 조합으로 변경했다.

---

## 최종 코드

```python
class FavoriteStock(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='favorites', db_index=True)
    stock = models.ForeignKey("stocks.Stock", on_delete=models.CASCADE, related_name='favorited_by', db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "stock"], name="uniq_user_stock")
        ]
        indexes = [
            models.Index(fields=["user", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.user_id}:{self.stock_id}"
