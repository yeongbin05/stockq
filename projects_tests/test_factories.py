import pytest
from tests.factories import UserFactory, StockFactory, FavoriteStockFactory

@pytest.mark.django_db
def test_user_factory_creates_user():
    user = UserFactory()
    assert "@test.com" in user.email
    assert user.check_password("password123")

@pytest.mark.django_db
def test_favorite_factory_creates_favorite():
    fav = FavoriteStockFactory()
    assert fav.user is not None
    assert fav.stock is not None
