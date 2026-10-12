#!/usr/bin/env python3
"""Talk to a USB TSC TE344 (TSPL2) and print one 5 mm x 5 mm label."""

import sys
import time

import usb.core
import usb.util

VID = 0x1203
PID = 0x0273
STATUS = {
    0x00: "normal",
    0x01: "head opened",
    0x02: "paper jam",
    0x03: "paper jam and head opened",
    0x04: "out of paper",
    0x05: "out of paper and head opened",
    0x08: "out of ribbon",
    0x09: "out of ribbon and head opened",
    0x0A: "out of ribbon and paper jam",
    0x0B: "out of ribbon, paper jam and head opened",
    0x0C: "out of ribbon and out of paper",
    0x0D: "out of ribbon, out of paper and head opened",
    0x10: "pause",
    0x20: "printing",
    0x80: "other error",
}


def open_printer():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        sys.exit("TE344 not found (vid:pid 1203:0273)")
    try:
        if dev.is_kernel_driver_active(0):
            dev.detach_kernel_driver(0)
    except (NotImplementedError, usb.core.USBError):
        pass
    dev.set_configuration()
    cfg = dev.get_active_configuration()
    intf = cfg[(0, 0)]
    usb.util.claim_interface(dev, intf.bInterfaceNumber)
    ep_out = usb.util.find_descriptor(
        intf,
        custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress)
        == usb.util.ENDPOINT_OUT,
    )
    ep_in = usb.util.find_descriptor(
        intf,
        custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress)
        == usb.util.ENDPOINT_IN,
    )
    if ep_out is None or ep_in is None:
        sys.exit("bulk in/out endpoints not found")
    return dev, intf, ep_out, ep_in


def drain(ep_in, timeout=200):
    chunks = []
    while True:
        try:
            data = bytes(ep_in.read(64, timeout=timeout))
        except usb.core.USBError:
            break
        if not data:
            break
        chunks.append(data)
        timeout = 100
    return b"".join(chunks)


def write(ep_out, payload: bytes):
    sent = ep_out.write(payload, timeout=3000)
    print(f"TX {sent} bytes: {payload!r}")
    return sent


def read_some(ep_in, timeout=1500):
    data = drain(ep_in, timeout=timeout)
    print(f"RX {len(data)} bytes: {data!r}")
    return data


def main():
    dev, intf, ep_out, ep_in = open_printer()
    print(
        f"opened TE344 serial={dev.serial_number!r} "
        f"iface={intf.bInterfaceNumber} "
        f"out=0x{ep_out.bEndpointAddress:02x} in=0x{ep_in.bEndpointAddress:02x}"
    )
    drain(ep_in, timeout=100)

    write(ep_out, b"\x1b!?")
    status = read_some(ep_in, timeout=1500)
    if status:
        code = status[0]
        print(f"status 0x{code:02x}: {STATUS.get(code, 'unknown')}")

    write(ep_out, b"~!I\r\n")
    info = read_some(ep_in, timeout=1500)
    if info:
        print("info:", info.decode("ascii", "replace"))

    write(ep_out, b"~!T\r\n")
    model = read_some(ep_in, timeout=1500)
    if model:
        print("model:", model.decode("ascii", "replace"))

    # 5 mm is 40 dots at 203 dpi and 60 dots at 300 dpi. A 24-dot box fits both.
    job = (
        "SIZE 5 mm, 5 mm\r\n"
        "GAP 2 mm, 0 mm\r\n"
        "DIRECTION 1\r\n"
        "REFERENCE 0,0\r\n"
        "CLS\r\n"
        "BOX 4,4,28,28,2\r\n"
        "PRINT 1\r\n"
    ).encode("ascii")
    write(ep_out, job)
    time.sleep(0.8)
    write(ep_out, b"\x1b!?")
    after = read_some(ep_in, timeout=2000)
    if after:
        code = after[0]
        print(f"after print 0x{code:02x}: {STATUS.get(code, 'unknown')}")

    usb.util.release_interface(dev, intf.bInterfaceNumber)
    usb.util.dispose_resources(dev)


if __name__ == "__main__":
    main()
