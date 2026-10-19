"""Extracción OpenGraph con conexiones fijadas a una IP pública validada."""
import hashlib
import http.client
import ipaddress
import re
import socket
import ssl
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

MAX_HTML = 1_048_576
TIMEOUT = 5


def normalize_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise ValueError("Solo se permiten URLs HTTP(S)")
    if parts.username or parts.password or parts.port not in {None, 80, 443}:
        raise ValueError("Credenciales o puerto no permitido")
    host = parts.hostname.encode("idna").decode("ascii").lower()
    if ":" in host:
        host = f"[{host}]"
    if parts.port:
        host += f":{parts.port}"
    return urlunsplit((parts.scheme.lower(), host, parts.path or "/", parts.query, ""))


def url_id(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


def extract_urls(text: str) -> list[str]:
    urls = []
    for raw in re.findall(r"https?://[^\s<>\"']+", text):
        try:
            url = normalize_url(raw.rstrip(".,;:!?)]}"))
            if len(url) <= 2048 and url not in urls:
                urls.append(url)
        except ValueError:
            continue
    return urls[:10]


def public_address(host: str, port: int) -> str:
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses:
        raise ValueError("Sin dirección IP")
    # Rechazar también dominios con respuestas mixtas públicas/privadas.
    for result in addresses:
        ip = ipaddress.ip_address(result[4][0])
        if not ip.is_global or ip.is_multicast or getattr(ip, "ipv4_mapped", None):
            raise ValueError("Destino interno o reservado no permitido")
    return addresses[0][4][0]


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, address: str):
        super().__init__(host, port, timeout=TIMEOUT, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        sock = socket.create_connection((self.address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def fetch_html(url: str) -> tuple[str, str]:
    deadline = time.monotonic() + 20
    for _ in range(4):
        url = normalize_url(url)
        parts = urlsplit(url)
        port = parts.port or (443 if parts.scheme == "https" else 80)
        address = public_address(parts.hostname, port)
        if parts.scheme == "https":
            connection = PinnedHTTPS(parts.hostname, port, address)
        else:
            connection = http.client.HTTPConnection(address, port, timeout=TIMEOUT)
        try:
            path = urlunsplit(("", "", parts.path, parts.query, ""))
            connection.request("GET", path, headers={
                "Host": parts.netloc, "User-Agent": "ChatMedia/1.0",
                "Accept": "text/html", "Accept-Encoding": "identity"})
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location:
                    raise ValueError("Redirección sin destino")
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise ValueError(f"El sitio respondió HTTP {response.status}")
            if "text/html" not in response.getheader("Content-Type", "").lower():
                raise ValueError("El destino no es HTML")
            if response.getheader("Content-Encoding", "identity") != "identity":
                raise ValueError("Codificación no soportada")
            chunks, size = [], 0
            while True:
                if time.monotonic() > deadline:
                    raise TimeoutError("Tiempo máximo de descarga excedido")
                chunk = response.read1(min(65536, MAX_HTML + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_HTML:
                    raise ValueError("HTML demasiado grande")
            charset = response.headers.get_content_charset() or "utf-8"
            return b"".join(chunks).decode(charset, errors="replace"), url
        finally:
            connection.close()
    raise ValueError("Demasiadas redirecciones")


class MetadataParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags = {}
        self.title = []
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            name = attrs.get("property", attrs.get("name", "")).lower()
            self.tags.setdefault(name, attrs.get("content", ""))
        if tag == "title":
            self.in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title.append(data)


def generate_preview(url: str) -> dict:
    html, final_url = fetch_html(url)
    parser = MetadataParser()
    parser.feed(html)
    image = None
    if parser.tags.get("og:image"):
        try:
            image = normalize_url(urljoin(final_url, parser.tags["og:image"]))
            parts = urlsplit(image)
            public_address(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
        except (ValueError, OSError):
            image = None
    return {"url": url, "final_url": final_url,
            "title": (parser.tags.get("og:title") or "".join(parser.title) or final_url)[:500],
            "description": (parser.tags.get("og:description") or parser.tags.get("description", ""))[:2000],
            "image_url": image}
