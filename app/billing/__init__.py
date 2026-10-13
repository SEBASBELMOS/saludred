"""Billing (R09): invoices live in MongoDB, apart from the clinical record.

Layout:

* ``tariffs``   -- illustrative COP prices.
* ``store``     -- the pymongo connection and the thin repository over ``facturas``.
* ``service``   -- business rules: scope, state machine, automatic invoices.
* ``reactors``  -- the event-bus reactor that bills on ``reporte_aprobado`` and
                   ``cama_liberada``.
* ``receipt``   -- the PDF receipt.

See ``docs/CONTABILIDAD.md``. This package must not import ``app.events`` at
module level beyond ``app.events.bus``: the events package registers the
billing reactor when it is imported.
"""
