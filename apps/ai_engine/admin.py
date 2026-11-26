from django.contrib import admin

from .models import CommunicationStyle, LLMUsage, OwnerProfile

admin.site.register(OwnerProfile)
admin.site.register(CommunicationStyle)
admin.site.register(LLMUsage)
