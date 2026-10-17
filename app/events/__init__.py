"""Event bus and live channel (R17, R19). See ``docs/EVENTOS.md``."""

from app.events.bus import DomainEvent, publish, register_reactor
from app.events.reactors import notification_reactor

# The inbox reactor is part of the system, not of any one feature.
register_reactor(notification_reactor)

# Billing (R19 flow 3). Imported after the bus so the module can use it.
from app.billing.reactors import billing_reactor  # noqa: E402

register_reactor(billing_reactor)

# Critical alerts (R20) and pending-work counters (R18). Registered last: the
# counters are computed after the inbox and the invoices are up to date.
from app.events.flows import ai_auto_reactor, alert_reactor, counters_reactor  # noqa: E402

register_reactor(alert_reactor)
register_reactor(counters_reactor)

# Automatic AI analysis: schedules background work, so its position is irrelevant.
register_reactor(ai_auto_reactor)

__all__ = ["DomainEvent", "publish", "register_reactor"]
