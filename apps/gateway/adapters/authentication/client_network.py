from __future__ import annotations

import ipaddress
from collections.abc import Iterable

from fastapi import Request


class ClientNetworkResolver:
    def __init__(self, trusted_proxy_cidrs: Iterable[str] = ()) -> None:
        self._trusted_proxies = tuple(
            ipaddress.ip_network(value, strict=True) for value in trusted_proxy_cidrs
        )

    def resolve(self, request: Request) -> str:
        peer = self._parse_address(request.client.host if request.client else None)
        if peer is None:
            return "unknown"

        candidate = peer
        if self._is_trusted(peer):
            forwarded = request.headers.getlist("x-forwarded-for")
            real_ip = request.headers.getlist("x-real-ip")
            if len(forwarded) > 1 or len(real_ip) > 1:
                return self._network_identity(peer)
            if forwarded:
                chain = self._parse_forwarded_chain(forwarded[0])
                if chain is None:
                    return self._network_identity(peer)
                candidate = self._first_untrusted_from_right(chain)
            elif real_ip:
                parsed_real_ip = self._parse_address(real_ip[0].strip())
                if parsed_real_ip is None:
                    return self._network_identity(peer)
                candidate = parsed_real_ip

        return self._network_identity(candidate)

    def _first_untrusted_from_right(
        self,
        chain: tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...],
    ) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
        for address in reversed(chain):
            if not self._is_trusted(address):
                return address
        return chain[0]

    def _parse_forwarded_chain(
        self,
        value: str,
    ) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...] | None:
        raw_items = value.split(",")
        if not raw_items or any(not item.strip() for item in raw_items):
            return None
        parsed = tuple(self._parse_address(item.strip()) for item in raw_items)
        if any(item is None for item in parsed):
            return None
        return parsed  # type: ignore[return-value]

    @staticmethod
    def _parse_address(
        value: str | None,
    ) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
        if not value:
            return None
        try:
            return ipaddress.ip_address(value)
        except ValueError:
            return None

    def _is_trusted(
        self,
        address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    ) -> bool:
        return any(
            address.version == network.version and address in network
            for network in self._trusted_proxies
        )

    @staticmethod
    def _network_identity(
        address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    ) -> str:
        if address.is_loopback:
            return str(address)
        prefix = 24 if address.version == 4 else 64
        return str(ipaddress.ip_network(f"{address}/{prefix}", strict=False))
