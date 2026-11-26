from django.contrib import admin

from .models import ApprovalQueue, PermissionRule

admin.site.register(PermissionRule)
admin.site.register(ApprovalQueue)
