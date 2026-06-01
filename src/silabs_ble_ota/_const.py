"""Silicon Labs AppLoader OTA constants, exceptions, and callback types."""

from __future__ import annotations

from collections.abc import Callable

# Identify the OTA service by its control/data characteristics — the OTA *service*
# UUID itself varies between AppLoader builds, but these characteristic UUIDs are
# stable.
OTA_CONTROL_UUID = "f7bf3564-fb6d-4e53-88a4-5e37e0326063"  # write 0x00 = start, 0x03 = finalize
OTA_DATA_UUID = "984227f3-34fc-4045-a5d0-2c581f81a153"  # GBL data sink

CHUNK_SIZE = 244  # bytes per data write
APPLOADER_BOOT_DELAY = 6.0  # seconds to wait before the first connect attempt
CONNECT_ATTEMPTS = 5  # establish_connection retries while the AppLoader boots

# Window of data chunks sent write-without-response before forcing one
# write-with-response (whose ATT ack gates the sender).
#
# Set to 1 = EVERY chunk is write-with-response. Required for reliability over a
# Bluetooth proxy: write-without-response (ATT Write Command) has no delivery
# guarantee — if the device's buffer is briefly full it SILENTLY DROPS the chunk.
# Over a proxy (no backpressure) some chunks get dropped, the stream still
# "completes" (periodic sync writes are acked), but the image is incomplete and
# the 0x03 finalize fails verification ("Application error"). The Silabs AppLoader
# has no packet-receipt mechanism to detect/recover drops, so acknowledge every
# write. (>1 is only safe on a direct connection, where the link layer makes Write
# Commands reliable.)
WINDOW = 1

# Window used in "fast" mode — only safe on a DIRECT connection (no Bluetooth
# proxy), where the OS socket backpressures write-without-response so chunks are
# never silently dropped. Sends FAST_WINDOW-1 chunks write-without-response then
# one acked write; the ATT ack both confirms delivery and paces the sender so the
# AppLoader's receive buffer can drain. Kept modest because the AppLoader still
# flashes each chunk synchronously — too large a burst can overrun its RX buffer.
FAST_WINDOW = 8

# A Bluetooth proxy returns "Congested" when its BLE TX buffer fills under the
# write burst. It means "slow down", not failure — back off and resend the chunk.
CONGESTION_RETRIES = 6
CONGESTION_BACKOFF = 0.15  # seconds, to let the proxy queue drain

# Callback types
ProgressCallback = Callable[[float], None]
LogCallback = Callable[[str], None]


class SilabsOTAError(Exception):
    """Raised when the Silabs AppLoader OTA cannot complete."""
