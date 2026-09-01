"""JSON Schema (draft-07) export of the ``.infra`` DSL — v0.7.1.

The ``.infra`` language is a textual DSL, but editors and external tools
commonly integrate via a JSON Schema describing the language's conceptual
document model: the set of top-level blocks (``service``, ``database``,
``environment``, ``network_policy``, ``secret_store`` …) and each block's
documented properties. :func:`build_schema` returns that schema as a plain
dict (serializable with ``json.dumps``); the ``infra schema`` CLI command
writes it to a file or stdout.

The field inventory mirrors ``infra.parser.ast_nodes`` (block kinds from
``lexer/grammar.lark``'s top-level ``definition`` rule, property keys from
the ``*Def`` dataclasses). Property values are typed permissively where the
DSL accepts full expressions (``value`` definition), while enums and
well-known shapes are pinned exactly.
"""

from __future__ import annotations

from typing import Any, Dict

SCHEMA_ID = "https://infra-lang.dev/schemas/infra.schema.json"
SCHEMA_DRAFT = "http://json-schema.org/draft-07/schema#"

#: A DSL expression value: literal, number, boolean, list or map (the AST
#: folds arithmetic/templates/match-expressions into these JSON shapes).
_VALUE: Dict[str, Any] = {
    "description": "Any .infra expression value (literal, number, boolean, "
    "duration like '30s', resource like '500m'/ '512Mi', list or map).",
    "oneOf": [
        {"type": "string"},
        {"type": "number"},
        {"type": "boolean"},
        {"type": "array", "items": {"$ref": "#/definitions/value"}},
        {
            "type": "object",
            "additionalProperties": {"$ref": "#/definitions/value"},
        },
    ],
}

_DEFINITIONS: Dict[str, Any] = {
    "value": _VALUE,
    "stringMap": {
        "type": "object",
        "additionalProperties": {"$ref": "#/definitions/value"},
    },
    "port": {
        "type": "object",
        "description": " port mapping (container/service/host ports, protocol).",
        "properties": {
            "port": {"type": "integer", "minimum": 1, "maximum": 65535},
            "target": {"type": "integer", "minimum": 1, "maximum": 65535},
            "host": {"type": "integer", "minimum": 1, "maximum": 65535},
            "protocol": {"type": "string", "enum": ["tcp", "udp", "http", "grpc"]},
        },
        "additionalProperties": True,
    },
    "service": {
        "type": "object",
        "description": "A container workload definition (service block).",
        "required": ["name"],
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "extends": {"type": "string"},
            "image": {
                "type": "string",
                "description": "Container image, e.g. 'nginx:1.25'.",
            },
            "build": {
                "type": ["object", "string"],
                "description": "Build context or {context, dockerfile, args} block.",
            },
            "replicas": {"type": "integer", "minimum": 0},
            "ports": {"type": "array", "items": {"$ref": "#/definitions/port"}},
            "env": {"$ref": "#/definitions/stringMap"},
            "env_from": {
                "type": "object",
                "properties": {
                    "config": {"type": "string"},
                    "secret": {"type": "string"},
                    "prefix": {"type": "string"},
                },
                "additionalProperties": True,
            },
            "command": {"type": "array", "items": {"type": "string"}},
            "args": {"type": "array", "items": {"type": "string"}},
            "resources": {
                "type": "object",
                "properties": {
                    "cpu": {"$ref": "#/definitions/value"},
                    "memory": {"$ref": "#/definitions/value"},
                    "requests": {"$ref": "#/definitions/stringMap"},
                    "limits": {"$ref": "#/definitions/stringMap"},
                },
                "additionalProperties": True,
            },
            "health": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "port": {"type": "integer"},
                    "interval": {"$ref": "#/definitions/value"},
                    "timeout": {"$ref": "#/definitions/value"},
                    "retries": {"type": "integer", "minimum": 1},
                },
                "additionalProperties": True,
            },
            "probes": {"type": "object", "additionalProperties": True},
            "volumes": {"type": "array", "items": {"type": "object"}},
            "depends": {"type": "array", "items": {"type": "string"}},
            "depends_on": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Compose-style dependency list (other block names).",
            },
            "labels": {"$ref": "#/definitions/stringMap"},
            "annotations": {"$ref": "#/definitions/stringMap"},
            "strategy": {"type": "object", "additionalProperties": True},
            "security": {"type": "object", "additionalProperties": True},
            "lifecycle": {"type": "object", "additionalProperties": True},
            "ingress": {
                "type": "object",
                "properties": {
                    "host": {"type": "string"},
                    "domain": {"type": "string"},
                    "tls": {"type": "boolean"},
                    "path": {"type": "string"},
                    "rate_limit": {"type": "object"},
                    "cors": {"type": "object"},
                },
                "additionalProperties": True,
            },
            "schedule": {"type": "object", "additionalProperties": True},
            "autoscale": {
                "type": "object",
                "properties": {
                    "min": {"type": "integer", "minimum": 0},
                    "max": {"type": "integer", "minimum": 1},
                    "target_cpu": {"type": "integer"},
                },
                "additionalProperties": True,
            },
            "disruption": {"type": "object", "additionalProperties": True},
            "network_policy": {"type": "object", "additionalProperties": True},
            "topology": {"type": "object", "additionalProperties": True},
            "affinity": {"type": "object", "additionalProperties": True},
            "expose": {"type": "object", "additionalProperties": True},
            "network": {"type": "string", "description": "Name of a network block."},
        },
        "additionalProperties": True,
    },
    "database": {
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "type": {
                "type": "string",
                "enum": ["postgres", "mysql", "mongodb", "redis", "mariadb", "sqlite"],
            },
            "version": {"type": "string"},
            "replicas": {"type": "integer", "minimum": 0},
            "ha": {"type": "boolean"},
            "ssl": {"type": "boolean"},
            "size": {"$ref": "#/definitions/value"},
            "storage": {"$ref": "#/definitions/value"},
            "backup": {"type": "object", "additionalProperties": True},
            "users": {"type": "array", "items": {"type": "object"}},
        },
        "additionalProperties": True,
    },
    "cache": {
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "type": {"type": "string", "enum": ["redis", "valkey", "memcached"]},
            "version": {"type": "string"},
            "maxmemory": {"$ref": "#/definitions/value"},
            "policy": {"type": "string"},
            "persistence": {"type": "boolean"},
            "replicas": {"type": "integer", "minimum": 0},
        },
        "additionalProperties": True,
    },
    "queue": {
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "type": {"type": "string", "enum": ["rabbitmq", "kafka", "nats"]},
            "version": {"type": "string"},
            "replicas": {"type": "integer", "minimum": 0},
            "topics": {"type": "array", "items": {"type": "object"}},
            "config": {"type": "object", "additionalProperties": True},
            "users": {"type": "array", "items": {"type": "object"}},
        },
        "additionalProperties": True,
    },
    "storage": {
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "type": {
                "type": "string",
                "enum": ["s3", "gcs", "azure_blob", "minio", "pvc", "efs"],
            },
            "size": {"$ref": "#/definitions/value"},
            "storage_class": {"type": "string"},
            "access_mode": {
                "type": "string",
                "enum": ["ReadWriteOnce", "ReadOnlyMany", "ReadWriteMany"],
            },
            "bucket": {"type": "string"},
            "region": {"type": "string"},
            "lifecycle": {"type": "object", "additionalProperties": True},
        },
        "additionalProperties": True,
    },
    "network": {
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "cidr": {"type": "string"},
            "subnets": {"type": "array", "items": {"type": "object"}},
            "policy": {"type": "object", "additionalProperties": True},
        },
        "additionalProperties": True,
    },
    "network_policy": {
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "target": {"type": "string"},
            "allow_ingress": {"type": "array", "items": {"type": "string"}},
            "allow_egress": {"type": "array", "items": {"type": "string"}},
            "block_all_ingress": {"type": "boolean"},
        },
        "additionalProperties": True,
    },
    "secret": {
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "entries": {"$ref": "#/definitions/stringMap"},
            "store": {"type": "string", "description": "Name of a secret_store."},
        },
        "additionalProperties": True,
    },
    "secret_store": {
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "provider": {
                "type": "string",
                "description": "e.g. aws, gcp, azure, vault.",
            },
            "address": {"type": "string"},
            "path": {"type": "string"},
            "region": {"type": "string"},
            "namespace": {"type": "string"},
            "project": {"type": "string"},
        },
        "additionalProperties": True,
    },
    "custom_resource": {
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "kind": {"type": "string", "description": "Kubernetes-style Kind name."},
            "properties": {"$ref": "#/definitions/stringMap"},
        },
        "additionalProperties": True,
    },
    "config": {
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "entries": {"$ref": "#/definitions/stringMap"},
        },
        "additionalProperties": True,
    },
    "pipeline": {
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "trigger": {"type": "object", "additionalProperties": True},
            "stages": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "steps": {"type": "array", "items": {"type": "object"}},
                        "matrix": {"type": "object"},
                    },
                    "additionalProperties": True,
                },
            },
            "artifacts": {"type": "array", "items": {"type": "string"}},
            "cache": {"type": "object", "additionalProperties": True},
            "concurrency": {"$ref": "#/definitions/value"},
        },
        "additionalProperties": True,
    },
    "environment": {
        "type": "object",
        "required": ["name"],
        "description": "Environment target or overlay (per-service overrides).",
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "extends": {"type": "string"},
            "provider": {"type": "string"},
            "region": {"type": "string"},
            "resources": {"type": "object", "additionalProperties": True},
            "namespace": {"type": "string"},
            "quotas": {"type": "object", "additionalProperties": True},
            "labels": {"$ref": "#/definitions/stringMap"},
        },
        "additionalProperties": True,
    },
    "cluster": {
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "provider": {"type": "string", "description": "e.g. eks, gke, aks, k3s."},
            "region": {"type": "string"},
            "version": {"type": "string"},
            "nodes": {"type": "object", "additionalProperties": True},
            "networking": {"type": "object", "additionalProperties": True},
            "iam": {"type": "object", "additionalProperties": True},
        },
        "additionalProperties": True,
    },
    "import": {
        "type": "object",
        "required": ["path"],
        "properties": {
            "path": {"type": "string", "minLength": 1},
            "alias": {"type": "string"},
            "names": {"type": "array", "items": {"type": "string"}},
        },
        "additionalProperties": True,
    },
    "variable": {
        "type": "object",
        "required": ["name", "value"],
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "value": {"$ref": "#/definitions/value"},
            "const": {"type": "boolean"},
        },
        "additionalProperties": True,
    },
}

#: Block kind → (collection key, definition ref).
_BLOCK_COLLECTIONS: Dict[str, str] = {
    "services": "service",
    "databases": "database",
    "caches": "cache",
    "queues": "queue",
    "storages": "storage",
    "networks": "network",
    "network_policies": "network_policy",
    "secrets": "secret",
    "secret_stores": "secret_store",
    "custom_resources": "custom_resource",
    "configs": "config",
    "pipelines": "pipeline",
    "environments": "environment",
    "clusters": "cluster",
}


def build_schema() -> Dict[str, Any]:
    """Return the complete draft-07 JSON Schema of the ``.infra`` DSL."""
    properties: Dict[str, Any] = {
        "imports": {
            "type": "array",
            "items": {"$ref": "#/definitions/import"},
            "description": "import/from-import statements.",
        },
        "variables": {
            "type": "array",
            "items": {"$ref": "#/definitions/variable"},
            "description": "let/const variable declarations.",
        },
    }
    for collection, definition in _BLOCK_COLLECTIONS.items():
        properties[collection] = {
            "type": "array",
            "items": {"$ref": f"#/definitions/{definition}"},
            "description": f"Top-level {definition} blocks.",
        }
    return {
        "$schema": SCHEMA_DRAFT,
        "$id": SCHEMA_ID,
        "title": "Infra Language document",
        "description": "JSON Schema (draft-07) of the .infra infrastructure DSL "
        "conceptual model — every top-level block and its documented properties.",
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
        "definitions": _DEFINITIONS,
    }
