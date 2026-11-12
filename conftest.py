"""Ordinary tests are offline and never inherit real provider credentials."""

import ipaddress
import os
import socket

import httpx
import pytest


@pytest.fixture(autouse=True)
def isolated_network_and_settings(monkeypatch):
    for name in os.environ:
        if name.startswith("BB_LLM_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BB_ENVIRONMENT", "test")
    # Explicit blanks also override a user's .env when Settings() is constructed.
    for name in ("BASE_URL", "API_KEY", "MODEL", "ACCESS_TOKEN"):
        monkeypatch.setenv("BB_LLM_" + name, "")
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_resolve = socket.getaddrinfo
    original_sendto = socket.socket.sendto

    def local_host(host):
        if host in {"localhost", b"localhost"}:
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    def check(address):
        if isinstance(address, tuple) and not local_host(address[0]):
            raise AssertionError("Внешняя сеть запрещена в обычных тестах; используйте подменённый transport")

    def connect(sock, address):
        check(address)
        return original_connect(sock, address)

    def connect_ex(sock, address):
        check(address)
        return original_connect_ex(sock, address)

    def resolve(host, *args, **kwargs):
        if host is not None and not local_host(host):
            raise AssertionError("Внешний DNS запрещён в обычных тестах")
        return original_resolve(host, *args, **kwargs)

    def sendto(sock, data, *args):
        check(args[-1])
        return original_sendto(sock, data, *args)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(socket.socket, "sendto", sendto)

    # MockTransport and ASGI/TestClient transports remain available; a real
    # provider transport is forbidden even when it points to loopback/proxy.
    def no_http(*_args, **_kwargs):
        raise AssertionError("Настоящий HTTP transport запрещён в обычных тестах")

    async def no_async_http(*_args, **_kwargs):
        raise AssertionError("Настоящий HTTP transport запрещён в обычных тестах")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", no_http)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", no_async_http)
    if hasattr(socket.socket, "sendmsg"):
        original_sendmsg = socket.socket.sendmsg

        def sendmsg(sock, buffers, ancdata=(), flags=0, address=None):
            if address is not None:
                check(address)
                return original_sendmsg(sock, buffers, ancdata, flags, address)
            return original_sendmsg(sock, buffers, ancdata, flags)

        monkeypatch.setattr(socket.socket, "sendmsg", sendmsg)
