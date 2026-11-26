"""Role-based access control: owner > admin > viewer.

- viewer: read-only access to the tenant's data
- admin: create / update / delete business data
- owner: everything, plus owner-only actions (users, risk acknowledgement, tenant settings)
"""

from rest_framework.permissions import SAFE_METHODS, BasePermission

ROLE_LEVELS = {"viewer": 1, "admin": 2, "owner": 3}


def role_at_least(role: str, minimum: str) -> bool:
    return ROLE_LEVELS.get(role, 0) >= ROLE_LEVELS[minimum]


class IsTenantMember(BasePermission):
    """Authenticated user attached to an active tenant."""

    message = "Tài khoản chưa thuộc tenant nào hoặc tenant đã bị khóa."

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        tenant = getattr(user, "tenant", None)
        return tenant is not None and tenant.is_active


class RolePermission(BasePermission):
    """Read for every member; writes need ``view.write_role`` (default admin).

    A view can set ``action_roles = {"action_name": "owner"}`` for stricter actions.
    """

    message = "Bạn không có quyền thực hiện thao tác này."

    def required_role(self, request, view) -> str:
        action = getattr(view, "action", None)
        action_roles = getattr(view, "action_roles", {}) or {}
        if action in action_roles:
            return action_roles[action]
        if request.method in SAFE_METHODS:
            return getattr(view, "read_role", "viewer")
        return getattr(view, "write_role", "admin")

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        return role_at_least(user.role, self.required_role(request, view))
