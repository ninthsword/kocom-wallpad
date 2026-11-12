"""Exercise serialx through a local PTY and bounded mocked error paths."""

import asyncio
import os
import pty
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.kocom_wallpad import transport as transport_module
from custom_components.kocom_wallpad.transport import AsyncConnection

pytestmark = pytest.mark.asyncio


async def test_native_serial_pty_roundtrip_and_reopen():
    master, slave = pty.openpty()
    os.set_blocking(master, False)
    connection = AsyncConnection(os.ttyname(slave), None)

    async def read_master():
        while True:
            try:
                return os.read(master, 128)
            except BlockingIOError:
                await asyncio.sleep(0.005)

    try:
        for _ in range(2):
            assert await asyncio.wait_for(connection.open(), 2)
            assert await connection.send(b"outbound") == 8
            assert await asyncio.wait_for(read_master(), 2) == b"outbound"
            os.write(master, b"inbound")
            assert await connection.recv(128, timeout=2) == b"inbound"
            assert await connection.recv(128, timeout=0.01) == b""
            assert connection._is_connected()
            await asyncio.wait_for(connection.close(), 2)
            assert connection._reader is None and connection._writer is None
            assert not connection._is_connected()
    finally:
        await asyncio.wait_for(connection.close(), 2)
        os.close(master)
        os.close(slave)


@pytest.mark.parametrize("error", [OSError, RuntimeError, ValueError])
async def test_serial_open_failure_clears_state(error):
    connection = AsyncConnection("/test-only", None)
    with patch("serialx.open_serial_connection", new=AsyncMock(side_effect=error)) as open_serial:
        assert not await connection.open()
    open_serial.assert_awaited_once_with(url="/test-only", baudrate=9600)
    assert connection._reader is None and connection._writer is None
    assert not connection._is_connected()


@pytest.mark.parametrize("operation", ["send", "recv"])
async def test_stream_error_closes_and_allows_reopen(operation):
    writer = Mock()
    writer.drain = AsyncMock(side_effect=OSError)
    writer.wait_closed = AsyncMock(side_effect=RuntimeError)
    reader = Mock()
    reader.read = AsyncMock(side_effect=OSError)
    connection = AsyncConnection("/test-only", None)
    with patch("serialx.open_serial_connection", new=AsyncMock(return_value=(reader, writer))):
        assert await connection.open()
        if operation == "send":
            with pytest.raises(OSError):
                await connection.send(b"test")
        else:
            assert await connection.recv(1) == b""
        writer.close.assert_called_once()
        assert connection._reader is None and connection._writer is None
        assert not connection._is_connected()
        assert await connection.open()
        await connection.close()


@pytest.mark.parametrize("operation", ["send", "close"])
async def test_blocked_stream_is_bounded_and_can_reopen(operation):
    entered = asyncio.Event()

    async def blocked():
        entered.set()
        await asyncio.Event().wait()

    writer = Mock()
    writer.drain = AsyncMock(side_effect=blocked if operation == "send" else None)
    writer.wait_closed = AsyncMock(side_effect=blocked)
    reader = Mock()
    connection = AsyncConnection("/test-only", None)
    with (
        patch("serialx.open_serial_connection", new=AsyncMock(return_value=(reader, writer))),
        patch.object(transport_module, "TRANSPORT_SEND_TIMEOUT", 0.01, create=True),
        patch.object(transport_module, "TRANSPORT_CLOSE_TIMEOUT", 0.01, create=True),
    ):
        assert await connection.open()
        task = asyncio.create_task(connection.send(b"test") if operation == "send" else connection.close())
        await asyncio.wait_for(entered.wait(), 0.5)
        if operation == "send":
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(task, 0.5)
        else:
            await asyncio.wait_for(task, 0.5)
        assert connection._reader is None and connection._writer is None
        assert not connection._is_connected()
        writer.close.assert_called_once()
        writer.wait_closed = AsyncMock()
        assert await connection.open()
        await connection.close()
