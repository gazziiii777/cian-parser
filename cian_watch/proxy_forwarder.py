import asyncio
import base64
import logging
import time
from http import HTTPStatus
from types import TracebackType
from typing import ClassVar, Self

from cian_watch.exceptions import TunnelRefused
from config.proxy import ProxySettings

logger = logging.getLogger(__name__)


class ProxyForwarder:
    """Локальный HTTP-прокси без пароля перед upstream-прокси с логином и паролем, HTTP или SOCKS5.
    Firefox внутри Camoufox отвечает на 407 от upstream через раз и навигация виснет, а SOCKS5
    с паролем он не умеет вовсе; на локальный прокси без авторизации он ходит стабильно.
    Пропускает только CONNECT: у Циана и Домклика весь трафик HTTPS, обычный HTTP отвечаем 405."""

    CONNECT_TIMEOUT_SECONDS: ClassVar[int] = 15
    UPSTREAM_REPLY_TIMEOUT_SECONDS: ClassVar[int] = 30
    PIPE_CHUNK_SIZE: ClassVar[int] = 65536
    HEADERS_END: ClassVar[bytes] = b"\r\n\r\n"
    REFUSAL_LOG_INTERVAL_SECONDS: ClassVar[float] = 30.0
    SOCKS_VERSION: ClassVar[int] = 5
    SOCKS_AUTH_USERPASS: ClassVar[int] = 2
    SOCKS_AUTH_VERSION: ClassVar[int] = 1
    SOCKS_CMD_CONNECT: ClassVar[int] = 1
    SOCKS_ATYP_IPV4: ClassVar[int] = 1
    SOCKS_ATYP_DOMAIN: ClassVar[int] = 3
    SOCKS_ATYP_IPV6: ClassVar[int] = 4
    SOCKS_REPLY_OK: ClassVar[int] = 0

    def __init__(self, upstream: ProxySettings) -> None:
        self._upstream = upstream
        self._server: asyncio.Server | None = None
        self._port = 0
        self._refusal_logged_at: dict[str, float] = {}

    async def __aenter__(self) -> Self:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self._port = self._server.sockets[0].getsockname()[1]
        logger.info("proxy forwarder listening on %d, upstream %s", self._port, self._upstream.server)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        self._server = None

    @property
    def server_url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Одно соединение браузера: читает CONNECT, открывает тоннель через upstream с подставленной
        авторизацией, отвечает браузеру 200 и дальше гонит байты в обе стороны; отказ upstream
        превращается в 502 для браузера."""
        upstream: tuple[asyncio.StreamReader, asyncio.StreamWriter] | None = None
        try:
            request_line = (await reader.readuntil(self.HEADERS_END)).split(b"\r\n", 1)[0]
            if not request_line.startswith(b"CONNECT "):
                writer.write(self._plain_reply(HTTPStatus.METHOD_NOT_ALLOWED))
                await writer.drain()
                return
            target = request_line.split()[1].decode()
            upstream = await self._open_tunnel(target=target)
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await writer.drain()
            await asyncio.gather(self._pipe(reader, upstream[1]), self._pipe(upstream[0], writer))
        except TunnelRefused as exc:
            self._log_refusal(target=exc.target, status=exc.status)
            writer.write(self._plain_reply(HTTPStatus.BAD_GATEWAY))
            await writer.drain()
        except (asyncio.IncompleteReadError, TimeoutError, OSError) as exc:
            logger.warning("proxy tunnel dropped: %s", exc)
        finally:
            writer.close()
            if upstream is not None:
                upstream[1].close()

    async def _open_tunnel(self, target: str) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        upstream_reader, upstream_writer = await asyncio.wait_for(
            asyncio.open_connection(self._upstream.host, self._upstream.port),
            timeout=self.CONNECT_TIMEOUT_SECONDS,
        )
        try:
            async with asyncio.timeout(self.UPSTREAM_REPLY_TIMEOUT_SECONDS):
                if self._upstream.is_socks5:
                    await self._socks5_connect(upstream_reader, upstream_writer, target=target)
                else:
                    await self._http_connect(upstream_reader, upstream_writer, target=target)
        except BaseException:
            upstream_writer.close()
            raise
        return upstream_reader, upstream_writer

    async def _http_connect(
        self, upstream_reader: asyncio.StreamReader, upstream_writer: asyncio.StreamWriter, target: str
    ) -> None:
        credentials = f"{self._upstream.username}:{self._upstream.password}".encode()
        auth = base64.b64encode(credentials).decode()
        request = f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\nProxy-Authorization: Basic {auth}\r\n\r\n"
        upstream_writer.write(request.encode())
        await upstream_writer.drain()
        reply = await upstream_reader.readuntil(self.HEADERS_END)
        status = reply.split(b" ", 2)[1].decode()
        if status != str(HTTPStatus.OK.value):
            raise TunnelRefused(target=target, status=status)

    async def _socks5_connect(
        self, upstream_reader: asyncio.StreamReader, upstream_writer: asyncio.StreamWriter, target: str
    ) -> None:
        """Рукопожатие SOCKS5 (RFC 1928) с авторизацией по логину и паролю (RFC 1929), адрес
        отдаём доменным именем, чтобы DNS резолвил прокси, а не мы."""
        host, port_text = target.rsplit(":", maxsplit=1)
        upstream_writer.write(bytes([self.SOCKS_VERSION, 1, self.SOCKS_AUTH_USERPASS]))
        await upstream_writer.drain()
        _, method = await upstream_reader.readexactly(2)
        if method != self.SOCKS_AUTH_USERPASS:
            raise TunnelRefused(target=target, status=f"socks auth method {method} not accepted")
        username = self._upstream.username.encode()
        password = self._upstream.password.encode()
        upstream_writer.write(
            bytes([self.SOCKS_AUTH_VERSION, len(username)]) + username + bytes([len(password)]) + password
        )
        await upstream_writer.drain()
        _, auth_status = await upstream_reader.readexactly(2)
        if auth_status != self.SOCKS_REPLY_OK:
            raise TunnelRefused(target=target, status="socks credentials rejected")
        domain = host.encode()
        upstream_writer.write(
            bytes([self.SOCKS_VERSION, self.SOCKS_CMD_CONNECT, 0, self.SOCKS_ATYP_DOMAIN, len(domain)])
            + domain
            + int(port_text).to_bytes(2, "big")
        )
        await upstream_writer.drain()
        _, reply, _, address_type = await upstream_reader.readexactly(4)
        await self._skip_bound_address(upstream_reader, address_type=address_type)
        if reply != self.SOCKS_REPLY_OK:
            raise TunnelRefused(target=target, status=f"socks reply {reply}")

    async def _skip_bound_address(self, upstream_reader: asyncio.StreamReader, address_type: int) -> None:
        if address_type == self.SOCKS_ATYP_IPV4:
            await upstream_reader.readexactly(4 + 2)
        elif address_type == self.SOCKS_ATYP_DOMAIN:
            length = (await upstream_reader.readexactly(1))[0]
            await upstream_reader.readexactly(length + 2)
        elif address_type == self.SOCKS_ATYP_IPV6:
            await upstream_reader.readexactly(16 + 2)

    def _log_refusal(self, target: str, status: str) -> None:
        """Прокси в отказе отвечает на каждый тоннель, страница открывает десятки за секунду:
        пишем по одному предупреждению на хост в полминуты, иначе лог нечитаем."""
        now = time.monotonic()
        host = target.rsplit(":", maxsplit=1)[0]
        if now - self._refusal_logged_at.get(host, 0.0) < self.REFUSAL_LOG_INTERVAL_SECONDS:
            return
        self._refusal_logged_at[host] = now
        logger.warning("upstream proxy refused tunnel to %s: %s", target, status)

    async def _pipe(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while chunk := await reader.read(self.PIPE_CHUNK_SIZE):
                writer.write(chunk)
                await writer.drain()
        except OSError as exc:
            logger.warning("proxy pipe closed early: %s", exc)
        finally:
            writer.close()

    @staticmethod
    def _plain_reply(status: HTTPStatus) -> bytes:
        return f"HTTP/1.1 {status.value} {status.phrase}\r\nConnection: close\r\n\r\n".encode()
