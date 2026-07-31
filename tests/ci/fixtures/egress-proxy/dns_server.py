from __future__ import annotations

import ipaddress
import socketserver
import struct
from collections import defaultdict


SAFE_IPV4 = ipaddress.ip_address("11.240.0.80").packed
PRIVATE_IPV4 = ipaddress.ip_address("169.254.169.254").packed
LOOPBACK_IPV6 = ipaddress.ip_address("::1").packed
query_counts: dict[tuple[str, int], int] = defaultdict(int)


def _read_name(message: bytes, offset: int) -> tuple[str, int]:
    labels: list[str] = []
    while True:
        length = message[offset]
        offset += 1
        if length == 0:
            break
        labels.append(message[offset : offset + length].decode("ascii"))
        offset += length
    return ".".join(labels).lower(), offset


def _encode_name(name: str) -> bytes:
    return (
        b"".join(
            bytes((len(label),)) + label.encode("ascii") for label in name.split(".")
        )
        + b"\x00"
    )


def _record(name: bytes, record_type: int, value: bytes) -> bytes:
    return name + struct.pack("!HHIH", record_type, 1, 1, len(value)) + value


def _answers(name: str, record_type: int) -> list[bytes]:
    key = (name, record_type)
    query_counts[key] += 1
    owner = b"\xc0\x0c"

    if record_type == 1 and name == "safe.test":
        return [_record(owner, 1, SAFE_IPV4)]
    if record_type == 1 and name == "unsafe.test":
        return [_record(owner, 1, PRIVATE_IPV4)]
    if record_type == 1 and name == "mixed.test":
        return [
            _record(owner, 1, SAFE_IPV4),
            _record(owner, 1, PRIVATE_IPV4),
        ]
    if name == "mixed-aaaa.test":
        if record_type == 1:
            return [_record(owner, 1, SAFE_IPV4)]
        if record_type == 28:
            return [_record(owner, 28, LOOPBACK_IPV6)]
    if record_type == 1 and name == "cname-unsafe.test":
        target = _encode_name("unsafe.test")
        return [
            _record(owner, 5, target),
            _record(target, 1, PRIVATE_IPV4),
        ]
    if record_type == 1 and name == "rebind.test":
        address = SAFE_IPV4 if query_counts[key] == 1 else PRIVATE_IPV4
        return [_record(owner, 1, address)]
    return []


class DnsHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        message, server = self.request
        try:
            name, offset = _read_name(message, 12)
            record_type, _record_class = struct.unpack(
                "!HH", message[offset : offset + 4]
            )
            question = message[12 : offset + 4]
            answers = _answers(name, record_type)
            header = struct.pack(
                "!HHHHHH",
                struct.unpack("!H", message[:2])[0],
                0x8180,
                1,
                len(answers),
                0,
                0,
            )
            server.sendto(header + question + b"".join(answers), self.client_address)
        except (IndexError, UnicodeError, struct.error):
            return


if __name__ == "__main__":
    with socketserver.ThreadingUDPServer(("0.0.0.0", 53), DnsHandler) as server:
        server.serve_forever()
