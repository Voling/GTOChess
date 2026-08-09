from __future__ import annotations

import hashlib
import hmac
import json

import httpx

from gtochess.jobs.notify import (
    EVENT_HEADER,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    deliver,
    sign,
)


def recorder(status: int = 200) -> tuple[httpx.Client, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status)

    return httpx.Client(transport=httpx.MockTransport(handle)), seen


class TestDelivery:
    def test_the_event_and_body_are_posted(self) -> None:
        client, seen = recorder()
        assert deliver("https://hook.test/x", "import.finished", {"usable": 12}, client=client)
        assert len(seen) == 1
        payload = json.loads(seen[0].content)
        assert payload == {"event": "import.finished", "data": {"usable": 12}}
        assert seen[0].headers[EVENT_HEADER] == "import.finished"

    def test_a_secret_signs_the_body(self) -> None:
        client, seen = recorder()
        deliver("https://hook.test/x", "e", {"a": 1}, secret="shh", client=client)
        request = seen[0]
        stamp = request.headers[TIMESTAMP_HEADER]
        expected = hmac.new(
            b"shh", f"{stamp}.".encode() + request.content, hashlib.sha256
        ).hexdigest()
        assert request.headers[SIGNATURE_HEADER] == f"sha256={expected}"

    def test_the_signature_covers_the_timestamp(self) -> None:
        body = b'{"a":1}'
        assert sign(body, "100", "k") != sign(body, "101", "k")

    def test_without_a_secret_nothing_is_signed(self) -> None:
        client, seen = recorder()
        deliver("https://hook.test/x", "e", {}, client=client)
        assert SIGNATURE_HEADER not in seen[0].headers

    def test_a_rejected_delivery_reports_failure(self) -> None:
        client, _ = recorder(status=500)
        assert deliver("https://hook.test/x", "e", {}, client=client) is False

    def test_an_unreachable_receiver_never_raises(self) -> None:
        def explode(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        client = httpx.Client(transport=httpx.MockTransport(explode))
        assert deliver("https://hook.test/x", "e", {}, client=client) is False

    def test_the_body_is_stable_so_signatures_verify(self) -> None:
        client, seen = recorder()
        deliver("https://hook.test/x", "e", {"b": 2, "a": 1}, client=client)
        deliver("https://hook.test/x", "e", {"a": 1, "b": 2}, client=client)
        assert seen[0].content == seen[1].content
