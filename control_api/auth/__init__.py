"""Auth for the control API: argon2 password + TOTP 2FA, JWTs, fresh-2FA guard, rate limits,
CORS/security headers, audit log, owner bootstrap CLI. See DASHBOARD_PLAN.md "Interface contract -> Auth".
"""
from .audit import audit
from .deps import current_user, require_fresh_2fa
from .middleware import install_security
from .routes import router

__all__ = ["audit", "current_user", "require_fresh_2fa", "install_security", "router"]
