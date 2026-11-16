"""
Live UART capture of a phone's boot log through a USB-to-serial adapter (CH340, CP210x, FTDI, PL2303).

Read only: the program never writes to the port, so only GND and the adapter's RX (to the phone's TX) need to
be connected; the adapter's TX stays unconnected. Many phones use 1.8 V logic on their UART pads: a 3.3 V
adapter's TX can damage them, and a 3.3 V adapter may not read 1.8 V levels reliably.

Every byte received is written to the capture file unchanged; the text is decoded only for display. After the
capture the file can be checked with core.log_analyzer like any saved log.
"""
import codecs
import os
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional

from app_paths import default_uart_dir, safe_name
from core.result import M, Msg

DEFAULT_BAUD = 115200
BAUD_RATES = (115200, 921600, 460800, 230400, 57600, 38400, 19200, 9600)
MAX_CAPTURE_BYTES = 200 * 1024 * 1024
READ_CHUNK = 4096

# USB vendor:product ids of common USB-to-serial chips
KNOWN_ADAPTERS = {
    (0x1A86, 0x7523): "CH340", (0x1A86, 0x5523): "CH341", (0x1A86, 0x55D4): "CH9102",
    (0x10C4, 0xEA60): "CP210x",
    (0x0403, 0x6001): "FTDI FT232R", (0x0403, 0x6015): "FTDI FT231X", (0x0403, 0x6010): "FTDI FT2232",
    (0x0403, 0x6014): "FTDI FT232H",
    (0x067B, 0x2303): "Prolific PL2303",
}


class UartError(Exception):
    def __init__(self, msg: Msg):
        super().__init__(str(msg))
        self.msg = msg


@dataclass
class SerialPortInfo:
    device: str
    description: str
    vid: Optional[int]
    pid: Optional[int]
    adapter: Optional[str]          # the chip, when its USB id is known


@dataclass
class CaptureResult:
    path: str
    bytes_received: int
    seconds: float
    error: Optional[Msg] = None     # why the capture ended early (unplugged, size limit), None when stopped normally


def list_ports() -> List[SerialPortInfo]:
    from serial.tools import list_ports as lp
    ports = []
    for p in lp.comports():
        vid, pid = getattr(p, "vid", None), getattr(p, "pid", None)
        ports.append(SerialPortInfo(device=p.device, description=p.description or "", vid=vid, pid=pid,
                                    adapter=KNOWN_ADAPTERS.get((vid, pid)) if vid is not None else None))
    return ports


def open_serial(port: str, baud: int):
    """Open a port read-only in practice (8N1, no flow control); "loop://" works too (pyserial URL)."""
    import serial
    return serial.serial_for_url(port, baudrate=baud, bytesize=8, parity="N", stopbits=1,
                                 timeout=0.2, xonxoff=False, rtscts=False, dsrdtr=False)


def new_capture_path(port: str) -> str:
    name = safe_name(port.replace("\\\\.\\", "").replace("\\", "_"))
    return os.path.join(default_uart_dir(), f"uart_{name}_{time.strftime('%Y%m%d_%H%M%S')}.log")


class UartCapture:
    """Reads a serial port on a worker thread into a file until stop() (or until the port fails)."""

    def __init__(self, port: str, baud: int = DEFAULT_BAUD, out_path: Optional[str] = None,
                 on_text: Optional[Callable[[str], None]] = None, open_port: Optional[Callable[[], object]] = None,
                 max_bytes: int = MAX_CAPTURE_BYTES):
        self.port, self.baud = port, baud
        self.out_path = out_path or new_capture_path(port)
        self.on_text = on_text
        self._open_port = open_port or (lambda: open_serial(port, baud))
        self.max_bytes = max_bytes
        self.bytes_received = 0
        self.error: Optional[Msg] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._serial = None
        self._started_at = 0.0

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        try:
            self._serial = self._open_port()
        except Exception as e:      # serial.SerialException, ValueError (bad baud), OSError
            raise UartError(M("uart.open_failed", port=self.port, error=str(e)))
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.out_path)), exist_ok=True)
            self._file = open(self.out_path, "wb")
        except OSError as e:        # folder not writable, path blocked: give the port back before failing
            try:
                self._serial.close()
            except Exception:
                pass
            raise UartError(M("uart.save_failed", path=self.out_path, error=str(e)))
        self._started_at = time.monotonic()
        self._thread = threading.Thread(target=self._run, name="uart-capture", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            while not self._stop.is_set():
                try:
                    data = self._serial.read(READ_CHUNK)
                except Exception as e:   # adapter unplugged, driver error
                    self.error = M("uart.read_failed", port=self.port, error=str(e))
                    break
                if not data:
                    continue
                room = self.max_bytes - self.bytes_received
                if len(data) > room:
                    data = data[:room]
                self._file.write(data)
                self._file.flush()
                self.bytes_received += len(data)
                if self.on_text:
                    text = decoder.decode(data)
                    if text:
                        self.on_text(text)
                if self.bytes_received >= self.max_bytes:
                    self.error = M("uart.size_limit", mb=self.max_bytes // (1024 * 1024))
                    break
        finally:
            try:
                self._serial.close()
            except Exception:
                pass
            self._file.close()

    def stop(self, timeout: float = 5.0) -> CaptureResult:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
        return CaptureResult(path=self.out_path, bytes_received=self.bytes_received,
                             seconds=round(time.monotonic() - self._started_at, 1), error=self.error)
