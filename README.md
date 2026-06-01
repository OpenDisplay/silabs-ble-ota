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
from silabs_ble_ota import perform_silabs_ota

# `ble_device` is a bleak BLEDevice already in (or booting into) the AppLoader,
# at the same address as the application:
gbl = open("firmware.gbl", "rb").read()
await perform_silabs_ota(gbl, ble_device, on_progress=lambda p: print(f"{p:.0f}%"))
```

## Reliability over Bluetooth proxies

The Silicon Labs AppLoader has **no packet-receipt flow control**. Over an
ESPHome proxy (which forwards write-without-response with no backpressure), an
unacknowledged data write can be **silently dropped** when the device's buffer
is full — producing a complete-looking stream but an incomplete image that fails
the finalize step. This library therefore **acknowledges every data write**
(`response=True`), so no chunk is silently lost, and retries on a proxy
`Congested` signal. On a direct adapter this is more conservative than necessary
but still correct.

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
