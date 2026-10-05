"""Causal tests for the actual-send ceiling and caller retry allocation."""
import io
import json
import time
import unittest
import urllib.error
from unittest.mock import Mock, patch

from agent.model_client import ModelClient, ModelError


class SendBudgetTests(unittest.TestCase):
    def client(self):
        client = ModelClient('http://house.invalid', 'house', 'synthetic-token')
        client.opener = Mock()
        return client

    def test_permanent_refusals_cannot_reset_actual_send_ceiling(self):
        client = self.client()
        client.opener.open.side_effect = urllib.error.HTTPError(
            'http://house.invalid', 403, 'refused', {}, None)
        for _ in range(26):
            with self.assertRaises(ModelError):
                client.complete([], time.monotonic() + 5)
        self.assertEqual(client.opener.open.call_count, 25)
        self.assertEqual(client.sends, 25)
        self.assertEqual(client.requests, 0)
        self.assertEqual(client.remaining_sends, 0)

    def test_caller_can_reserve_retry_slots_for_later_groups(self):
        client = self.client()
        client.opener.open.side_effect = TimeoutError()
        with patch('agent.model_client.time.sleep'), self.assertRaises(ModelError):
            client.complete([], time.monotonic() + 5, max_sends=1)
        self.assertEqual(client.opener.open.call_count, 1)
        self.assertEqual(client.sends, 1)

    def test_terminal_failure_is_sanitized_and_classified(self):
        client = self.client()
        client.opener.open.side_effect = urllib.error.HTTPError(
            'http://secret.invalid?token=private', 401, 'private response', {}, None)
        with self.assertRaises(ModelError) as caught:
            client.complete([], time.monotonic() + 5)
        self.assertTrue(caught.exception.terminal)
        self.assertEqual(caught.exception.category, 'http_permanent')
        self.assertNotIn('private', str(caught.exception))
        self.assertNotIn('secret', str(caught.exception))

    def test_last_slot_does_not_permit_transport_retry(self):
        client = self.client()
        client.sends = 24
        client.opener.open.side_effect = TimeoutError()
        with patch('agent.model_client.time.sleep'), self.assertRaises(ModelError):
            client.complete([], time.monotonic() + 5)
        self.assertEqual(client.opener.open.call_count, 1)
        self.assertEqual(client.remaining_sends, 0)

    def test_success_keeps_response_metadata_without_payload(self):
        client = self.client()
        raw = json.dumps({'choices': [{'message': {'content': 'private solution'},
                                      'finish_reason': 'stop'}]}).encode()
        client.opener.open.return_value = io.BytesIO(raw)
        self.assertEqual(client.complete([], time.monotonic() + 5), 'private solution')
        self.assertEqual(client.last_finish_reason, 'stop')
        self.assertEqual(client.last_response_bytes, len(raw))
        self.assertEqual(client.sends, client.requests)


if __name__ == '__main__':
    unittest.main()
