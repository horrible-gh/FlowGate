from __future__ import annotations

import ipaddress
import socket
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 5
CONNECT_TIMEOUT_SEC = 5
READ_TIMEOUT_SEC = 15


class UrlImportError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 422):
        self.code = code
        self.status_code = status_code
        super().__init__(message)


def _github_blob_raw_hint(url: str) -> str:
    parsed = urlsplit(url)
    if (parsed.hostname or '').lower() != 'github.com' or '/blob/' not in parsed.path:
        return url
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query['raw'] = '1'
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ''))


def _validate_target(url: str) -> None:
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise UrlImportError('invalid_url', 'URL 형식이 올바르지 않습니다.') from exc
    if parsed.scheme not in {'http', 'https'}:
        raise UrlImportError('unsupported_scheme', 'HTTP(S) URL만 사용할 수 있습니다.')
    if parsed.username is not None or parsed.password is not None:
        raise UrlImportError('userinfo_forbidden', 'URL credential/userinfo는 사용할 수 없습니다.')
    host = parsed.hostname
    if not host:
        raise UrlImportError('invalid_host', 'URL host가 없습니다.')
    try:
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    except ValueError as exc:
        raise UrlImportError('invalid_port', 'URL port가 올바르지 않습니다.') from exc
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise UrlImportError('dns_failed', 'URL host를 확인할 수 없습니다.', 502) from exc
    if not infos:
        raise UrlImportError('dns_failed', 'URL host를 확인할 수 없습니다.', 502)
    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0].split('%', 1)[0])
        except ValueError as exc:
            raise UrlImportError('dns_invalid', 'URL host의 주소를 확인할 수 없습니다.', 502) from exc
        if not address.is_global:
            raise UrlImportError('private_target_forbidden', '내부/로컬 네트워크 주소는 가져올 수 없습니다.')


def fetch_text(url: str) -> dict:
    source_url = (url or '').strip()
    if not source_url:
        raise UrlImportError('url_required', 'URL을 입력해 주세요.')
    current = _github_blob_raw_hint(source_url)
    session = requests.Session()
    session.trust_env = False
    headers = {'User-Agent': 'FlowGate document URL import'}
    try:
        for redirect_no in range(MAX_REDIRECTS + 1):
            _validate_target(current)
            try:
                response = session.get(
                    current,
                    headers=headers,
                    timeout=(CONNECT_TIMEOUT_SEC, READ_TIMEOUT_SEC),
                    allow_redirects=False,
                    stream=True,
                )
            except requests.RequestException as exc:
                raise UrlImportError('fetch_failed', 'URL 내용을 가져오지 못했습니다.', 502) from exc
            with response:
                if 300 <= response.status_code < 400:
                    location = response.headers.get('location')
                    if not location:
                        raise UrlImportError('redirect_invalid', 'redirect 대상 URL이 없습니다.', 502)
                    if redirect_no >= MAX_REDIRECTS:
                        raise UrlImportError('redirect_limit', 'redirect 횟수 제한을 초과했습니다.', 502)
                    current = urljoin(current, location)
                    continue
                if response.status_code < 200 or response.status_code >= 300:
                    raise UrlImportError(
                        'upstream_status',
                        f'URL 응답 상태가 올바르지 않습니다. (HTTP {response.status_code})',
                        502,
                    )
                content_type = (response.headers.get('content-type') or '').split(';', 1)[0].strip().lower()
                if content_type and not (content_type.startswith('text/') or content_type in {'application/json', 'application/xml'}):
                    raise UrlImportError('unsupported_content_type', f'텍스트 문서가 아닌 응답입니다. ({content_type})')
                chunks: list[bytes] = []
                total = 0
                try:
                    for chunk in response.iter_content(chunk_size=65536):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > MAX_RESPONSE_BYTES:
                            raise UrlImportError('response_too_large', 'URL 문서가 허용 크기를 초과했습니다.')
                        chunks.append(chunk)
                except requests.RequestException as exc:
                    raise UrlImportError('read_failed', 'URL 응답을 읽지 못했습니다.', 502) from exc
                raw = b''.join(chunks)
                encoding = response.encoding or 'utf-8'
                try:
                    text = raw.decode(encoding)
                except (LookupError, UnicodeDecodeError) as exc:
                    raise UrlImportError('decode_failed', 'URL 문서를 텍스트로 해석하지 못했습니다.') from exc
                return {
                    'content': text,
                    'source_url': source_url,
                    'final_url': current,
                    'content_type': content_type or None,
                    'size': len(raw),
                }
    finally:
        session.close()
    raise UrlImportError('fetch_failed', 'URL 내용을 가져오지 못했습니다.', 502)
