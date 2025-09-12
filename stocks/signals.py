from django.db.models.signals import post_save
from django.dispatch import receiver
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from .models import News, FavoriteStock, Notification


@receiver(post_save, sender=News)
def create_notifications_on_news(sender, instance: News, created: bool, **kwargs):
    if not created:
        return
    stock = instance.stock
    favorites = FavoriteStock.objects.filter(stock=stock).select_related('user')
    notifications = []
    for fav in favorites:
        notifications.append(Notification(
            user=fav.user,
            stock=stock,
            news=instance,
            message=f"{stock.symbol}: {instance.headline[:100]}"
        ))
    if notifications:
        Notification.objects.bulk_create(notifications, ignore_conflicts=True)
        # Push via Channels
        channel_layer = get_channel_layer()
        for fav in favorites:
            async_to_sync(channel_layer.group_send)(
                f'user_{fav.user_id}',
                {
                    'type': 'notify',
                    'data': {
                        'symbol': stock.symbol,
                        'headline': instance.headline,
                        'news_id': instance.id,
                    }
                }
            )

