from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from omega_prime.eventstream.config import EventStreamConfig
from omega_prime.eventstream.envelope import CloudEvent, PublishRequest
from omega_prime.eventstream.errors import ConfigurationError
from omega_prime.eventstream.outbox import SQLiteOutbox
from omega_prime.eventstream.taxonomy import derive_key, schema_uri_for_type


def test_secret_file_permissions_and_outbox_redaction_fail_closed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    async def scenario() -> None:
        secret = "tok" + "_live_" + "runtime-known-secret-123"
        secret_path = tmp_path / "eventstream-token"
        secret_path.write_text(secret, encoding="utf-8")
        secret_path.chmod(0o600)

        config = EventStreamConfig(token_file=secret_path)
        assert config.read_secret("token") == secret
        database = tmp_path / "runtime-security.sqlite3"
        outbox = SQLiteOutbox(
            database,
            producer_name="runtime-security",
            config=config,
        )

        payload: dict[str, Any] = {
            "repo": "Omega-Prime",
            "number": 75,
            "head_sha": "b8eb05f",
            "actor": secret,
            "title": "Example sk-runtime-secret-shaped-value-123",
            "base_ref": "main",
        }
        event_type = "omega.sdlc.pr.opened"
        event = CloudEvent.model_validate(
            {
                "specversion": "1.0",
                "id": "runtime-security-event",
                "source": "github://example/project",
                "type": event_type,
                "datacontenttype": "application/json",
                "dataschema": schema_uri_for_type(event_type),
                "data": payload,
                "omegakey": "Omega-Prime#75",
            }
        )
        request = PublishRequest(
            event=event,
            topic="persistent://omega/sdlc/pr",
            key=derive_key(event.type, event.data),
            sequence_id=0,
            producer_name="runtime-security",
            properties={
                "authorization": secret,
                "nested": {"opaque": "sk-runtime-property-secret-987654"},
            },
        )

        connection = outbox.open_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            record = outbox.append(connection, request)
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()
        outbox.notify_after_commit()

        with closing(outbox.open_connection()) as stored:
            row = stored.execute(
                "SELECT event, properties FROM eventstream_outbox WHERE sequence_id = ?",
                (record.sequence_id,),
            ).fetchone()
            assert row is not None
            event_bytes = bytes(row[0])
            properties_bytes = str(row[1]).encode("utf-8")
        stored_bytes = event_bytes + properties_bytes
        for sensitive in (
            secret,
            "sk-runtime-secret-shaped-value-123",
            "sk-runtime-property-secret-987654",
        ):
            assert sensitive.encode("utf-8") not in stored_bytes
        persisted_event = json.loads(event_bytes)
        persisted_properties = json.loads(properties_bytes)
        assert persisted_event["data"]["actor"] == "[REDACTED]"
        assert persisted_event["data"]["title"] == "Example [REDACTED]"
        assert persisted_properties["authorization"] == "[REDACTED]"
        assert persisted_properties["nested"]["opaque"] == "[REDACTED]"
        assert repr(config).find(secret) == -1
        assert repr(outbox).find(secret) == -1

        replacement = tmp_path / "replacement-token"
        replacement.write_text(secret, encoding="utf-8")
        replacement.chmod(0o644)
        os.replace(replacement, secret_path)

        with pytest.raises(ConfigurationError) as read_error:
            config.read_secret("token")
        with pytest.raises(ConfigurationError) as append_error:
            connection = outbox.open_connection()
            try:
                connection.execute("BEGIN IMMEDIATE")
                outbox.append(connection, request)
                connection.commit()
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise
            finally:
                connection.close()
        messages = " ".join(
            (
                str(read_error.value),
                str(append_error.value),
                repr(config),
                repr(outbox),
                caplog.text,
            )
        )
        assert secret not in messages
        assert "replacement-token" not in messages

        with closing(sqlite3.connect(database)) as verification:
            assert verification.execute(
                "SELECT COUNT(*) FROM eventstream_outbox"
            ).fetchone() == (1,)
        assert "[REDACTED]" in stored_bytes.decode("utf-8")

    asyncio.run(scenario())
