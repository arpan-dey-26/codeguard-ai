"""Data-access layer.

Intentionally empty in Phase 0.

Every SQL statement in the application lives in this package — never in a
route, never in a service.  Two reasons (architecture.md §6.7, §21.6):

* it keeps the SQL-injection surface to one reviewed layer;
* it is where scope filtering is applied, so an endpoint cannot forget it.
"""
