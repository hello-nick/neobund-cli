"""Request validation and persistent duplicate-submission protection."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from client import CLIError

FIELDS = {'authType', 'authId', 'productId', 'productTitle', 'videoTitle',
          'videoSource', 'fileId', 'taskNo', 'videoId', 'coverTimestampMs',
          'precheck', 'musicId', 'musicTitle', 'musicAuthor', 'musicUrl',
          'musicCoverUrl', 'remark', 'disableDuet', 'disableStitch',
          'disableComment', 'brandContentToggle', 'brandOrganicToggle',
          'aigc', 'musicVolume', 'originalSoundVolume', 'scheduledReleaseTime'}


def positive_id(value, name='ID'):
    if type(value) is not int or not 0 < value <= 9223372036854775807:
        raise CLIError(f'{name} 必须是正的 int64 整数。')
    return value


def schedule_utc(value):
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if dt.tzinfo is None or dt.utcoffset() is None:
            raise ValueError()
        dt = dt.astimezone(timezone.utc)
        if dt <= datetime.now(timezone.utc):
            raise ValueError()
    except (ValueError, TypeError, AttributeError, OverflowError):
        raise CLIError('发布时间必须是未来的带时区 ISO 时间，例如 2027-08-01T18:00:00+08:00。') from None
    return dt.strftime('%Y-%m-%d %H:%M:%S')


def validate_tasks(tasks):
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= 100:
        raise CLIError('tasks 必须包含 1～100 条任务。')
    for index, t in enumerate(tasks, 1):
        if not isinstance(t, dict) or set(t) - FIELDS:
            raise CLIError(f'任务 {index} 不是对象或包含不支持的字段。')
        if type(t.get('authType')) is not int or t['authType'] != 1:
            raise CLIError('本工具只提交带货视频：authType 必须为 1。')
        positive_id(t.get('authId'), 'authId')
        for key in ('productId', 'productTitle', 'videoTitle'):
            if not isinstance(t.get(key), str) or not t[key].strip():
                raise CLIError(f'{key} 必须是非空字符串。')
        if len(t['productTitle']) > 30:
            raise CLIError('productTitle 最多 30 个字符。')
        if type(t.get('videoSource')) is not int or t['videoSource'] not in (1, 2):
            raise CLIError('videoSource 必须为 1（上传）或 2（AI 资产）。')
        if t['videoSource'] == 1:
            positive_id(t.get('fileId'), 'fileId')
            if 'taskNo' in t or 'videoId' in t:
                raise CLIError('上传来源只能传 fileId，不能混用 AI 来源。')
        else:
            positive_id(t.get('videoId'), 'videoId')
            if not isinstance(t.get('taskNo'), str) or not t['taskNo'].strip() or 'fileId' in t:
                raise CLIError('AI 来源必须有 taskNo 和 videoId，不能传 fileId。')
        for k in ('precheck', 'disableDuet', 'disableStitch', 'disableComment',
                  'brandContentToggle', 'brandOrganicToggle', 'aigc'):
            if k in t and (type(t[k]) is not int or t[k] not in (0, 1)):
                raise CLIError(f'{k} 必须为整数 0 或 1。')
        for k in ('musicVolume', 'originalSoundVolume'):
            if k in t and (type(t[k]) is not int or not 0 <= t[k] <= 100):
                raise CLIError(f'{k} 必须为 0～100 的整数。')
        if 'coverTimestampMs' in t and (type(t['coverTimestampMs']) is not int or t['coverTimestampMs'] < 0):
            raise CLIError('coverTimestampMs 必须是非负整数。')
        for k in ('remark', 'musicId', 'musicTitle', 'musicAuthor', 'musicUrl', 'musicCoverUrl'):
            if k in t and not isinstance(t[k], str):
                raise CLIError(f'{k} 必须是字符串。')
        if 'scheduledReleaseTime' in t:
            try:
                text = t['scheduledReleaseTime']
                dt = datetime.strptime(text, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
                if dt.strftime('%Y-%m-%d %H:%M:%S') != text or dt <= datetime.now(timezone.utc):
                    raise ValueError()
            except (ValueError, TypeError):
                raise CLIError('批量 scheduledReleaseTime 必须为未来 UTC 时间：yyyy-MM-dd HH:mm:ss。') from None
    # Neobund defaults precheck to 0; normalize before fingerprinting across CLI modes.
    return [dict(t, precheck=t.get('precheck', 0)) for t in tasks]


def state_directory():
    return Path(os.environ.get('NEOBUND_STATE_DIR',
                               str(Path.home()/'.local/state/neobund-cli/submissions'))).expanduser()


def save_receipt(path, record):
    fd, temporary = tempfile.mkstemp(prefix='.receipt-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def submit_tasks(client, tasks, directory=None, submission_id=None):
    tasks = validate_tasks(tasks)
    if submission_id is not None and not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', submission_id):
        raise CLIError('submission-id 只接受 1～64 位字母、数字、下划线或连字符。')
    # Stable for an identical effective batch. This is local protection, not server idempotency.
    canonical = json.dumps({'tasks': tasks, 'submission_id': submission_id},
                           sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
    directory = Path(directory) if directory is not None else state_directory()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    receipt = directory / (fingerprint + '.json')
    record = {'state': 'sending', 'created_at': datetime.now(timezone.utc).isoformat(),
              'fingerprint': fingerprint, 'submission_id': submission_id, 'tasks': tasks}
    try:
        fd = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise CLIError(f'相同提交已有记录，未再次发送：{receipt}。先核对 tasks list；只有明确发起新的独立发布时才使用新的 --submission-id。') from None
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    try:
        result = client.request('POST', '/openapi/v1/video-publication-tasks', {'tasks': tasks})
        record['response'] = result
        ids = result.get('data', {}).get('taskIds') if isinstance(result.get('data'), dict) else None
        if (not isinstance(ids, list) or len(ids) != len(tasks)
                or any(type(x) is not int or x <= 0 for x in ids)):
            raise CLIError('提交返回的 taskIds 不完整，不能确认整批结果。')
    except Exception as e:
        record['state'] = 'unknown'
        save_receipt(receipt, record)
        raise CLIError(f'{e}\n提交结果未确认，未重试。日志：{receipt}；请用 tasks list 核查。') from None
    record['state'] = 'submitted'
    save_receipt(receipt, record)
    return dict(result, receipt=str(receipt), publication_state='submitted_not_published')
