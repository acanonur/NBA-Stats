"""The EuroLeague's HTTP surface: ``/v1/el``.

``routes``       the router (prefix ``/el``; ``api/app.py`` mounts it under ``/v1``)
``deps``         the EuroLeague session, the state gate, the clock and the write guard
``serializers``  the three request bodies and the model-settings payload

Every read payload is built in :mod:`nbastats.euroleague.read`, not here, so the dashboard's
widgets and these routes share one implementation. Importing :mod:`~nbastats.euroleague.api.routes`
is what registers the EuroLeague with the league registry; nothing in this package is imported by
the NBA's modules except ``api/routes_euroleague.py``, the three-line shim.
"""
