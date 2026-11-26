from django.apps import AppConfig


class ZaloChannelsConfig(AppConfig):
    name = "apps.channels"
    # "channels" is taken by the Django Channels library.
    label = "zalo_channels"
    verbose_name = "Kênh Zalo"
