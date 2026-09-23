"""Bounded-memory multipart uploads using only returned S3 signed headers."""
import mimetypes
from pathlib import Path
from client import CLIError

MAX_SIZE = 524288000


def file_info(path, cover=False):
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise CLIError(f'文件不存在：{path}')
    size = path.stat().st_size
    if not 1 <= size <= MAX_SIZE:
        raise CLIError('文件必须非空且不超过 500 MiB。')
    mime = mimetypes.guess_type(path.name)[0] or ''
    if not mime.startswith('image/' if cover else 'video/'):
        raise CLIError('请使用具有正确扩展名的视频文件或封面图片。')
    return path, {'type': 2, 'fileName': path.name, 'contentType': mime,
                  'fileSize': size, 'cover': cover}


def upload_file(client, path, cover=False, cover_key=None):
    path, info = file_info(path, cover)
    original = path.stat()
    response = client.request('POST', '/openapi/v1/files/upload-url', info)
    upload = response.get('data')
    if not isinstance(upload, dict):
        raise CLIError('上传初始化返回无效响应，未重试。')
    try:
        parts, part_size = upload['parts'], upload['partSize']
        if (not isinstance(parts, list) or not 1 <= len(parts) <= 50
                or type(part_size) is not int or not 1 <= part_size <= 10485760
                or upload['partCount'] != len(parts) or upload['fileSize'] != info['fileSize']
                or not isinstance(upload['uploadId'], str) or not isinstance(upload['key'], str)):
            raise CLIError('上传初始化返回的分片信息不合法。')
        total = 0
        for n, part in enumerate(parts, 1):
            expected = min(part_size, info['fileSize'] - total)
            if (expected <= 0 or part['partNumber'] != n or part['contentLength'] != expected
                    or part['method'] != 'PUT'):
                raise CLIError('上传分片顺序或长度不正确。')
            total += expected
        if total != info['fileSize']:
            raise CLIError('上传分片总长度不匹配。')
        with path.open('rb') as source:
            for part in parts:
                data = source.read(part['contentLength'])
                if len(data) != part['contentLength']:
                    raise CLIError('上传过程中本地文件发生变化。')
                client.put_part(part['uploadUrl'], part['headers'], data)
        current = path.stat()
        if (current.st_size, current.st_mtime_ns) != (original.st_size, original.st_mtime_ns):
            raise CLIError('上传过程中本地文件发生变化，未完成登记。')
        body = {'uploadId': upload['uploadId'], 'key': upload['key']}
        if cover_key is not None:
            body['coverKey'] = cover_key
        result = client.request('POST', '/openapi/v1/files', body)
        return dict(result, upload_key=upload['key'])
    except (CLIError, KeyError, TypeError, OSError) as e:
        raise CLIError(f'上传未完成：{e}；uploadId={upload.get("uploadId", "unknown")}。未自动重试；分片失败需重新创建上传，完成登记请求超时应先查 assets。') from None
