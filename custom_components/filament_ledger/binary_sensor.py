"""Home Assistant binary-sensor entry point; implementation stays in infrastructure."""

from .infrastructure.ha.binary_sensor import SpoolLowStockSensor, async_setup_entry

__all__ = ["SpoolLowStockSensor", "async_setup_entry"]
