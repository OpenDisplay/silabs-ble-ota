"""Silicon Labs AppLoader OTA over BLE.

Flash an EFR32 device that is already in the Silabs AppLoader (OTA bootloader).
The caller is responsible for triggering the bootloader and obtaining a fresh
``BLEDevice`` for the AppLoader first (both are vendor/device specific).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from ._const import (
    APPLOADER_BOOT_DELAY,
    CHUNK_SIZE,
    CONGESTION_BACKOFF,
    CONGESTION_RETRIES,
    CONNECT_ATTEMPTS,
    FAST_WINDOW,
    GBL_MAGIC,
    MIN_GBL_SIZE,
    OTA_CONTROL_UUID,
    OTA_DATA_UUID,
    WINDOW,
    LogCallback,
    ProgressCallback,
    SilabsOTAError,
)

if TYPE_CHECKING:
    from bleak.backends.device import BLEDevice


async def perform_silabs_ota(
    gbl_bytes: bytes,
    ble_device: BLEDevice,
    on_progress: ProgressCallback | None = None,
    on_log: LogCallback | None = None,
    *,
    fast: bool = False,
) -> None:
    """Flash an EFR32 device that is in the Silicon Labs AppLoader.

    Retries the *connection* while the AppLoader boots — no external sleep is
    required before calling. The AppLoader exits back to the application when the
    BLE connection drops, so the full transfer must complete in a single
    connection: we only retry failed connects (which do not arm the reboot) and
    never reconnect once connected.

    Through a Bluetooth proxy, clear the proxy's stale per-MAC GATT cache *before*
    this call — while still connected in app mode — so this connection
    re-discovers the AppLoader's OTA service instead of a cached app-firmware
    table.

    Args:
        gbl_bytes: Raw ``.gbl`` firmware bytes.
        ble_device: The device in (or booting into) the AppLoader, same address.
        on_progress: Optional callback with float percentage 0–100.
        on_log: Optional callback for human-readable status messages.
        fast: Use the larger write-without-response window (``FAST_WINDOW``) for a
            much faster transfer. Only safe on a **direct** connection (no
            Bluetooth proxy), where the OS socket backpressures write commands.
            Leave ``False`` (every chunk acked) when flashing through an ESPHome
            Bluetooth proxy, which can silently drop write-without-response chunks
            and corrupt the image. Defaults to ``False``.

    Raises:
        SilabsOTAError: ``gbl_bytes`` is not a valid GBL image (bad magic or too
            small — checked before connecting, so the device is left untouched), the
            connection or transfer failed, or the device is not in Silabs OTA mode.
    """
    log: LogCallback = on_log or (lambda _: None)
    file_size = len(gbl_bytes)
    window = FAST_WINDOW if fast else WINDOW

    # Validate the image BEFORE connecting/erasing. AppLoader OTA is single-bank and
    # in-place: writing 0x00 erases the app slot, so a bad image (truncated download,
    # HTML error page, .hex, nRF .zip) would leave the device app-less. Reject it here
    # while the app is still intact.
    if file_size < MIN_GBL_SIZE:
        raise SilabsOTAError(
            f"Firmware is too small to be a valid GBL image ({file_size} bytes; "
            f"expected at least {MIN_GBL_SIZE}). Refusing to erase the device — "
            "check the firmware download."
        )
    if not gbl_bytes.startswith(GBL_MAGIC):
        raise SilabsOTAError(
            "Firmware is not a valid GBL image (missing GBL header magic "
            f"{GBL_MAGIC.hex(' ').upper()}). Refusing to erase the device — the file may "
            "be a truncated download, an error page, or the wrong format (.hex/.zip)."
        )

    # Brief pause to let the device begin booting into the AppLoader before the
    # first connect. establish_connection then retries the connect itself —
    # failed/incomplete connects are harmless because the AppLoader only arms its
    # reboot-on-disconnect after a *successful* connection. So we retry the
    # connect but NEVER reconnect once connected: this is our only chance to flash.
    log("Waiting for AppLoader to boot…")
    await asyncio.sleep(APPLOADER_BOOT_DELAY)

    try:
        # use_services_cache=False forces a fresh GATT discovery rather than
        # reusing a cached (app-firmware) service table for this address.
        client = await establish_connection(
            BleakClientWithServiceCache,
            ble_device,
            ble_device.name or "AppLoader",
            use_services_cache=False,
            max_attempts=CONNECT_ATTEMPTS,
        )
    except Exception as exc:  # noqa: BLE001 - surfaced as SilabsOTAError
        raise SilabsOTAError(f"Could not connect to AppLoader: {exc}") from exc

    try:
        # Identify the OTA service by its control/data characteristics rather than
        # the service UUID, which varies between AppLoader builds.
        char_uuids = {str(c.uuid).lower() for svc in client.services for c in svc.characteristics}
        if OTA_CONTROL_UUID not in char_uuids or OTA_DATA_UUID not in char_uuids:
            raise SilabsOTAError(
                "Device is not in Silabs OTA mode — AppLoader OTA characteristics not found. "
                "Cannot recover on this connection (reconnecting would reboot the device out of "
                "OTA mode); re-trigger the bootloader and retry."
            )

        mode = f"fast (window={window})" if fast else "proxy-safe (every chunk acked)"
        log(f"Connected to AppLoader. Starting OTA transfer — {mode}…")
        await client.write_gatt_char(OTA_CONTROL_UUID, bytearray([0x00]), response=True)

        # Stream the GBL. With window=1 every chunk is write-with-response, which
        # is required over a Bluetooth proxy (write-without-response can be
        # silently dropped, yielding an incomplete image that fails finalize). In
        # fast mode (direct connection only) we send window-1 chunks
        # write-without-response then one acked write. The final chunk is always
        # synced so all data is acked before the 0x03 finalize.
        sent = 0
        index = 0
        while sent < file_size:
            chunk = gbl_bytes[sent : sent + CHUNK_SIZE]
            sent += len(chunk)
            index += 1
            sync = index % window == 0 or sent >= file_size
            # Resend on proxy congestion ("Congested" = TX buffer full): the write
            # was not delivered, so back off briefly and retry the same chunk.
            for congestion_attempt in range(CONGESTION_RETRIES):
                try:
                    await client.write_gatt_char(OTA_DATA_UUID, chunk, response=sync)
                    break
                except Exception as exc:  # noqa: BLE001 - inspect message for congestion
                    if "congest" not in str(exc).lower() or congestion_attempt == CONGESTION_RETRIES - 1:
                        raise
                    await asyncio.sleep(CONGESTION_BACKOFF)
            if on_progress:
                on_progress(sent / file_size * 100)

        log("Stream complete. Finalizing…")
        await client.write_gatt_char(OTA_CONTROL_UUID, bytearray([0x03]), response=True)
        log("OTA complete — device is verifying and rebooting.")

    except SilabsOTAError:
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced as SilabsOTAError
        raise SilabsOTAError(f"Silabs OTA failed: {exc}") from exc
    finally:
        # The device reboots out of the AppLoader on disconnect anyway; disconnect
        # explicitly so we don't leak the connection if the transfer raised.
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass
