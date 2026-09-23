"""Small HTTPS client. No implicit retries or credential-bearing redirects."""
import json
from http.client import HTTPException
import os
from pathlib import Path
import stat
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener


class CLIError(Exception):
    pass


def key_file():
    return Path(os.environ.get('NEOBUND_API_KEY_FILE',
                               str(Path.home()/'.config/neobund/api-key'))).expanduser()


def load_api_key():
    if os.environ.get('NEOBUND_API_KEY'):
        return os.environ['NEOBUND_API_KEY']
    path = key_file()
    try:
        meta = path.stat()
    except FileNotFoundError:
        return ''
    if not stat.S_ISREG(meta.st_mode) or meta.st_mode & 0o077:
        raise CLIError('API Key 文件必须是仅当前用户可读写的普通文件（chmod 600）。')
    return path.read_text(encoding='utf-8').strip()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def https_url(value):
    try:
        u = urlsplit(value)
        if u.scheme != 'https' or not u.hostname or u.username or u.password or u.fragment:
            raise ValueError()
        _ = u.port
    except ValueError:
        raise CLIError('必须使用不含用户名、密码或片段的 HTTPS 地址。') from None
    return u


class Client:
    def __init__(self, api_key=None, base_url=None, timeout=60):
        self.api_key = api_key if api_key is not None else load_api_key()
        if not self.api_key or self.api_key != self.api_key.strip() or any(c in self.api_key for c in '\r\n'):
            raise CLIError('请在运行环境中配置有效的 NEOBUND_API_KEY；不要把密钥写入命令参数。')
        self.base_url = (base_url or os.environ.get('NEOBUND_BASE_URL') or 'https://open.neobund.ai').rstrip('/')
        u = https_url(self.base_url)
        if u.query or u.path:
            raise CLIError('NEOBUND_BASE_URL 必须是 HTTPS 域名，可带端口，不能包含路径或查询参数。')
        self.timeout = timeout
        self.opener = build_opener(NoRedirect())

    def redact(self, message):
        return str(message).replace(self.api_key, '[REDACTED]')

    def _send(self, request):
        try:
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    return response.status, response.read()
            except HTTPError as e:
                try:
                    return e.code, e.read(65536)
                finally:
                    e.close()
        except (URLError, OSError, TimeoutError, HTTPException) as e:
            # Do not echo a URL: S3 signed URLs are also credentials.
            raise CLIError(f'网络请求失败（{type(e).__name__}）；写请求结果可能未知，未自动重试。') from None

    def request(self, method, path, body=None, query=None):
        if not path.startswith('/openapi/v1/') or '?' in path or '#' in path:
            raise CLIError('无效的 API 路径。')
        url = self.base_url + path
        if query:
            values = {k: ('true' if v is True else 'false' if v is False else v)
                      for k, v in query.items() if v is not None}
            url += '?' + urlencode(values)
        headers = {'NB-API-Key': self.api_key, 'Accept': 'application/json'}
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        status, raw = self._send(Request(url, data=data, headers=headers, method=method))
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeError):
            raise CLIError(f'HTTP {status} 返回非 JSON 内容；写请求结果可能未知，未自动重试。') from None
        if (not 200 <= status < 300 or not isinstance(result, dict)
                or type(result.get('status')) is not int or result['status'] != 0):
            message = result.get('msg', '无有效业务状态') if isinstance(result, dict) else '无效响应结构'
            raise CLIError(self.redact(f'HTTP {status} / Neobund: {str(message)[:1000]}'))
        return result

    def put_part(self, url, headers, data):
        https_url(url)
        if not isinstance(headers, dict) or any(k.lower() in ('nb-api-key', 'authorization') for k in headers):
            raise CLIError('无效的 S3 分片请求头。')
        status, _ = self._send(Request(url, data=data, headers=headers, method='PUT'))
        if not 200 <= status < 300:
            raise CLIError(f'S3 分片上传失败：HTTP {status}；未自动重试。')
