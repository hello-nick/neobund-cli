#!/usr/bin/env python3
"""Neobund shoppable video CLI; stdout is JSON, errors go to stderr."""
import argparse
import getpass
import json
import os
from pathlib import Path
import sys
import time

from client import Client, CLIError, key_file, load_api_key
from publishing import positive_id, schedule_utc, validate_tasks, submit_tasks, state_directory
from uploads import file_info, upload_file


def id_arg(value):
    try:
        return positive_id(int(value))
    except (ValueError, CLIError):
        raise argparse.ArgumentTypeError('必须是正的 int64 整数。') from None


def page_size(value):
    try:
        n = int(value)
        if not 1 <= n <= 100:
            raise ValueError()
        return n
    except ValueError:
        raise argparse.ArgumentTypeError('必须为 1～100。') from None


def pagination(p):
    p.add_argument('--page', type=id_arg, default=1)
    p.add_argument('--page-size', type=page_size, default=20)


def execution(p, publish=False):
    p.add_argument('--execute', action='store_true', help='实际执行；不传时只生成本地预览')
    if publish:
        p.add_argument('--submission-id', help='明确发起同参数的新发布时使用；不能用来盲目重试')


def parser():
    p = argparse.ArgumentParser(description='Neobund 带货视频 CLI（Python 3.11+）。凭证：NEOBUND_API_KEY。')
    commands = p.add_subparsers(dest='command', required=True)
    commands.add_parser('doctor', help='本地检查配置，不输出密钥，不调用 API')
    q = commands.add_parser('configure', help='在交互终端安全输入 API Key，保存为权限 600 的本地文件')
    q.add_argument('--replace', action='store_true', help='替换已存在的密钥文件')
    a = commands.add_parser('auth', help='已在 Neobund 绑定的授权账号').add_subparsers(dest='action', required=True)
    q = a.add_parser('list'); pagination(q)
    q.add_argument('--username'); q.add_argument('--product-id')
    q.add_argument('--include-subaccounts', action='store_true')
    a = commands.add_parser('products', help='查询和同步商品').add_subparsers(dest='action', required=True)
    q = a.add_parser('list'); pagination(q)
    q.add_argument('--auth-id', type=id_arg, required=True)
    q.add_argument('--product-id'); q.add_argument('--title')
    q = a.add_parser('sync'); q.add_argument('--auth-id', type=id_arg, action='append', required=True); execution(q)
    q = commands.add_parser('assets', help='上传登记超时后核查资产'); pagination(q)
    q.add_argument('--folder-id', type=int, default=0)
    q = commands.add_parser('upload', help='上传视频，可附带封面；默认只预览本地文件')
    q.add_argument('--file', type=Path, required=True); q.add_argument('--cover', type=Path); execution(q)
    q = commands.add_parser('publish', help='创建一条带货视频任务；默认预览')
    q.add_argument('--auth-id', type=id_arg, required=True)
    q.add_argument('--product-id', required=True); q.add_argument('--product-title', required=True)
    q.add_argument('--title', required=True, help='视频文案')
    source = q.add_mutually_exclusive_group(required=True)
    source.add_argument('--file-id', type=id_arg, help='已上传的视频资产 ID')
    source.add_argument('--ai-video-id', type=id_arg, help='已有 AI 资产的 assetId')
    q.add_argument('--ai-task-no', help='已有 AI 资产的 videoTaskNo')
    q.add_argument('--at', help='未来的带时区 ISO 时间，例如 2027-08-01T18:00:00+08:00；省略则尽快发布')
    q.add_argument('--precheck', action='store_true'); q.add_argument('--remark')
    q.add_argument('--cover-ms', type=int); execution(q, publish=True)
    a = commands.add_parser('tasks', help='批量发布及结果查询').add_subparsers(dest='action', required=True)
    q = a.add_parser('submit'); q.add_argument('--file', type=Path, required=True, help='{"tasks":[...]} JSON 文件'); execution(q, True)
    q = a.add_parser('list'); pagination(q)
    q.add_argument('--auth-id', type=id_arg); q.add_argument('--product-id'); q.add_argument('--title')
    q.add_argument('--status', type=int, choices=[100, 200, 250, 300, 350, 500, 800])
    for name in ('get', 'wait'):
        q = a.add_parser(name); q.add_argument('--task-id', type=id_arg, required=True)
        if name == 'wait':
            q.add_argument('--timeout', type=id_arg, default=120, help='总等待秒数；默认 120')
            q.add_argument('--interval', type=id_arg, default=10, help='轮询间隔秒数；最小 2')
    return p


def preview(path, body):
    return {'dry_run': True, 'method': 'POST', 'path': path, 'body': body,
            'note': '仅完成本地参数校验，未提交。账号及商品资格由 Neobund/TikTok 验证。'}


def read_batch(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise CLIError(f'JSON 包含重复字段：{key}')
            result[key] = value
        return result
    try:
        obj = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique)
    except (ValueError, UnicodeError):
        raise CLIError('任务文件不是有效的 UTF-8 JSON。') from None
    if not isinstance(obj, dict) or set(obj) != {'tasks'}:
        raise CLIError('任务文件顶层必须且只能有 tasks 字段。')
    return validate_tasks(obj['tasks'])


def run(args):
    if args.command == 'configure':
        if not sys.stdin.isatty():
            raise CLIError('configure 需要交互终端；也可配置 NEOBUND_API_KEY 环境变量。')
        path = key_file()
        if path.exists() and not args.replace:
            raise CLIError('密钥文件已存在；如要更换请使用 configure --replace。')
        key = getpass.getpass('Neobund API Key（输入不回显）: ').strip()
        if not key or any(c in key for c in '\r\n'):
            raise CLIError('密钥不能为空或包含换行。')
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Atomic replace; secret never enters argv, stdout or a world-readable file.
        import tempfile
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.api-key-')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(key + '\n')
                f.flush()
                os.fsync(f.fileno())
            if args.replace:
                os.replace(tmp, path)
            else:
                os.link(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return {'configured': True, 'key_file': str(path)}
    if args.command == 'doctor':
        return {'api_key_configured': bool(load_api_key()),
                'base_url_source': 'NEOBUND_BASE_URL' if os.environ.get('NEOBUND_BASE_URL') else 'https://open.neobund.ai',
                'state_directory': str(state_directory()), 'python': sys.version.split()[0],
                'api_access_requirement': 'Neobund 企业版主账号',
                'account_support': 'Official Account 自卖商品支持须由 Neobund 或实际联调确认。'}
    if args.command == 'publish' or (args.command == 'tasks' and args.action == 'submit'):
        if args.command == 'publish':
            t = {'authType': 1, 'authId': args.auth_id, 'productId': args.product_id,
                 'productTitle': args.product_title, 'videoTitle': args.title,
                 'videoSource': 1 if args.file_id is not None else 2,
                 'precheck': int(args.precheck)}
            if args.file_id is not None:
                t['fileId'] = args.file_id
                if args.ai_task_no is not None:
                    raise CLIError('--ai-task-no 不能与 --file-id 混用。')
            else:
                t.update(taskNo=args.ai_task_no, videoId=args.ai_video_id)
            if args.at:
                t['scheduledReleaseTime'] = schedule_utc(args.at)
            if args.remark is not None:
                t['remark'] = args.remark
            if args.cover_ms is not None:
                t['coverTimestampMs'] = args.cover_ms
            tasks = validate_tasks([t])
        else:
            tasks = read_batch(args.file)
        if not args.execute:
            return preview('/openapi/v1/video-publication-tasks', {'tasks': tasks})
        return submit_tasks(Client(), tasks, submission_id=args.submission_id)
    if args.command == 'upload':
        video_path, video_info = file_info(args.file)
        cover_info = file_info(args.cover, cover=True)[1] if args.cover else None
        if not args.execute:
            return {'dry_run': True, 'video': str(video_path), 'video_info': video_info,
                    'cover_info': cover_info, 'note': '未上传。加 --execute 上传；上传本身不会发布视频。'}
        c = Client()
        key = upload_file(c, args.cover, cover=True)['upload_key'] if args.cover else None
        return upload_file(c, args.file, cover_key=key)
    if args.command == 'products' and args.action == 'sync':
        if not 1 <= len(args.auth_id) <= 10:
            raise CLIError('一次同步 1～10 个授权 ID。')
        body = {'authIds': args.auth_id}
        if not args.execute:
            return preview('/openapi/v1/creator-authorizations/products/sync', body)
        return Client().request('POST', '/openapi/v1/creator-authorizations/products/sync', body)
    c = Client()
    if args.command == 'auth':
        return c.request('GET', '/openapi/v1/creator-authorizations', query={
            'pageNum': args.page, 'pageSize': args.page_size, 'username': args.username,
            'productId': args.product_id, 'includeSubAccounts': args.include_subaccounts})
    if args.command == 'products':
        return c.request('GET', '/openapi/v1/products', query={
            'pageNum': args.page, 'pageSize': args.page_size, 'authId': args.auth_id,
            'productId': args.product_id, 'title': args.title})
    if args.command == 'assets':
        if args.folder_id < 0:
            raise CLIError('folder-id 不能为负数。')
        return c.request('GET', '/openapi/v1/assets', query={
            'pageNum': args.page, 'pageSize': args.page_size, 'assetType': 1,
            'sourceType': 2, 'folderId': args.folder_id})
    if args.command == 'tasks':
        path = '/openapi/v1/video-publication-tasks'
        if args.action == 'list':
            return c.request('GET', path, query={'pageNum': args.page, 'pageSize': args.page_size,
                'authType': 1, 'authId': args.auth_id, 'productId': args.product_id,
                'videoTitle': args.title, 'status': args.status})
        query = {'taskId': args.task_id, 'pageNum': 1, 'pageSize': 20}
        if args.action == 'get':
            return c.request('GET', path, query=query)
        if args.interval < 2:
            raise CLIError('轮询间隔至少 2 秒。')
        deadline = time.monotonic() + args.timeout
        while True:
            remaining = deadline - time.monotonic()
            c.timeout = max(0.1, min(60, remaining))
            result = c.request('GET', path, query=query)
            data = result.get('data') or {}
            records = data.get('records', [])
            row = next((r for r in records if str(r.get('taskId')) == str(args.task_id)), None)
            if row and row.get('status') in (500, 800):
                return dict(result, finished=True, published=row['status'] == 500,
                            _exit_code=0 if row['status'] == 500 else 4)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return dict(result, finished=False, timed_out=True, _exit_code=3)
            time.sleep(min(args.interval, remaining))
    raise CLIError('不支持的命令。')


def main():
    try:
        args = parser().parse_args()
        result = run(args)
        code = result.pop('_exit_code', 0)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return code
    except (CLIError, OSError, ValueError) as e:
        message = str(e)
        try:
            key = load_api_key()
        except (CLIError, OSError):
            key = os.environ.get('NEOBUND_API_KEY')
        if key:
            message = message.replace(key, '[REDACTED]')
        print(json.dumps({'error': message}, ensure_ascii=False), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print('{"error":"已中断；若正在提交，请先检查本地日志和任务列表，勿直接重发。"}', file=sys.stderr)
        return 130


if __name__ == '__main__':
    sys.exit(main())
