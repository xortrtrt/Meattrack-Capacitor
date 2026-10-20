"""Compatibility facade for MEATTRACK's domain repositories."""

from app.portal_config import default_section_for, portal_nav, portal_nav_for, roles, team_leader_nav_by_role, team_leader_role_labels
from . import _shared, accounts, orders, inventory, inquiries, forecasts, notifications, refunds, reporting, superuser
from ._shared import *
from .accounts import *
from .orders import *
from .inventory import *
from .inquiries import *
from .forecasts import *
from .notifications import *
from .refunds import *
from .reporting import *
from .superuser import *
__all__ = _shared.__all__ + accounts.__all__ + orders.__all__ + inventory.__all__ + inquiries.__all__ + forecasts.__all__ + notifications.__all__ + refunds.__all__ + reporting.__all__ + superuser.__all__ + ['default_section_for', 'portal_nav', 'portal_nav_for', 'roles', 'team_leader_nav_by_role', 'team_leader_role_labels']
