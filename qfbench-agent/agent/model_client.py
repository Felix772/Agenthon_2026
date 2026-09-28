"""Chat-completions client for the organizer-provided endpoint only."""
import http.client
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request


class ModelError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ModelClient:
    def __init__(self, endpoint=None, model=None, token=None):
        endpoint = endpoint or os.environ.get("MODEL_ENDPOINT", "")
        self.model = model or os.environ.get("MODEL_NAME", "")
        self.token = token or os.environ.get("MODEL_TOKEN", "")
        if not endpoint or not self.model:
            raise ModelError("Set organizer-provided MODEL_ENDPOINT and MODEL_NAME; no model is configured.")
        if not self.token or any(c.isspace() for c in self.token):
            raise ModelError("Set a valid organizer-provided MODEL_TOKEN bearer credential.")
        parsed = urllib.parse.urlsplit(endpoint)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ModelError("MODEL_ENDPOINT must be an HTTP(S) base URL without credentials, query or fragment.")
        if parsed.path not in ("", "/"):
            raise ModelError("MODEL_ENDPOINT must be the route origin without a path; /v1 is added by the client.")
        self.url = endpoint.rstrip("/") + "/v1/chat/completions"
        # Respects proxy environment variables. No vendor defaults,
        # redirects, remote tools or fallback endpoints.
        self.opener = urllib.request.build_opener(NoRedirect())
        self.input_tokens = self.output_tokens = self.requests = 0
        self.unknown_usage_requests = 0
        self.last_http_status = None
        self.seed = int(os.environ.get("QFBENCH_SEED", "0"))

    def complete(self, messages, deadline):
        estimate = len(json.dumps(messages, ensure_ascii=False).encode("utf-8")) + 1024
        # Current House contract: 25 admitted requests, 4000 output tokens per
        # request. Cumulative usage is evidence, not a per-unit token allowance.
        max_tokens = 4000
        payload = json.dumps({"model": self.model, "messages": messages, "temperature": 0,
                              "seed": self.seed, "max_tokens": max_tokens, "stream": False,
                              "chat_template_kwargs": {"enable_thinking": False}}).encode("utf-8")
        for retry in range(2):
            # Conservatively count every send, including uncertain failures/retries.
            if self.requests >= 25:
                raise ModelError("Local model request budget exhausted (25 requests)")
            remaining = deadline - time.monotonic()
            if remaining < 1:
                raise ModelError("Task deadline reached before model request")
            # Reserve pessimistically when requests fail or usage is absent.
            self.input_tokens += estimate
            self.output_tokens += max_tokens
            self.requests += 1
            self.unknown_usage_requests += 1
            self.last_http_status = None
            request = urllib.request.Request(self.url, data=payload, headers={
                "Content-Type": "application/json", "Authorization": "Bearer " + self.token})
            try:
                with self.opener.open(request, timeout=min(90, remaining)) as response:
                    self.last_http_status = 200
                    raw = response.read(2_000_001)
                if time.monotonic() >= deadline:
                    raise ModelError("Task deadline reached while receiving model response")
                if len(raw) > 2_000_000:
                    raise ModelError("Model response exceeds 2 MB")
                result = json.loads(raw)
                usage = result.get("usage") or {}
                if all(type(usage.get(key)) is int and usage[key] >= 0
                       for key in ("prompt_tokens", "completion_tokens")):
                    self.unknown_usage_requests -= 1
                for key, attr, reserved in (("prompt_tokens", "input_tokens", estimate), ("completion_tokens", "output_tokens", max_tokens)):
                    value = usage.get(key)
                    if type(value) is int and value >= 0:
                        setattr(self, attr, getattr(self, attr) - reserved + value)
                choice = result["choices"][0]
                if choice.get("finish_reason") == "length":
                    raise ModelError("Model response truncated; reduce solution size")
                content = choice["message"]["content"]
                if not isinstance(content, str) or not content.strip():
                    raise ModelError("Model returned no text solution")
                return content
            except urllib.error.HTTPError as exc:
                self.last_http_status = exc.code
                if exc.code in (401, 403):
                    # Official contract: these refusals happen before admission.
                    self.input_tokens -= estimate
                    self.output_tokens -= max_tokens
                    self.requests -= 1
                    self.unknown_usage_requests -= 1
                if exc.code not in (429, 500, 502, 503, 504) or retry:
                    raise ModelError(f"Model endpoint returned HTTP {exc.code}") from None
            except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError):
                if retry:
                    raise ModelError("Model endpoint connection failed or timed out") from None
            except (KeyError, IndexError, TypeError, ValueError, AttributeError):
                raise ModelError("Model endpoint returned malformed chat-completions JSON") from None
            time.sleep(min(1.0, max(0, deadline - time.monotonic())))
        raise ModelError("Model request failed")
