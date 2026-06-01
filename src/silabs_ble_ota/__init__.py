"""silabs-ble-ota — Silicon Labs AppLoader OTA firmware updates over BLE.

Flash an EFR32 device that is in the Silicon Labs AppLoader (OTA bootloader)
with a ``.gbl`` image. Transport-agnostic (uses bleak-retry-connector's
``establish_connection``), so the same code works over a direct adapter or an
ESPHome Bluetooth proxy. Reliable over proxies: it acknowledges every chunk so
none are silently dropped.

The caller must trigger the bootloader and obtain a fresh ``BLEDevice`` for the
AppLoader first (vendor/device specific)::

    from silabs_ble_ota import perform_silabs_ota

    # device already triggered into the AppLoader and re-discovered:
    await perform_silabs_ota(gbl_bytes, ble_device, on_progress=lambda p: print(f"{p:.0f}%"))
"""

from __future__ import annotations

from ._const import SilabsOTAError
from .ota import perform_silabs_ota

__version__ = "0.1.0"

__all__ = [
    "perform_silabs_ota",
    "SilabsOTAError",
]
