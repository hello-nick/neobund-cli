import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skill/neobund-publish/scripts'
sys.path.insert(0, str(SCRIPTS))

from client import Client, CLIError
from publishing import validate_tasks, schedule_utc, submit_tasks
from uploads import upload_file


def task(**changes):
    return dict(authType=1, authId=9223372036854775800,
                productId='749000000001', productTitle='Blue shirt',
                videoTitle='New arrival', videoSource=1, fileId=2001001,
                **changes)


class PublishingTests(unittest.TestCase):
    def test_explicit_default_does_not_bypass_unknown_protection(self):
        calls = []
        class Fake:
            def request(self, *args, **kwargs):
                calls.append(args)
                raise CLIError('timeout')
        with tempfile.TemporaryDirectory() as d:
            for t in [task(), task(precheck=0)]:
                with self.assertRaises(CLIError):
                    submit_tasks(Fake(), [t], Path(d))
            self.assertEqual(len(calls), 1)

    def test_partial_batch_response_is_unknown(self):
        class Fake:
            def request(self, *args, **kwargs):
                return {'status': 0, 'data': {'taskIds': [5001]}}
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(CLIError):
                submit_tasks(Fake(), [task(), dict(task(), videoTitle='Second')], Path(d))
            receipt = json.loads(next(Path(d).glob('*.json')).read_text())
            self.assertEqual(receipt['state'], 'unknown')
            self.assertEqual(receipt['response']['data']['taskIds'], [5001])

    def test_concurrent_identical_batch_sends_once(self):
        from concurrent.futures import ThreadPoolExecutor
        calls = []
        class Fake:
            def request(self, *args, **kwargs):
                calls.append(args)
                return {'status': 0, 'data': {'taskIds': [5001]}}
        with tempfile.TemporaryDirectory() as d:
            def attempt(_):
                try:
                    return submit_tasks(Fake(), [task()], Path(d))
                except CLIError:
                    return None
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(attempt, range(2)))
            self.assertEqual(len(calls), 1)
            self.assertEqual(sum(x is not None for x in results), 1)

    def test_timezone_conversion(self):
        self.assertEqual(schedule_utc('2099-08-01T18:00:00+08:00'),
                         '2099-08-01 10:00:00')

    def test_naive_and_past_times_rejected(self):
        for value in ['2099-08-01T18:00:00', '2020-01-01T00:00:00Z']:
            with self.subTest(value=value), self.assertRaises(CLIError):
                schedule_utc(value)

    def test_batch_rejects_invalid_fields_before_sending(self):
        cases = [[], [task()] * 101, [task(unknown=True)],
                 [dict(task(), authId=True)], [dict(task(), fileId=1.5)],
                 [dict(task(), productId=' ')], [dict(task(), productTitle='x'*31)],
                 [dict(task(), taskNo='AI', videoId=1)],
                 [dict(task(), authType=2)],
                 [dict(task(), scheduledReleaseTime='2099-02-30 10:00:00')]]
        for tasks in cases:
            with self.subTest(tasks=tasks), self.assertRaises(CLIError):
                validate_tasks(tasks)

    def test_ai_source_and_int64(self):
        t = task()
        t.pop('fileId')
        t.update(videoSource=2, taskNo='AI123', videoId=9223372036854775801)
        self.assertEqual(validate_tasks([t]), [dict(t, precheck=0)])
        self.assertEqual(json.loads(json.dumps(t))['authId'], 9223372036854775800)

    def test_ambiguous_submit_is_not_retried(self):
        calls = []
        class Fake:
            def request(self, *args, **kwargs):
                calls.append(args)
                raise CLIError('request outcome unknown')
        with tempfile.TemporaryDirectory() as d:
            for _ in range(2):
                with self.assertRaises(CLIError):
                    submit_tasks(Fake(), [task()], Path(d))
            self.assertEqual(len(calls), 1)
            receipt = json.loads(next(Path(d).glob('*.json')).read_text())
            self.assertEqual(receipt['state'], 'unknown')

    def test_success_receipt_and_duplicate_block(self):
        class Fake:
            def request(self, *args, **kwargs):
                return {'status': 0, 'msg': 'success', 'data': {'taskIds': [5001]}}
        with tempfile.TemporaryDirectory() as d:
            result = submit_tasks(Fake(), [task()], Path(d))
            self.assertEqual(result['data']['taskIds'], [5001])
            self.assertEqual(json.loads(Path(result['receipt']).read_text())['state'], 'submitted')
            with self.assertRaises(CLIError):
                submit_tasks(Fake(), [task()], Path(d))


class TransportTests(unittest.TestCase):
    def test_truncated_http_response_is_reported_without_traceback(self):
        import http.client
        c = Client(api_key='secret')
        with patch.object(c.opener, 'open', side_effect=http.client.IncompleteRead(b'part', 10)):
            with self.assertRaises(CLIError):
                c.request('GET', '/openapi/v1/products')

    def test_private_key_file_and_permissions(self):
        from client import load_api_key
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {}, clear=True):
            path = Path(d)/'key'
            path.write_text('private-key\n')
            path.chmod(0o600)
            with patch.dict(os.environ, {'NEOBUND_API_KEY_FILE': str(path)}):
                self.assertEqual(load_api_key(), 'private-key')
                path.chmod(0o644)
                with self.assertRaises(CLIError):
                    load_api_key()

    def test_bad_base_urls_rejected(self):
        for base in ['http://example.com', 'https://user:pass@example.com',
                     'https://example.com?key=x', 'https://example.com/#x']:
            with self.subTest(base=base), self.assertRaises(CLIError):
                Client(api_key='secret', base_url=base)

    def test_key_missing_fails_locally(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(CLIError):
            Client()

    def test_business_errors_redact_key(self):
        client = Client(api_key='very-secret')
        with patch.object(client, '_send', return_value=(200, b'{"status":403,"msg":"very-secret denied"}')):
            with self.assertRaises(CLIError) as err:
                client.request('GET', '/openapi/v1/products')
        self.assertNotIn('very-secret', str(err.exception))

    def test_big_response_integer_is_preserved(self):
        client = Client(api_key='secret')
        with patch.object(client, '_send', return_value=(200, b'{"status":0,"data":{"authId":9223372036854775800}}')):
            result = client.request('GET', '/openapi/v1/creator-authorizations')
        self.assertEqual(result['data']['authId'], 9223372036854775800)

    def test_http_failure_even_if_json_status_success(self):
        client = Client(api_key='secret')
        with patch.object(client, '_send', return_value=(503, b'{"status":0,"data":null}')):
            with self.assertRaises(CLIError):
                client.request('POST', '/openapi/v1/video-publication-tasks', {})

    def test_redirect_is_disabled(self):
        from client import NoRedirect
        from urllib.request import Request
        self.assertIsNone(NoRedirect().redirect_request(Request('https://example.com'), None,
                                                       302, 'Found', {}, 'https://other.com'))


class UploadTests(unittest.TestCase):
    def test_multipart_exact_bytes_and_completion(self):
        sent, calls = [], []
        class Fake:
            def request(self, method, path, body=None, **kwargs):
                calls.append((path, body))
                if path.endswith('upload-url'):
                    return {'status': 0, 'data': {
                        'uploadId': 'u1', 'key': 'videos/v.mp4', 'partSize': 4,
                        'partCount': 3, 'fileSize': 10, 'parts': [
                            {'partNumber': n, 'contentLength': size,
                             'method': 'PUT', 'uploadUrl': 'https://s3.example/p'+str(n),
                             'headers': {'Content-Length': str(size)}}
                            for n, size in [(1, 4), (2, 4), (3, 2)]]}}
                return {'status': 0, 'data': {'fileId': 2001001}}
            def put_part(self, url, headers, data):
                sent.append(data)
                self_test.assertNotIn('NB-API-Key', headers)
        self_test = self
        with tempfile.TemporaryDirectory() as d:
            video = Path(d) / 'test.mp4'
            video.write_bytes(b'0123456789')
            out = upload_file(Fake(), video)
        self.assertEqual(sent, [b'0123', b'4567', b'89'])
        self.assertEqual(calls[-1], ('/openapi/v1/files', {'uploadId': 'u1', 'key': 'videos/v.mp4'}))
        self.assertEqual(out['data']['fileId'], 2001001)

    def test_failed_part_never_completes(self):
        calls = []
        class Fake:
            def request(self, method, path, body=None, **kwargs):
                calls.append(path)
                return {'data': {'uploadId': 'u1', 'key': 'x', 'partSize': 4,
                                 'partCount': 1, 'fileSize': 4, 'parts': [
                                     {'partNumber': 1, 'contentLength': 4, 'method': 'PUT',
                                      'uploadUrl': 'https://s3.example/x', 'headers': {}}]}}
            def put_part(self, *args):
                raise CLIError('upload failed')
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'test.mp4'
            p.write_bytes(b'0123')
            with self.assertRaises(CLIError):
                upload_file(Fake(), p)
        self.assertEqual(calls, ['/openapi/v1/files/upload-url'])


class CLITests(unittest.TestCase):
    def test_installer_bad_bin_directory_leaves_no_partial_skill(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            bad = root/'not-a-directory'
            bad.write_text('existing')
            r = subprocess.run([sys.executable, str(ROOT/'install.py'),
                                '--skill-root', str(root/'skills'), '--bin-dir', str(bad)],
                               text=True, capture_output=True)
            self.assertNotEqual(r.returncode, 0)
            self.assertFalse((root/'skills/neobund-publish').exists())
            self.assertNotIn('Traceback', r.stderr)

    def run_cli(self, *args):
        env = {k: v for k, v in os.environ.items() if k not in ('NEOBUND_API_KEY', 'NEOBUND_API_KEY_FILE')}
        env['NEOBUND_API_KEY_FILE'] = '/nonexistent/neobund-test-key'
        return subprocess.run([sys.executable, str(SCRIPTS/'neobund.py'), *args],
                              capture_output=True, text=True, env=env)

    def test_publish_is_offline_dry_run_and_int64(self):
        result = self.run_cli('publish', '--auth-id', '9223372036854775800',
                              '--product-id', '749000000001', '--product-title', 'Blue shirt',
                              '--title', 'New arrival', '--file-id', '2001001',
                              '--at', '2099-08-01T18:00:00+08:00')
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertTrue(data['dry_run'])
        t = data['body']['tasks'][0]
        self.assertEqual(t['authId'], 9223372036854775800)
        self.assertEqual(t['scheduledReleaseTime'], '2099-08-01 10:00:00')

    def test_batch_file_dry_run(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'tasks.json'
            p.write_text(json.dumps({'tasks': [task()]}))
            r = self.run_cli('tasks', 'submit', '--file', str(p))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(json.loads(r.stdout)['dry_run'])

    def test_execute_requires_key_but_does_not_print_traceback(self):
        r = self.run_cli('products', 'list', '--auth-id', '101')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('NEOBUND_API_KEY', r.stderr)
        self.assertNotIn('Traceback', r.stderr)

    def test_doctor_without_credentials(self):
        r = self.run_cli('doctor')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(json.loads(r.stdout)['api_key_configured'])


if __name__ == '__main__':
    unittest.main()
