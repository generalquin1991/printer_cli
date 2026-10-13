"""USB transport and printer readiness; job bytes remain opaque here."""

import time

import usb.core
import usb.util


VID = 0x1203
PID = 0x0273
STATUS = {
    0x00: "normal", 0x01: "head opened", 0x02: "paper jam",
    0x03: "paper jam and head opened", 0x04: "out of paper",
    0x05: "out of paper and head opened", 0x08: "out of ribbon",
    0x09: "out of ribbon and head opened", 0x0A: "out of ribbon and paper jam",
    0x0B: "out of ribbon, paper jam and head opened", 0x0C: "out of ribbon and out of paper",
    0x0D: "out of ribbon, out of paper and head opened", 0x10: "pause",
    0x20: "printing", 0x80: "other error",
}


class PrinterError(Exception):
    pass


class USBTransport:
    def __init__(self, device, interface, endpoint_out, endpoint_in):
        self.device = device
        self.interface = interface
        self.endpoint_out = endpoint_out
        self.endpoint_in = endpoint_in

    @classmethod
    def connect(cls):
        device = usb.core.find(idVendor=VID, idProduct=PID)
        if device is None:
            raise PrinterError("TE344 not found (vid:pid 1203:0273)")
        try:
            if device.is_kernel_driver_active(0):
                device.detach_kernel_driver(0)
        except (NotImplementedError, usb.core.USBError):
            pass
        claimed = False
        interface = None
        try:
            device.set_configuration()
            interface = device.get_active_configuration()[(0, 0)]
            usb.util.claim_interface(device, interface.bInterfaceNumber)
            claimed = True
            endpoint_out = usb.util.find_descriptor(
                interface,
                custom_match=lambda endpoint: usb.util.endpoint_direction(
                    endpoint.bEndpointAddress
                ) == usb.util.ENDPOINT_OUT,
            )
            endpoint_in = usb.util.find_descriptor(
                interface,
                custom_match=lambda endpoint: usb.util.endpoint_direction(
                    endpoint.bEndpointAddress
                ) == usb.util.ENDPOINT_IN,
            )
            if endpoint_out is None or endpoint_in is None:
                raise PrinterError("bulk in/out endpoints not found")
            return cls(device, interface, endpoint_out, endpoint_in)
        except Exception as exc:
            if claimed and interface is not None:
                try:
                    usb.util.release_interface(device, interface.bInterfaceNumber)
                except usb.core.USBError:
                    pass
            usb.util.dispose_resources(device)
            if isinstance(exc, PrinterError):
                raise
            raise PrinterError(f"USB connection failed: {exc}") from exc

    def write(self, payload):
        try:
            sent = self.endpoint_out.write(payload, timeout=3000)
        except usb.core.USBError as exc:
            raise PrinterError(f"USB write failed: {exc}") from exc
        if sent != len(payload):
            raise PrinterError(f"incomplete USB transfer: sent {sent} of {len(payload)} bytes")
        return sent

    def read_available(self, timeout=200):
        chunks = []
        while True:
            try:
                data = bytes(self.endpoint_in.read(64, timeout=timeout))
            except usb.core.USBError:
                break
            if not data:
                break
            chunks.append(data)
            timeout = 80
        return b"".join(chunks)

    def close(self):
        if self.device is None:
            return
        try:
            usb.util.release_interface(self.device, self.interface.bInterfaceNumber)
        finally:
            usb.util.dispose_resources(self.device)
            self.device = None


class PrinterExecutor:
    def __init__(self, transport, expected_model="TE344"):
        self.transport = transport
        self.expected_model = expected_model

    @classmethod
    def connect(cls, expected_model="TE344"):
        return cls(USBTransport.connect(), expected_model)

    def close(self):
        self.transport.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def status(self):
        self.transport.write(b"\x1b!?")
        data = self.transport.read_available(timeout=1500)
        if not data:
            return None, "no reply"
        code = data[0]
        return code, STATUS.get(code, f"unknown 0x{code:02x}")

    def prepare(self):
        self.transport.read_available(timeout=100)
        self.transport.write(b"~!T\r\n")
        model = self.transport.read_available(timeout=1500).strip()
        if model != self.expected_model.encode("ascii"):
            raise PrinterError(
                f"Printer did not answer model {self.expected_model}; received {model!r}"
            )
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            code, name = self.status()
            if code == 0x00:
                return
            if code not in (None, 0x20):
                raise PrinterError(f"printer not ready: {name}")
            time.sleep(0.25)
        raise PrinterError("printer did not become ready")

    def execute(self, payload, observe_cycle=True, timeout=10, settle_seconds=0):
        if not isinstance(payload, bytes) or not payload:
            raise ValueError("job payload must be non-empty bytes")
        self.transport.write(payload)
        if observe_cycle:
            return self._wait_cycle(timeout)
        time.sleep(max(0, settle_seconds))
        code, name = self.status()
        if code == 0x20:
            deadline = time.monotonic() + max(1, timeout)
            while code == 0x20 and time.monotonic() < deadline:
                time.sleep(0.25)
                code, name = self.status()
        if code is None:
            raise PrinterError("job was sent but printer returned no status")
        if code != 0x00:
            raise PrinterError(f"printer did not return to ready: {name}")
        return {"status_code": code, "status": name, "cycle_observed": False}

    def _wait_cycle(self, timeout):
        deadline = time.monotonic() + timeout
        saw_motion = False
        last = None
        while time.monotonic() < deadline:
            code, name = self.status()
            if code == 0x20:
                saw_motion = True
            elif code == 0x00 and saw_motion:
                return {"status_code": code, "status": name, "cycle_observed": True}
            elif code not in (None, 0x00):
                raise PrinterError(f"printer error: {name}")
            last = (code, name)
            time.sleep(0.05)
        if not saw_motion:
            raise PrinterError("no busy status observed; output may have occurred, do not retry blindly")
        raise PrinterError(f"printer did not return to ready: {last[1] if last else 'timeout'}")
