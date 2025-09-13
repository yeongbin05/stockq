import factory
from django.contrib.auth import get_user_model
from stocks.models import Stock, FavoriteStock

User = get_user_model()

class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User

    email = factory.Sequence(lambda n: f"user{n}@test.com")
    password = factory.PostGenerationMethodCall("set_password", "password123")

class StockFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Stock

    symbol = factory.Sequence(lambda n: f"SYM{n}")
    name = factory.Faker("company")
    exchange = "NASDAQ"

class FavoriteStockFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = FavoriteStock

    user = factory.SubFactory(UserFactory)
    stock = factory.SubFactory(StockFactory)
