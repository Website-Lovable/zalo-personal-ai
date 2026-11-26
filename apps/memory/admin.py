from django.contrib import admin

from .models import LongTermMemory, ShortTermMemory

admin.site.register(ShortTermMemory)
admin.site.register(LongTermMemory)
