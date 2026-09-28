"""Boundary tests for local artifact auditing, independent of official scoring."""
import json
from pathlib import Path
import tempfile
import unittest
from evidence_audit import LIMIT, audit_budget, audit_output


class AuditTests(unittest.TestCase):
    def test_separate_file_and_aggregate_limits_and_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'a').write_bytes(b'123456')
            (root / 'b').write_bytes(b'12345')
            report = audit_output(root, expected=['a', 'missing'], limit=10)
            self.assertFalse(report['passed'])
            self.assertIn('aggregate_size_limit', report['errors'])
            self.assertNotIn('file_size_limit', report['errors'])
            self.assertEqual(report['missing_deliverable_count'], 1)
            self.assertEqual(report['credential_check'], 'not_checked')
            (root / 'b').write_bytes(b'1' * 11)
            self.assertIn('file_size_limit', audit_output(root, limit=10)['errors'])

    def test_secret_across_chunk_boundary_is_redacted(self):
        with tempfile.TemporaryDirectory() as directory:
            secret = 'dummy-secret-for-audit'
            path = Path(directory) / 'result.bin'
            path.write_bytes(b'x' * (1024 * 1024 - 4) + secret.encode())
            report = audit_output(directory, secrets=[secret])
            self.assertEqual(report['credential_check'], 'failed')
            self.assertNotIn(secret, json.dumps(report))
            self.assertFalse(report['passed'])

    def test_valid_and_exact_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'results.json').write_bytes(b'{}')
            report = audit_output(directory, ['results.json'], ['dummy-unused'], limit=2)
            self.assertTrue(report['passed'])
            self.assertEqual(report['total_bytes'], 2)
            self.assertEqual(report['credential_check'], 'passed')

    def test_rejects_symlink_without_reading_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'target').write_text('private', encoding='utf-8')
            (root / 'link').symlink_to(root / 'target')
            self.assertIn('non_regular_output', audit_output(root)['errors'])

    def test_real_64mib_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sparse'
            for size in (LIMIT - 1, LIMIT, LIMIT + 1):
                with path.open('wb') as handle:
                    handle.truncate(size)
                self.assertEqual(audit_output(directory)['passed'], size <= LIMIT)

    def test_fifo_is_rejected_without_blocking(self):
        import os
        with tempfile.TemporaryDirectory() as directory:
            os.mkfifo(Path(directory) / 'fifo')
            self.assertFalse(audit_output(directory)['passed'])

    def test_stdout_stderr_and_artifact_secret_scope(self):
        for filename in ('stdout.log', 'stderr.log', 'result.json'):
            with tempfile.TemporaryDirectory() as directory:
                (Path(directory) / filename).write_text('dummy-sentinel')
                report = audit_output(directory, secrets=['dummy-sentinel'])
                self.assertEqual(report['credential_check'], 'failed')
                self.assertNotIn('dummy-sentinel', json.dumps(report))
                scope = {'stdout.log': 'stdout', 'stderr.log': 'stderr'}.get(filename, 'artifacts')
                self.assertEqual(report['credential_scopes'][scope], 'failed')
                self.assertTrue(all(value == 'not_checked' for key, value in report['credential_scopes'].items() if key != scope))

    def test_budget_retries_unknown_and_deadline(self):
        self.assertEqual(audit_budget(0, 0, [], 1, 2)['status'], 'passed')
        self.assertEqual(audit_budget(2, 100, [100, 200], 1, 2)['status'], 'passed')
        self.assertEqual(audit_budget(25, 100, [1] * 25, 1, 2)['status'], 'passed')
        self.assertIn('request_budget', audit_budget(26, 100, [1] * 26, 1, 2)['errors'])
        self.assertEqual(audit_budget(1, None, [None], 1, 2)['status'], 'unknown')
        self.assertIn('deadline_expired', audit_budget(0, 0, [], 2, 2)['errors'])
        self.assertEqual(audit_budget(25, 1_500_000, [4000] * 25, 1, 2)['status'], 'passed')
        self.assertIn('invalid_input_token_usage', audit_budget(1, -1, [1], 1, 2)['errors'])
        self.assertIn('output_token_budget', audit_budget(1, 1, [4001], 1, 2)['errors'])

    def test_before_after_verifier_preserves_extra_artifact_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'results.json').write_text('{}')
            before = audit_output(root, expected=['results.json'])
            (root / 'reward.json').write_text('{"reward": 1}')
            after = audit_output(root, expected=['results.json'])
            self.assertEqual(len(before['files']), 1)
            self.assertEqual(len(after['files']), 2)
            self.assertTrue(after['passed'])
            self.assertGreater(after['total_bytes'], before['total_bytes'])


if __name__ == '__main__':
    unittest.main()
