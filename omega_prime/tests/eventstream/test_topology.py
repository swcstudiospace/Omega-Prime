from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from omega_prime.eventstream.taxonomy import (
    EVENT_TYPES,
    get_event_type,
    resolve_schema_uri,
)


def _load_topology() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[3] / "omega_prime/eventstream/topology.yaml"
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _topics(topology: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["name"]: item for item in topology["topics"]["definitions"]}


def _role(topology: dict[str, Any], name: str) -> dict[str, Any]:
    return next(
        item
        for item in topology["identity"]["applicationRoles"]
        if item["name"] == name
    )


def _subscription(topology: dict[str, Any], name: str) -> dict[str, Any]:
    return next(
        item for item in topology["subscriptions"]["handlers"] if item["name"] == name
    )


def test_topology_discloses_exactly_the_broker_security_and_bookkeeper_profile() -> (
    None
):
    topology = _load_topology()
    broker = topology["broker"]
    settings = broker["settings"]
    assert topology["metadata"]["version"] == 1
    assert topology["metadata"]["provisioning"] is False
    assert settings["allowAutoTopicCreation"] is False
    assert settings["brokerDeleteInactiveTopicsEnabled"] is False
    assert settings["brokerDeduplicationEnabled"] is True
    assert settings["delayedDeliveryEnabled"] is True
    assert settings["delayedDeliveryTracker"] == "bucket"
    assert settings["functionsWorkerEnabled"] is True
    assert settings["functionsWorkerRuntime"] == "process"
    assert settings["authenticationEnabled"] is settings["authorizationEnabled"] is True
    assert settings["authenticationProviders"] == ["AuthenticationProviderToken"]
    assert settings["tokenAlgorithm"] == "RS256"
    assert settings["brokerPublicKeyOnly"] is True
    assert set(settings["superUserRoles"]) == {"omega-admin", "omega-broker-internal"}
    assert (settings["brokerServicePortTls"], settings["webServicePortTls"]) == (
        6651,
        8443,
    )
    assert [
        settings[key]
        for key in (
            "managedLedgerDefaultEnsembleSize",
            "managedLedgerDefaultWriteQuorum",
            "managedLedgerDefaultAckQuorum",
        )
    ] == [1, 1, 1]
    assert settings["backlogQuotaDefaultRetentionPolicy"] == "producer_request_hold"
    assert broker["bookkeeper"]["journalSyncData"] is True
    assert broker["bookkeeper"]["durableLedgers"] == "every-topic"
    assert broker["jvm"] == {
        "minimumHeap": "1g",
        "maximumHeap": "1g",
        "maximumDirectMemory": "1g",
        "tunable": True,
    }
    assert broker["network"]["tlsRequiredForNonLoopback"] is True
    assert broker["network"]["adminApiPublic"] is False
    assert broker["security"]["tokenFilesMode"] == "0600"
    assert broker["security"]["secretsInTopology"] is False
    assert broker["security"]["privateSigningKeyInBroker"] is False
    assert topology["identity"]["administrator"] == "omega-admin"
    assert topology["identity"]["aclGranularity"] == {
        "default": "namespace-level",
        "explicitTopicExceptions": {"omega-vps-leaf": ["sdlc/ci"]},
        "readerOnlyIsClientIntentNotAuthorizationCapability": True,
    }


def test_exact_application_grants_and_privileged_internal_identity_are_separate() -> (
    None
):
    topology = _load_topology()
    roles = topology["identity"]["applicationRoles"]
    assert {role["name"] for role in roles} == {
        "omega-box",
        "omega-audit",
        "omega-vps-leaf",
        "omega-ingress",
        "omega-dashboard",
        "omega-functions",
    }
    expected = {
        "omega-box": (
            {"sdlc", "auth", "job", "sandbox", "task", "memory", "control", "system"},
            {
                "sdlc",
                "auth",
                "job",
                "sandbox",
                "task",
                "memory",
                "control",
                "audit",
                "system",
            },
        ),
        "omega-audit": ({"audit"}, {"control"}),
        "omega-vps-leaf": ({"auth", "job", "memory"}, set()),
        "omega-ingress": ({"sdlc"}, set()),
        "omega-dashboard": (
            set(),
            {
                "sdlc",
                "auth",
                "job",
                "sandbox",
                "task",
                "memory",
                "control",
                "audit",
                "system",
            },
        ),
        "omega-functions": ({"sdlc", "control"}, {"sdlc"}),
    }
    for name, (produces, consumes) in expected.items():
        role = _role(topology, name)
        assert set(role["produces"].get("namespaces", [])) == produces
        assert set(role["consumes"].get("namespaces", [])) == consumes
        assert (
            "audit" not in role["produces"].get("namespaces", [])
            or name == "omega-audit"
        )
    assert _role(topology, "omega-vps-leaf")["produces"]["topics"] == ["sdlc/ci"]
    dashboard = _role(topology, "omega-dashboard")
    assert dashboard["clientIntent"] == "reader-only"
    internal = topology["identity"]["privilegedInfrastructureIdentity"]
    assert internal["name"] == "omega-broker-internal"
    assert internal["applicationGrant"] is False
    assert internal["superuser"] is True
    assert internal["separateFromApplicationRoles"] is True
    assert internal["category"] == "infrastructure-bootstrap-not-application"
    assert internal["credentialSource"] == "operator-managed-0600-file"


def test_namespace_retention_schema_dedup_and_audit_stage_intent() -> None:
    topology = _load_topology()
    namespaces = topology["tenant"]["namespaces"]
    assert set(namespaces) == {
        "sdlc",
        "auth",
        "job",
        "sandbox",
        "task",
        "memory",
        "control",
        "audit",
        "system",
    }
    expected = {
        "sdlc": ("7d", "5GB", "BACKWARD_TRANSITIVE"),
        "auth": ("30d", None, "BACKWARD_TRANSITIVE"),
        "job": ("7d", None, "BACKWARD_TRANSITIVE"),
        "sandbox": ("48h", None, "BACKWARD_TRANSITIVE"),
        "task": ("48h", None, "BACKWARD_TRANSITIVE"),
        "memory": ("30d", None, "BACKWARD_TRANSITIVE"),
        "control": ("30d", None, "BACKWARD_TRANSITIVE"),
        "audit": ("400d", None, "FULL_TRANSITIVE"),
        "system": ("30d", None, "none-envelope-bytes"),
    }
    for name, (retention, size, compatibility) in expected.items():
        entry = namespaces[name]
        assert entry["retention"]["time"] == retention
        assert entry["retention"].get("size") == size
        assert entry["deduplication"] is True
        assert entry["schemaCompatibility"] == compatibility
    assert namespaces["task"]["retention"]["compactedState"] == "indefinite"
    assert namespaces["auth"]["encryption"] == "end-to-end"
    assert namespaces["auth"]["encryptionMechanism"] == "Pulsar-CryptoKeyReader"
    assert namespaces["control"]["delayedTopic"] == "deadline"
    offload = namespaces["audit"]["offload"]
    assert offload == {
        "enabled": True,
        "threshold": "1GB",
        "target": "Railway-Bucket",
        "stage": 3,
        "intentOnly": True,
        "provisioning": False,
    }
    assert namespaces["system"]["topics"] == [
        "retry.<subscription>",
        "dlq.<subscription>",
    ]


def test_complete_base_topic_inventory_partition_compaction_and_keys() -> None:
    topology = _load_topology()
    topics = _topics(topology)
    assert set(topics) == {
        "sdlc/pr",
        "sdlc/review",
        "sdlc/ci",
        "sdlc/pr-state",
        "auth/session",
        "job/lifecycle",
        "sandbox/lifecycle",
        "task/state",
        "task/heartbeat",
        "memory/hindsight",
        "control/signal",
        "control/action",
        "control/hitl",
        "control/deadline",
        "audit/log",
    }
    partitioned = {name for name, item in topics.items() if item["partitions"] == 4}
    assert partitioned == {"sdlc/pr", "task/state"}
    assert {name for name, item in topics.items() if item["partitions"] == 0} == set(
        topics
    ) - partitioned
    assert {name for name, item in topics.items() if item["compacted"]} == {
        "sdlc/pr-state",
        "task/state",
    }
    assert topics["task/state"]["compactedRetention"] == "indefinite"
    assert topics["control/deadline"]["delayedDelivery"] is True
    assert topics["audit/log"]["appendOnly"] is True
    assert topics["auth/session"]["encrypted"] is True
    assert topology["topics"]["partitioning"]["default"] == "non-partitioned"
    assert topology["topics"]["partitioning"]["keyedOrdering"] == "per-key-per-topic"
    assert topology["topics"]["partitioning"]["crossTopicOrdering"] == "not-guaranteed"


def test_sdk_schema_bundles_resolve_taxonomy_and_explicit_audit_family(
    schema_bundles: dict[str, Any],
) -> None:
    topology = _load_topology()
    schemas = topology["topics"]["schemas"]
    sdk = schemas["sourceOfTruth"]
    assert sdk["draft"] == "2020-12"
    assert set(sdk["families"]) == set(schema_bundles)
    assert len(sdk["families"]) == 12
    assert sdk["auditRegistration"] == "explicit-open-family-omega.audit.*"
    event_types = topology["topics"]["eventTypes"]
    concrete = [entry for entry in event_types if not entry.get("openFamily", False)]
    assert len(concrete) == 48
    assert {entry["type"] for entry in concrete} == set(EVENT_TYPES)
    assert len(event_types) == 49
    audit = next(entry for entry in event_types if entry["type"] == "omega.audit.*")
    assert audit["openFamily"] is True
    assert audit["family"] == "audit"
    assert audit["topic"] == "audit/log"
    schema_names = {
        type_name
        for family in sdk["families"]
        for type_name in schema_bundles[family]["$defs"]
        if type_name != "CloudEvent"
    }
    assert schema_names == {item["type"] for item in event_types}
    for item in event_types:
        type_name = item["type"]
        family = item["family"]
        bundle = schema_bundles[family]
        assert bundle["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert bundle["$id"] == f"urn:omega:schema:{family}:1"
        assert type_name in bundle["$defs"]
        assert sdk["payloadReference"] == "urn:omega:schema:{type}:1"
        assert sdk["definitionPointer"] == "#/$defs/{type}"
        assert sdk["path"].format(family=family) == (
            f"omega_prime/eventstream/schemas/{family}.v1.json"
        )
        definition = bundle["$defs"][type_name]
        assert isinstance(definition, dict)
        assert definition["examples"]
        assert definition["x-omega-invalid-examples"]
        payload_uri = sdk["payloadReference"].format(type=type_name)
        assert payload_uri == f"urn:omega:schema:{type_name}:1"
        assert resolve_schema_uri(payload_uri) == definition
        if type_name != "omega.audit.*":
            catalog = EVENT_TYPES[type_name]
            assert item["topic"] == catalog.topic.removeprefix("persistent://omega/")
            assert item["keyRule"] == catalog.key_rule
            assert catalog.schema_ref == payload_uri
    with pytest.raises(ValueError, match="unknown event type"):
        get_event_type("omega.unknown.event")
    assert schemas["nativeRegistry"]["pulsarSchemaType"] == "JSON"
    assert (
        schemas["nativeRegistry"]["format"]
        == "Avro-format-metadata-with-custom-JSON-codec"
    )
    assert (
        schemas["nativeRegistry"]["derivedFrom"]
        == "105-01-JSON-Schema-bundles-and-CloudEvent-envelope"
    )
    assert (
        schemas["nativeRegistry"]["owner"]
        == "105-04-existing-adapter-module-shared-with-admin"
    )
    assert schemas["nativeRegistry"]["conversionIsDistinctFromSdkSourceOfTruth"] is True
    assert schemas["nativeRegistry"]["fixedEnvelopeFields"] == [
        "specversion",
        "id",
        "source",
        "type",
        "subject",
        "time",
        "datacontenttype",
        "dataschema",
        "data",
    ]
    assert schemas["nativeRegistry"]["extensionFields"] == [
        "traceparent",
        "omegakey",
        "omegatask",
        "omegagraph",
        "omegaheadsha",
    ]
    assert schemas["nativeRegistry"]["typedTopicPayloadBranches"] is True
    assert schemas["nativeRegistry"]["stableRecordNames"] == "type-family-derived"
    assert schemas["nativeRegistry"]["messageProperties"] == [
        "ce_id",
        "ce_type",
        "ce_source",
        "ce_subject",
        "ce_time",
        "traceparent",
    ]


def test_durable_handler_modes_ack_backpressure_input_sets_and_reader_contract() -> (
    None
):
    topology = _load_topology()
    defaults = topology["subscriptions"]["defaults"]
    assert defaults["durable"] is True
    assert defaults["initialPosition"] == "Earliest"
    assert defaults["acknowledgement"] == "individual"
    assert defaults["batchIndexAcknowledgements"] is True
    assert defaults["maxInFlight"] == 16
    assert defaults["receiverQueueSize"] == "bounded"
    assert defaults["perKeyInFlight"] == 1
    assert defaults["retryTargetFilter"] == "omega_retry_target"
    expected = {
        "gate": (
            "Key_Shared",
            {"sdlc/pr", "sdlc/review", "sdlc/ci", "system/retry.gate"},
        ),
        "blocked": (
            "Key_Shared",
            {"job/lifecycle", "sandbox/lifecycle", "sdlc/ci", "system/retry.blocked"},
        ),
        "review": ("Key_Shared", {"sdlc/review", "system/retry.review"}),
        "escalator": (
            "Key_Shared",
            {
                "auth/session",
                "task/state",
                "memory/hindsight",
                "control/signal",
                "system/retry.escalator",
            },
        ),
        "merge-executor": ("Failover", {"control/action"}),
        "audit-projector": ("Exclusive", {"control/action"}),
    }
    for name, (mode, inputs) in expected.items():
        handler = _subscription(topology, name)
        assert handler["mode"] == mode
        assert set(handler["inputs"]) == inputs
    reader = topology["subscriptions"]["reader"]
    assert reader["name"] == "cc-bridge"
    assert reader["durable"] is False
    assert reader["mode"] == "Reader"
    assert reader["seek"] == "time"
    assert reader["acknowledgementObligation"] == "none"
    assert reader["authorizationNote"].startswith("Reader-only is client intent")
    assert "audit/log" in reader["inputs"]
    assert not any(
        topic.startswith(("system/retry.", "system/dlq.")) for topic in reader["inputs"]
    )


def test_retry_dlq_singleton_handoff_and_coordinator_preserve_delivery_safety() -> None:
    topology = _load_topology()
    retry = topology["retryAndDlq"]
    assert retry["perDurableSubscriptionResources"] == {
        "retryTopic": "persistent://omega/system/retry.<subscription>",
        "deadLetterTopic": "persistent://omega/system/dlq.<subscription>",
        "provisioning": "declared-only",
    }
    assert retry["maxAttempts"] == 5
    assert retry["backoff"] == {
        "algorithm": "decorrelated-jitter",
        "base": "1s",
        "cap": "600s",
    }
    assert retry["transientNegativeAckRedeliveryDelay"] == "1000ms"
    assert retry["retryTopic"] == "persistent://omega/system/retry.<subscription>"
    assert retry["deadLetterTopic"] == "persistent://omega/system/dlq.<subscription>"
    assert retry["properties"] == [
        "omega_attempt",
        "omega_origin_topic",
        "omega_last_error",
    ]
    assert retry["propertySanitization"] == "required"
    assert retry["attemptPreservedWithOriginalEnvelope"] is True
    dlq = retry["dlq"]
    assert dlq["durableSubscription"] == "dlq-triage"
    assert dlq["blockedSignalTopic"] == "persistent://omega/control/signal"
    assert dlq["blockedSignalType"] == "omega.control.signal.blocked"
    assert dlq["acknowledgeOnlyAfterDurablePublish"] is True
    handoff = retry["singletonHandoff"]
    assert handoff["requiredForModes"] == ["Failover", "Exclusive"]
    assert handoff["receiverMode"] == "Shared"
    assert set(handoff["inputs"]) == {
        "system/retry.merge-executor",
        "system/retry.audit-projector",
    }
    assert set(_subscription(topology, "merge-executor")["inputs"]) == {
        "control/action"
    }
    assert set(_subscription(topology, "audit-projector")["inputs"]) == {
        "control/action"
    }
    direct_retry_subscribers = {
        handler["name"]
        for handler in topology["subscriptions"]["handlers"]
        if handler["mode"] in {"Shared", "Key_Shared"}
        and any(topic.startswith("system/retry.") for topic in handler["inputs"])
    }
    assert direct_retry_subscribers == {"gate", "blocked", "review", "escalator"}
    handlers = topology["subscriptions"]["handlers"]
    for handler in handlers:
        retry_inputs = {
            topic for topic in handler["inputs"] if topic.startswith("system/retry.")
        }
        if handler["name"] == "dlq-triage":
            system_inputs = {
                topic for topic in handler["inputs"] if topic.startswith("system/")
            }
            assert system_inputs
            assert all(topic.startswith("system/dlq.") for topic in system_inputs)
            assert not retry_inputs
        elif handler["mode"] not in {"Shared", "Key_Shared"}:
            assert not retry_inputs
        elif handler["name"] in {"gate", "blocked", "review", "escalator"}:
            assert retry_inputs == {f"system/retry.{handler['name']}"}
    assert set(handoff["inputs"]) == {
        "system/retry.merge-executor",
        "system/retry.audit-projector",
    }
    assert handoff["targetProperty"] == "omega_retry_target"
    assert handoff["eligibleTargetRepublishedToOriginalTopic"] is True
    assert handoff["removeDeliverAfter"] is True
    assert handoff["publishBeforeSourceAck"] is True
    assert handoff["nonTargetHandlerBehavior"] == "ack-no-op-before-idempotency-claim"
    assert set(handoff["preserve"]) == {
        "ce_id",
        "key",
        "attempt",
        "origin_topic",
        "failure_history",
    }
    coordinator = retry["coordinator"]
    assert coordinator["permissionSource"] == "omega-box-existing-runtime-publisher"
    assert coordinator["noAdditionalApplicationGrant"] is True
    assert coordinator["functionsAndAuditDoNotReceiveSystemTopicGrant"] is True
    roles = topology["identity"]["applicationRoles"]
    assert _role(topology, "omega-functions")["produces"]["namespaces"] == [
        "sdlc",
        "control",
    ]
    assert _role(topology, "omega-audit")["produces"]["namespaces"] == ["audit"]
    assert len(roles) == 6


def test_functions_guarantees_filters_and_projector_crash_gap_are_explicit() -> None:
    topology = _load_topology()
    functions = topology["functions"]
    assert functions["runtime"] == "process"
    assert functions["language"] == "Python"
    assert functions["sideEffects"] == "pure-transforms-only"
    assert set(functions["supportedProcessingGuarantees"]) == {
        "ATLEAST_ONCE",
        "EFFECTIVELY_ONCE",
    }
    greptile, ci, projector = functions["definitions"]
    assert (greptile["mode"], greptile["source"], greptile["sink"]) == (
        "EFFECTIVELY_ONCE",
        "sdlc/review",
        "sdlc/review",
    )
    assert greptile["sourceFilter"] == "comment-events-only"
    assert greptile["sinkType"] == "omega.sdlc.review.greptile_scored"
    assert greptile["selfLoopFilter"] == "reject-own-output"
    assert greptile["sourceAndSinkMustBeSingleNonPartitionedTopic"] is True
    assert ci["mode"] == "EFFECTIVELY_ONCE"
    assert ci["sourceFilter"] == "check-suite-results-only"
    assert set(ci["sinkTypes"]) == {"omega.ci.run.passed", "omega.ci.run.failed"}
    assert ci["selfLoopFilter"] == "reject-own-output"
    assert ci["sourceAndSinkMustBeSingleNonPartitionedTopic"] is True
    assert projector["mode"] == "ATLEAST_ONCE"
    assert projector["parallelism"] == 1
    assert projector["durableState"] is True
    assert set(projector["sources"]) == {"sdlc/pr", "sdlc/review", "sdlc/ci"}
    assert projector["sourcePosition"] == "monotonic"
    assert projector["reducer"] == "head-aware-monotonic"
    assert projector["sink"] == "sdlc/pr-state"
    assert projector["duplicateAndStaleInputs"] == "deterministic-safe-output"
    assert projector["duplicateInputReemitsCurrentProjection"] is True
    assert (
        projector["crashGap"] == "state-before-output-is-closed-by-duplicate-reemission"
    )
    assert (
        projector["exactlyOnceMechanism"]
        == "idempotent-state-projection-not-stale-delivery-guarantee"
    )
    assert topology["restrictions"]["noBrokerProvisioning"] is True
    assert topology["restrictions"]["noBrokerConnection"] is True
    assert topology["restrictions"]["noTimers"] is True
