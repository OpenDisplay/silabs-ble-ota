"""Tests for the Silabs AppLoader OTA transfer."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from silabs_ble_ota import SilabsOTAError, perform_silabs_ota
from silabs_ble_ota._const import (
    APPLOADER_BOOT_DELAY,
    CHUNK_SIZE,
    OTA_CONTROL_UUID,
    OTA_DATA_UUID,
    WINDOW,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(char_uuids: list[str]) -> MagicMock:
    """A connected client (as returned by establish_connection) whose services
    expose the given characteristic UUIDs. write_gatt_char/disconnect are async."""
    char_mocks = [MagicMock(uuid=uuid) for uuid in char_uuids]
    svc = MagicMock()
    svc.characteristics = char_mocks
    client = AsyncMock()
    client.services = [svc]
    return client


def _make_ble_device(address: str = "AA:BB:CC:DD:EE:FF") -> MagicMock:
    dev = MagicMock()
    dev.address = address
    return dev


def _patch_connect(client_or_exc: object) -> object:
    """Patch establish_connection to return a client (or raise)."""
    if isinstance(client_or_exc, BaseException):
        return patch("silabs_ble_ota.ota.establish_connection", new=AsyncMock(side_effect=client_or_exc))
    return patch("silabs_ble_ota.ota.establish_connection", new=AsyncMock(return_value=client_or_exc))


# ---------------------------------------------------------------------------
# perform_silabs_ota
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path() -> None:
    """Full transfer: start write, data chunks, finalize write, disconnect."""
    gbl = bytes(range(256)) * 2  # 512 bytes → 3 chunks of 244/244/24
    client = _make_client([OTA_CONTROL_UUID, OTA_DATA_UUID])
    progress: list[float] = []
    connect = AsyncMock(return_value=client)

    with (
        patch("silabs_ble_ota.ota.asyncio.sleep", new=AsyncMock()),
        patch("silabs_ble_ota.ota.establish_connection", new=connect),
    ):
        await perform_silabs_ota(gbl, _make_ble_device(), on_progress=progress.append)

    # Fresh GATT discovery (not a cached app-firmware table).
    assert connect.await_args.kwargs["use_services_cache"] is False

    calls = client.write_gatt_char.call_args_list
    assert calls[0].args[0] == OTA_CONTROL_UUID and calls[0].args[1] == bytearray([0x00])
    assert calls[0].kwargs["response"] is True

    data_calls = [c for c in calls if c.args[0] == OTA_DATA_UUID]
    assert sum(len(c.args[1]) for c in data_calls) == len(gbl)
    assert all(len(c.args[1]) <= CHUNK_SIZE for c in data_calls)
    # Final chunk is synced so data is acked before finalize (WINDOW=1 → all synced).
    assert data_calls[-1].kwargs["response"] is True

    assert calls[-1].args[0] == OTA_CONTROL_UUID and calls[-1].args[1] == bytearray([0x03])
    assert calls[-1].kwargs["response"] is True

    assert progress[0] > 0 and progress[-1] == pytest.approx(100.0)
    client.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_windowed_flow_control() -> None:
    """Data chunks sync (response=True) every WINDOW chunks and on the last chunk."""
    n_chunks = WINDOW * 2 + 3
    gbl = b"\x00" * (CHUNK_SIZE * n_chunks)
    client = _make_client([OTA_CONTROL_UUID, OTA_DATA_UUID])

    with patch("silabs_ble_ota.ota.asyncio.sleep", new=AsyncMock()), _patch_connect(client):
        await perform_silabs_ota(gbl, _make_ble_device())

    data_calls = [c for c in client.write_gatt_char.call_args_list if c.args[0] == OTA_DATA_UUID]
    assert len(data_calls) == n_chunks
    synced = [i for i, c in enumerate(data_calls, start=1) if c.kwargs["response"] is True]
    expected = sorted(set(range(WINDOW, n_chunks + 1, WINDOW)) | {n_chunks})
    assert synced == expected


@pytest.mark.asyncio
async def test_retries_on_congestion() -> None:
    """A 'Congested' proxy error on a data write is retried, not fatal."""
    client = _make_client([OTA_CONTROL_UUID, OTA_DATA_UUID])
    fails = {"n": 0}

    async def write(uuid, data, response=False):  # noqa: ARG001
        if uuid == OTA_DATA_UUID and fails["n"] < 2:
            fails["n"] += 1
            raise RuntimeError("Bluetooth GATT Error ... description=Congested")

    client.write_gatt_char = AsyncMock(side_effect=write)

    with patch("silabs_ble_ota.ota.asyncio.sleep", new=AsyncMock()), _patch_connect(client):
        await perform_silabs_ota(b"\x00" * (CHUNK_SIZE * 2), _make_ble_device())

    assert fails["n"] == 2  # first data chunk resent twice then succeeded


@pytest.mark.asyncio
async def test_waits_for_apploader_boot() -> None:
    """Waits for the AppLoader to boot before connecting."""
    sleep_mock = AsyncMock()
    client = _make_client([OTA_CONTROL_UUID, OTA_DATA_UUID])

    with patch("silabs_ble_ota.ota.asyncio.sleep", new=sleep_mock), _patch_connect(client):
        await perform_silabs_ota(b"\x00" * 10, _make_ble_device())

    sleep_mock.assert_awaited_once_with(APPLOADER_BOOT_DELAY)


@pytest.mark.asyncio
async def test_missing_control_char_raises() -> None:
    """SilabsOTAError when the OTA control characteristic is absent; connection closed."""
    client = _make_client([OTA_DATA_UUID, "some-other-uuid"])
    with patch("silabs_ble_ota.ota.asyncio.sleep", new=AsyncMock()), _patch_connect(client):
        with pytest.raises(SilabsOTAError, match="not in Silabs OTA mode"):
            await perform_silabs_ota(b"\x00" * 10, _make_ble_device())
    client.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_data_char_raises() -> None:
    """SilabsOTAError when the OTA data characteristic is absent (control present)."""
    client = _make_client([OTA_CONTROL_UUID])
    with patch("silabs_ble_ota.ota.asyncio.sleep", new=AsyncMock()), _patch_connect(client):
        with pytest.raises(SilabsOTAError, match="not in Silabs OTA mode"):
            await perform_silabs_ota(b"\x00" * 10, _make_ble_device())


@pytest.mark.asyncio
async def test_connect_failure_wrapped() -> None:
    """A failed connection is wrapped in SilabsOTAError."""
    with (
        patch("silabs_ble_ota.ota.asyncio.sleep", new=AsyncMock()),
        _patch_connect(RuntimeError("connection refused")),
    ):
        with pytest.raises(SilabsOTAError, match="Could not connect to AppLoader"):
            await perform_silabs_ota(b"\x00" * 10, _make_ble_device())


@pytest.mark.asyncio
async def test_transfer_error_wrapped() -> None:
    """A mid-transfer error is wrapped in SilabsOTAError and the connection closed."""
    client = _make_client([OTA_CONTROL_UUID, OTA_DATA_UUID])
    client.write_gatt_char = AsyncMock(side_effect=RuntimeError("gatt write failed"))
    with patch("silabs_ble_ota.ota.asyncio.sleep", new=AsyncMock()), _patch_connect(client):
        with pytest.raises(SilabsOTAError, match="Silabs OTA failed"):
            await perform_silabs_ota(b"\x00" * 10, _make_ble_device())
    client.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_progress_callback() -> None:
    """Works without an on_progress callback."""
    client = _make_client([OTA_CONTROL_UUID, OTA_DATA_UUID])
    with patch("silabs_ble_ota.ota.asyncio.sleep", new=AsyncMock()), _patch_connect(client):
        await perform_silabs_ota(b"\x00" * 10, _make_ble_device(), on_progress=None)


@pytest.mark.asyncio
async def test_log_callback() -> None:
    """on_log receives status messages during the transfer."""
    client = _make_client([OTA_CONTROL_UUID, OTA_DATA_UUID])
    logs: list[str] = []
    with patch("silabs_ble_ota.ota.asyncio.sleep", new=AsyncMock()), _patch_connect(client):
        await perform_silabs_ota(b"\x00" * 10, _make_ble_device(), on_log=logs.append)
    assert any("AppLoader" in m for m in logs) and any("complete" in m.lower() for m in logs)
