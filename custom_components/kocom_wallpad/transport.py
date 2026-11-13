"""Transport for Kocom Wallpad."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import serialx

from .const import LOGGER, TRANSPORT_CLOSE_TIMEOUT, TRANSPORT_SEND_TIMEOUT


@dataclass
class AsyncConnection:
    """Async Connection."""
    host: str
    port: int | None
    serial_baud: int = 9600
    connect_timeout: float = 5.0
    reconnect_backoff: tuple[float, float] = (1.0, 30.0)  # min, max seconds

    def __post_init__(self) -> None:
        """Initialize the connection."""
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._last_activity_mono: float = time.monotonic()
        self._last_reconn_delay: float = 0.0
        self._connected = False

    async def open(self) -> bool:
        """Make one connection attempt.

        Reconnect scheduling deliberately belongs to ``KocomGateway`` so a
        failed startup cannot recurse through ``open -> reconnect -> open``.
        """
        try:
            if self.port is None:
                reader, writer = await asyncio.wait_for(
                    serialx.open_serial_connection(url=self.host, baudrate=self.serial_baud),
                    timeout=self.connect_timeout,
                )
                LOGGER.info("시리얼 연결을 열었습니다: %s", self.host)
            else:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port),
                    timeout=self.connect_timeout,
                )
                LOGGER.info("소켓 연결을 열었습니다: %s:%s", self.host, self.port)
            self._reader, self._writer = reader, writer
            self._connected = True
            self._touch()
            return True
        except asyncio.CancelledError:
            self._connected = False
            self._reader = None
            self._writer = None
            raise
        except (OSError, RuntimeError, ValueError) as e:
            LOGGER.warning("연결을 열지 못했습니다: %r", e)
            self._connected = False
            self._reader = None
            self._writer = None
            return False

    async def close(self) -> None:
        writer = self._writer
        self._writer = None
        self._reader = None
        self._connected = False
        if writer is not None:
            await self._close_writer(writer)

    async def _close_writer(self, writer: asyncio.StreamWriter) -> None:
        """Close the captured stream without touching a replacement connection."""
        try:
            writer.close()
            await asyncio.wait_for(writer.wait_closed(), timeout=TRANSPORT_CLOSE_TIMEOUT)
        except (OSError, RuntimeError, TimeoutError) as err:
            LOGGER.debug("연결 종료를 완료하지 못했습니다: %r", err)

    def _is_connected(self) -> bool:
        return self._connected

    def _touch(self) -> None:
        self._last_activity_mono = time.monotonic()

    def idle_since(self) -> float:
        return max(0.0, time.monotonic() - self._last_activity_mono)

    async def send(self, data: bytes) -> int:
        writer = self._writer
        if writer is None:
            raise RuntimeError("연결이 열려 있지 않습니다")
        try:
            LOGGER.debug("송신합니다: %s", data.hex())
            writer.write(data)
            await asyncio.wait_for(writer.drain(), timeout=TRANSPORT_SEND_TIMEOUT)
            self._touch()
            return len(data)
        except asyncio.CancelledError:
            if self._writer is writer:
                self._writer = None
                self._reader = None
                self._connected = False
                try:
                    writer.close()
                except (OSError, RuntimeError, TimeoutError):
                    pass
            raise
        except (OSError, RuntimeError, TimeoutError) as e:
            LOGGER.warning("송신에 실패했습니다: %r", e)
            if self._writer is writer:
                self._writer = None
                self._reader = None
                self._connected = False
            await self._close_writer(writer)
            raise

    async def recv(self, nbytes: int, timeout: float = 0.05) -> bytes:
        if not self._reader:
            raise RuntimeError("연결이 열려 있지 않습니다")
        try:
            chunk = await asyncio.wait_for(self._reader.read(nbytes), timeout=timeout)
        except asyncio.TimeoutError:
            return b""
        except (OSError, RuntimeError) as e:
            LOGGER.warning("수신에 실패했습니다: %r", e)
            await self.close()
            return b""
        if chunk:
            self._touch()
        elif self.port is not None:
            # StreamReader.read() returns b"" only for an orderly TCP EOF.
            # A timeout is handled above, while serial's empty-read behavior
            # remains unchanged.
            LOGGER.info("TCP 상대가 연결을 종료했습니다")
            await self.close()
        return chunk
