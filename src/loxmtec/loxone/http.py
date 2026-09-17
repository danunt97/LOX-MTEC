"""Push values into a Loxone 'Virtueller Eingang' via the Miniserver web API.

The Miniserver accepts ``GET /dev/sps/io/<name>/<value>``.  Gen1 Miniservers use
basic auth, Gen2 usually answers with digest, so the transport starts with basic
and transparently switches once it sees a 401.
"""

from __future__ import annotations

import logging
import threading
from urllib.parse import quote

import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

from loxmtec.loxone.base import Payload, SendResult, Transport

logger = logging.getLogger(__name__)

MAX_REPORTED_ERRORS = 3


class HttpTransport(Transport):
    name = "http"

    def __init__(
        self,
        host: str,
        port: int = 80,
        scheme: str = "http",
        user: str = "",
        password: str = "",
        timeout: int = 5,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.scheme = scheme if scheme in ("http", "https") else "http"
        self.user = user
        self.password = password
        self.timeout = timeout
        self._session: requests.Session | None = None
        self._digest = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    @property
    def base_url(self) -> str:
        default_port = 80 if self.scheme == "http" else 443
        host = self.host
        if self.port and self.port != default_port:
            host = f"{host}:{self.port}"
        return f"{self.scheme}://{host}"

    def _auth(self):
        if not self.user:
            return None
        if self._digest:
            return HTTPDigestAuth(self.user, self.password)
        return HTTPBasicAuth(self.user, self.password)

    def _get_session(self) -> requests.Session:
        if self._session is None:
            session = requests.Session()
            session.trust_env = False  # never route Miniserver calls via a proxy
            if self.scheme == "https":
                # Miniservers ship self-signed certificates.
                session.verify = False
                try:
                    import urllib3

                    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                except Exception:  # pragma: no cover - warning suppression is optional
                    pass
            self._session = session
        return self._session

    # ------------------------------------------------------------------
    def send(self, payloads: list[Payload]) -> SendResult:
        result = SendResult()
        if not payloads:
            return result
        if not self.host:
            result.failed = len(payloads)
            result.errors.append("Keine Miniserver-Adresse konfiguriert")
            return result

        with self._lock:
            session = self._get_session()
            for payload in payloads:
                url = (
                    f"{self.base_url}/dev/sps/io/"
                    f"{quote(payload.target, safe='')}/{quote(payload.text, safe='')}"
                )
                try:
                    response = session.get(url, auth=self._auth(), timeout=self.timeout)
                    if response.status_code == 401 and self.user and not self._digest:
                        logger.info("Miniserver requested digest auth - switching")
                        self._digest = True
                        response = session.get(url, auth=self._auth(), timeout=self.timeout)
                    if response.status_code >= 400:
                        result.failed += 1
                        if len(result.errors) < MAX_REPORTED_ERRORS:
                            result.errors.append(
                                f"HTTP {response.status_code} für '{payload.target}'"
                            )
                        continue
                    result.sent.append(payload)
                    logger.debug("HTTP -> %s = %s", payload.target, payload.text)
                except requests.RequestException as err:
                    result.failed += 1
                    if len(result.errors) < MAX_REPORTED_ERRORS:
                        result.errors.append(f"HTTP-Fehler für '{payload.target}': {err}")
        return result

    def describe(self) -> str:
        return f"HTTP an {self.base_url}/dev/sps/io/"

    def close(self) -> None:
        if self._session is not None:
            try:
                self._session.close()
            except Exception:  # pragma: no cover - best effort
                pass
            self._session = None
