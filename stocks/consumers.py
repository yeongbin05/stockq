from channels.generic.websocket import AsyncJsonWebsocketConsumer


class NotifyConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        if self.scope.get('user') and self.scope['user'].is_authenticated:
            self.group_name = f'user_{self.scope["user"].id}'
            await self.channel_layer.group_add(self.group_name, self.channel_name)
            await self.accept()
        else:
            await self.close()

    async def disconnect(self, close_code):
        if hasattr(self, 'group_name'):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def notify(self, event):
        await self.send_json(event.get('data', {}))

