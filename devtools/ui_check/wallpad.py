"""A synthetic Kocom wallpad: a TCP server on 127.0.0.1 that sends the repository's fixture frames.

The frames are the decoder fixtures of tests/test_control_path_safety.py, byte for byte: two
captured thermostat status reports and the synthetic reports of its _REPORTS table. No frame is
invented here. Every connection receives all of them, then again every RESEND seconds, so the
integration sees current reports; whatever the integration sends is only recorded.
"""

from __future__ import annotations

import socket
import threading
import time

RESEND = 5.0
DRAIN = 5.0  # how long stop() keeps reading a connection that has not ended
FRAMES = {
    "thermostat_room1": "aa5530bc00010036010010001500140000005d0d0d",
    "thermostat_living": "aa5530bc00010036000010001500140000005c0d0d",
    "ac_on": "aa5530bc000100390200100003001a1600006b0d0d",
    "vent_on": "aa5530bc0001004800001102800007230200f40d0d",
    "elevator_arrival": "aa5530bc0001004400000331320000000000970d0d",
    "motion_on": "aa5530bc0001006000040000000000000000510d0d",
    "airquality_status": "aa5530bc00010098010023110258012c19328c0d0d",
}


class Wallpad:
    """Serve FRAMES to every client until stop(); count connections and received bytes."""

    def __init__(self) -> None:
        self._server = socket.create_server(("127.0.0.1", 0))
        self.port = int(self._server.getsockname()[1])
        self._stopped = threading.Event()
        self._lock = threading.Lock()
        self.connections = 0
        self.received = bytearray()
        self._acceptor: threading.Thread | None = None
        self._serving: list[threading.Thread] = []

    def start(self) -> None:
        self._acceptor = threading.Thread(target=self._accept, name="wallpad-accept", daemon=True)
        self._acceptor.start()

    def stop(self) -> None:
        """Stop accepting and resending, then wait until every connection has been read to its end.

        Home Assistant is reaped before this is called, so each of its connections already ends in
        EOF; taking the connections still waiting to be accepted and reading every connection on until
        then records every byte it wrote, also those sent just before it exited.
        """
        self._stopped.set()
        if self._acceptor is None:
            self._server.close()
        else:
            self._acceptor.join(timeout=10)
        for thread in list(self._serving):
            thread.join(timeout=DRAIN + 5)
        if (self._acceptor is not None and self._acceptor.is_alive()) or any(
            thread.is_alive() for thread in self._serving
        ):
            raise RuntimeError("the synthetic wallpad did not finish reading its connections")

    def _accept(self) -> None:
        with self._server:
            self._server.settimeout(0.5)
            while True:
                if self._stopped.is_set():
                    self._server.setblocking(False)  # take only the connections already waiting
                try:
                    client, _ = self._server.accept()
                except TimeoutError:
                    continue
                except OSError:  # BlockingIOError once the waiting connections are taken
                    return
                with self._lock:
                    self.connections += 1
                thread = threading.Thread(target=self._serve, args=(client,), daemon=True)
                thread.start()
                self._serving.append(thread)

    def _serve(self, client: socket.socket) -> None:
        """Send FRAMES now and every RESEND seconds until stopped; read until EOF and record it all."""
        payload = b"".join(bytes.fromhex(frame) for frame in FRAMES.values())
        next_send = 0.0
        sending = True
        deadline: float | None = None
        with client:
            while True:
                now = time.monotonic()
                if self._stopped.is_set():
                    sending = False
                    deadline = deadline or now + DRAIN
                    if now >= deadline:
                        return
                if sending and now >= next_send:
                    try:
                        client.sendall(payload)
                    except OSError:
                        sending = False  # the peer is gone; what it sent is still read below
                    next_send = now + RESEND
                client.settimeout(0.05 if not sending else min(0.5, max(0.05, next_send - now)))
                try:
                    data = client.recv(4096)
                except TimeoutError:
                    continue
                except OSError:
                    return
                if not data:
                    return
                with self._lock:
                    self.received.extend(data)
