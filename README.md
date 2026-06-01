[![Tests](https://github.com/OpenDisplay/silabs-ble-ota/actions/workflows/test.yml/badge.svg)](https://github.com/OpenDisplay/silabs-ble-ota/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/silabs-ble-ota)](https://pypi.org/project/silabs-ble-ota/)
[![Python Version](https://img.shields.io/pypi/pyversions/silabs-ble-ota)](https://pypi.org/project/silabs-ble-ota/)

# silabs-ble-ota

Flash firmware to Silicon Labs EFR32 devices over BLE from Python, using the
**Silicon Labs AppLoader** OTA GATT service (a `.gbl` image). Transport-agnostic
— it uses [`bleak-retry-connector`](https://github.com/Bluetooth-Devices/bleak-retry-connector)'s
`establish_connection`, so the same code flashes over a **direct** Bluetooth
adapter or an **ESPHome Bluetooth proxy**.

## Installation

```bash
pip install silabs-ble-ota
```

## Library

The device must already be in the **AppLoader** (OTA bootloader) when you call
`perform_silabs_ota`. Triggering the bootloader and re-discovering the device is
vendor/device specific and is the caller's responsibility.

```python
from silabs_ble_ota import SilabsOTAError, perform_silabs_ota

# `ble_device` is a bleak BLEDevice already in (or booting into) the AppLoader,
# at the same address as the application:
gbl = open("firmware.gbl", "rb").read()

try:
    await perform_silabs_ota(
        gbl,
        ble_device,
        on_progress=lambda pct: print(f"{pct:.0f}%"),
        on_log=print,
    )
except SilabsOTAError as exc:
    print(f"OTA failed: {exc}")
```

### `perform_silabs_ota(gbl_bytes, ble_device, on_progress=None, on_log=None, *, fast=False)`

| Argument | Description |
|----------|-------------|
| `gbl_bytes` | Raw `.gbl` firmware bytes. |
| `ble_device` | A bleak `BLEDevice` already in (or booting into) the AppLoader, at the same address as the application. |
| `on_progress` | Optional callback, called with a float percentage `0–100`. |
| `on_log` | Optional callback for human-readable status messages. |
| `fast` | `True` uses a larger write-without-response window for a ~2× faster transfer. **Only safe on a direct connection** — see below. Defaults to `False`. |

Raises `SilabsOTAError` if the connection or transfer fails, or the device is not
in OTA mode. No external sleep is needed before calling — it retries the
*connect* itself while the AppLoader boots.

## Reliability over Bluetooth proxies

The Silicon Labs AppLoader has **no packet-receipt flow control**. Over an
ESPHome proxy (which forwards write-without-response with no backpressure), an
unacknowledged data write can be **silently dropped** when the device's buffer
is full — producing a complete-looking stream but an incomplete image that fails
the finalize step. By default this library therefore **acknowledges every data
write** (`response=True`), so no chunk is silently lost, and retries on a proxy
`Congested` signal. This is the safe default and is required through a proxy.

### `fast=True` for direct connections

On a **direct** Bluetooth adapter (BlueZ, CoreBluetooth) the OS socket
backpressures write-without-response, so chunks are never silently dropped.
Passing `fast=True` then streams a window of write-without-response chunks
between acks for roughly **2× the throughput** (measured ~58s vs ~125s for a
234 KB image on EFR32BG22). **Do not** use `fast=True` through an ESPHome proxy —
dropped chunks will corrupt the image.

It also:
- connects with `use_services_cache=False` (fresh GATT discovery — the AppLoader
  has a different service table than the application at the same address);
- treats the connection as **one-shot** (the AppLoader reboots to the application
  when the connection drops), retrying only the *connect*, never reconnecting
  mid-flash;
- identifies the OTA service by its **characteristic** UUIDs (the OTA service
  UUID varies between AppLoader builds).

## License

Apache-2.0
