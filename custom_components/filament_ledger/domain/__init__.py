"""The domain layer.

Business rules and nothing else. Zero imports from `homeassistant`, no database access, no
I/O, and no knowledge of how any of it is presented. That constraint is what makes every
rule testable in milliseconds, and it is verified by tests/architecture/.
"""
