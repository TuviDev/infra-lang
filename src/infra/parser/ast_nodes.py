"""Abstract Syntax Tree node definitions for the Infra Language.

All nodes are frozen dataclasses deriving from :class:`ASTNode`, which carries
a source location. Collections are stored as tuples to guarantee immutability.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple, Union

from infra.parser.location import SourceLocation

# ---------------------------------------------------------------------------
# Location & base
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class ASTNode:
    """Base class for every AST node.

    ``location`` is keyword-only so subclass field ordering is unaffected.
    """

    location: Optional[SourceLocation] = None


# ---------------------------------------------------------------------------
# Expressions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Literal(ASTNode):
    """A literal value (str, int, float, bool or None)."""

    value: object


@dataclass(frozen=True)
class Identifier(ASTNode):
    """A reference to a named symbol."""

    name: str


@dataclass(frozen=True)
class BinaryOp(ASTNode):
    """A binary operation such as ``a + b``."""

    left: Expression
    operator: str
    right: Expression


@dataclass(frozen=True)
class UnaryOp(ASTNode):
    """A unary operation such as ``-x`` or ``!ok``."""

    operator: str
    operand: Expression


@dataclass(frozen=True)
class Call(ASTNode):
    """A function call: ``callee(args, kw=value)``."""

    callee: Expression
    args: Tuple[Expression, ...] = ()
    kwargs: Tuple[Tuple[str, Expression], ...] = ()


@dataclass(frozen=True)
class Index(ASTNode):
    """An index access: ``obj[key]``."""

    obj: Expression
    index: Expression


@dataclass(frozen=True)
class Attribute(ASTNode):
    """An attribute access: ``obj.attr``."""

    obj: Expression
    attr: str


@dataclass(frozen=True)
class List(ASTNode):
    """A list literal ``[a, b, c]``."""

    items: Tuple[Expression, ...] = ()


@dataclass(frozen=True)
class MapEntry:
    """A single key/value pair within a map literal."""

    key: Expression
    value: Expression


@dataclass(frozen=True)
class Map(ASTNode):
    """A map literal ``{key: value, ...}``."""

    entries: Tuple[MapEntry, ...] = ()


@dataclass(frozen=True)
class TemplateString(ASTNode):
    """A template string such as ```Hello {name}`` ``.

    ``parts`` alternates between literal string pieces and expression nodes.
    Literal pieces are ``str``, expressions are :class:`Expression`.
    """

    parts: Tuple[Union[str, "Expression"], ...] = ()


@dataclass(frozen=True)
class IfExpr(ASTNode):
    """An if-expression: ``if c then a else b``."""

    condition: Expression
    then_branch: Expression
    else_branch: Expression


@dataclass(frozen=True)
class MatchArm(ASTNode):
    """A single ``pattern -> body`` arm in a match expression."""

    pattern: "PatternValue"
    body: Expression


@dataclass(frozen=True)
class MatchExpr(ASTNode):
    """A pattern-matching expression: ``match x { ... }``."""

    subject: Expression
    arms: Tuple[MatchArm, ...] = ()


@dataclass(frozen=True)
class Duration(ASTNode):
    """A time duration such as ``30s`` or ``2h``."""

    value: float
    unit: str

    def to_seconds(self) -> float:
        factors = {
            "ms": 0.001,
            "s": 1.0,
            "min": 60.0,
            "h": 3600.0,
            "d": 86400.0,
            "w": 604800.0,
            # legacy "m" minutes, kept for backward compat
            "m": 60.0,
        }
        return self.value * factors.get(self.unit, 1.0)


@dataclass(frozen=True)
class ResourceValue(ASTNode):
    """A Kubernetes-style resource value such as ``128Mi`` or ``500m``."""

    value: float
    unit: str

    def to_kubernetes(self) -> str:
        """Return the canonical Kubernetes string (e.g. ``128Mi``, ``500m``)."""
        if self.unit in ("Ki", "Mi", "Gi", "Ti"):
            return f"{int(self.value)}{self.unit}"
        if self.value == int(self.value):
            return f"{int(self.value)}{self.unit}"
        return f"{self.value}{self.unit}"

    def to_bytes(self) -> int:
        """Return the value in bytes for memory units (Ki/Mi/Gi/Ti)."""
        factors = {
            "Ki": 1024,
            "Mi": 1024**2,
            "Gi": 1024**3,
            "Ti": 1024**4,
        }
        if self.unit in factors:
            return int(self.value * factors[self.unit])
        # cpu milli / nano are not bytes; return the raw numeric value
        return int(self.value)


@dataclass(frozen=True)
class Percentage(ASTNode):
    """A percentage value such as ``50%``."""

    value: float


#: All node types that can appear as an expression.
Expression = Union[
    Literal,
    Identifier,
    BinaryOp,
    UnaryOp,
    Call,
    Index,
    Attribute,
    List,
    Map,
    TemplateString,
    IfExpr,
    MatchExpr,
    Duration,
    ResourceValue,
    Percentage,
]

#: A value usable as a match pattern (literal, identifier, or wildcard).
PatternValue = Union[Literal, Identifier, None]  # None == wildcard `_`


# ---------------------------------------------------------------------------
# Declarations
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Import(ASTNode):
    """An ``import`` or ``from ... import ...`` statement."""

    path: str
    alias: Optional[str] = None
    names: Tuple[str, ...] = ()


@dataclass(frozen=True)
class VariableDecl(ASTNode):
    """A ``let``/``const`` variable declaration."""

    name: str
    value: Expression
    const: bool = False


@dataclass(frozen=True)
class Decorator(ASTNode):
    """A decorator such as ``@deploy(replicas=3)``."""

    name: str
    args: Tuple[Expression, ...] = ()
    kwargs: Tuple[Tuple[str, Expression], ...] = ()


@dataclass(frozen=True)
class Program(ASTNode):
    """The root node: a sequence of statements and definitions."""

    statements: Tuple[Union["Statement", "Definition"], ...] = ()
    imports: Tuple[Import, ...] = ()


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BuildSpec(ASTNode):
    """A container build specification."""

    context: Optional[str] = None
    dockerfile: Optional[str] = None
    target: Optional[str] = None
    args: Tuple[Tuple[str, Expression], ...] = ()


@dataclass(frozen=True)
class RateLimitSpec(ASTNode):
    """Ingress rate-limit configuration."""

    rps: Optional[float] = None
    burst: Optional[int] = None


@dataclass(frozen=True)
class CorsSpec(ASTNode):
    """Ingress CORS configuration."""

    origins: Tuple[str, ...] = ()
    methods: Tuple[str, ...] = ()
    headers: Tuple[str, ...] = ()
    credentials: Optional[bool] = None


@dataclass(frozen=True)
class IngressSpec(ASTNode):
    """Ingress (HTTP routing) configuration for a service."""

    host: Optional[str] = None
    domain: Optional[str] = None
    tls: bool = False
    paths: Tuple[str, ...] = ()
    rate_limit: Optional[RateLimitSpec] = None
    cors: Optional[CorsSpec] = None


@dataclass(frozen=True)
class PortSpec(ASTNode):
    """A container port mapping."""

    host: Optional[int] = None
    target: Optional[int] = None
    protocol: Optional[str] = None


@dataclass(frozen=True)
class EnvEntry(ASTNode):
    """A single environment-variable entry for a service.

    Exactly one of the value/reference fields is set.
    """

    name: str
    value: Optional[Expression] = None
    from_secret: Optional[str] = None  # "secret_name" or "secret_name.key"
    from_config: Optional[str] = None
    from_field: Optional[str] = None
    from_env: Optional[str] = None


@dataclass(frozen=True)
class EnvFromSpec(ASTNode):
    """A bulk ``envFrom`` source (ConfigMap or Secret)."""

    source: str
    kind: str = "configmap"


@dataclass(frozen=True)
class ResourceMap(ASTNode):
    """Requested or limited resource amounts."""

    cpu: Optional[ResourceValue] = None
    memory: Optional[ResourceValue] = None


@dataclass(frozen=True)
class ResourcesSpec(ASTNode):
    """Container resource requests and limits."""

    requests: Optional[ResourceMap] = None
    limits: Optional[ResourceMap] = None


@dataclass(frozen=True)
class HealthSpec(ASTNode):
    """A health-check / probe specification."""

    kind: str = "http"  # http | tcp | exec | grpc
    path: Optional[str] = None
    port: Optional[int] = None
    command: Tuple[str, ...] = ()
    interval: Optional[Duration] = None
    timeout: Optional[Duration] = None
    retries: Optional[int] = None
    start_period: Optional[Duration] = None
    initial_delay: Optional[Duration] = None


@dataclass(frozen=True)
class ProbesSpec(ASTNode):
    """liveness / readiness / startup probe definitions."""

    liveness: Optional[HealthSpec] = None
    readiness: Optional[HealthSpec] = None
    startup: Optional[HealthSpec] = None


@dataclass(frozen=True)
class VolumeSpec(ASTNode):
    """A volume mounted into a container."""

    name: str
    mount_path: Optional[str] = None
    host_path: Optional[str] = None
    claim: Optional[str] = None
    storage_class: Optional[str] = None
    size: Optional[ResourceValue] = None
    read_only: bool = False


@dataclass(frozen=True)
class CanaryStep(ASTNode):
    """A single canary deployment step."""

    weight: Optional[int] = None
    steps: Optional[int] = None
    traffic: Optional[float] = None


@dataclass(frozen=True)
class StrategySpec(ASTNode):
    """Deployment strategy."""

    type: str = "rolling"  # rolling | recreate | blue_green | canary
    steps: Tuple[int, ...] = ()
    canary: Tuple[CanaryStep, ...] = ()


@dataclass(frozen=True)
class SelinuxSpec(ASTNode):
    """SELinux security options."""

    level: Optional[str] = None
    role: Optional[str] = None
    type: Optional[str] = None


@dataclass(frozen=True)
class SecuritySpec(ASTNode):
    """Container security context."""

    user: Optional[int] = None
    group: Optional[int] = None
    capabilities: Tuple[str, ...] = ()
    seccomp: Optional[str] = None
    selinux: Optional[SelinuxSpec] = None
    read_only_root_filesystem: bool = False
    privileged: bool = False


@dataclass(frozen=True)
class HookSpec(ASTNode):
    """A lifecycle hook (exec / http)."""

    kind: str = "exec"
    command: Tuple[str, ...] = ()
    url: Optional[str] = None


@dataclass(frozen=True)
class LifecycleSpec(ASTNode):
    """Container lifecycle hooks."""

    post_start: Optional[HookSpec] = None
    pre_stop: Optional[HookSpec] = None


@dataclass(frozen=True)
class ScheduleConfig(ASTNode):
    """Resource settings for a time-based scaling slot."""

    replicas: int = 2
    cpu: Optional[ResourceValue] = None
    memory: Optional[ResourceValue] = None


@dataclass(frozen=True)
class ScheduleSlot(ASTNode):
    """A single cron-triggered scaling configuration."""

    cron: Optional[str]
    config: ScheduleConfig


@dataclass(frozen=True)
class ScheduleSpec(ASTNode):
    """Time-based scaling schedule for a service."""

    default: Optional[ScheduleConfig] = None
    slots: Tuple[ScheduleSlot, ...] = ()


@dataclass(frozen=True)
class AutoscaleSpec(ASTNode):
    """Horizontal Pod Autoscaler configuration."""

    min_replicas: int = 1
    max_replicas: int = 10
    target_cpu: int = 70
    target_memory: Optional[int] = None
    scale_up_delay: Optional[Duration] = None
    scale_down_delay: Optional[Duration] = None


@dataclass(frozen=True)
class DisruptionSpec(ASTNode):
    """PodDisruptionBudget configuration."""

    min_available: Optional[Union[int, str]] = None
    max_unavailable: Optional[Union[int, str]] = None


@dataclass(frozen=True)
class NetworkPolicySpec(ASTNode):
    """Per-service network policy."""

    allow_from: Tuple[str, ...] = ()
    deny_from: Tuple[str, ...] = ()
    allow_egress: Tuple[str, ...] = ()


@dataclass(frozen=True)
class TopologySpec(ASTNode):
    """Topology spread constraints for a Deployment."""

    spread_by: str = "zone"
    max_skew: int = 1


@dataclass(frozen=True)
class AffinitySpec(ASTNode):
    """Pod affinity / anti-affinity preferences for a Deployment."""

    prefer_same: Tuple[str, ...] = ()
    avoid_same: Tuple[str, ...] = ()


@dataclass(frozen=True)
class QuotaSpec(ASTNode):
    """ResourceQuota limits for an environment namespace."""

    max_cpu: Optional[ResourceValue] = None
    max_memory: Optional[ResourceValue] = None
    max_pods: Optional[int] = None


@dataclass(frozen=True)
class ServiceDef(ASTNode):
    """A top-level ``service`` definition."""

    name: str
    extends: Optional[str] = None
    image: Optional[str] = None
    build: Optional[BuildSpec] = None
    replicas: int = 1
    ports: Tuple[PortSpec, ...] = ()
    env: Tuple[EnvEntry, ...] = ()
    env_from: Tuple[EnvFromSpec, ...] = ()
    command: Tuple[str, ...] = ()
    args: Tuple[str, ...] = ()
    resources: Optional[ResourcesSpec] = None
    health: Optional[HealthSpec] = None
    probes: Optional[ProbesSpec] = None
    volumes: Tuple[VolumeSpec, ...] = ()
    depends: Tuple[str, ...] = ()
    labels: Tuple[Tuple[str, str], ...] = ()
    annotations: Tuple[Tuple[str, str], ...] = ()
    strategy: Optional[StrategySpec] = None
    security: Optional[SecuritySpec] = None
    lifecycle: Optional[LifecycleSpec] = None
    ingress: Optional[IngressSpec] = None
    schedule: Optional[ScheduleSpec] = None
    autoscale: Optional[AutoscaleSpec] = None
    disruption: Optional[DisruptionSpec] = None
    network_policy: Optional[NetworkPolicySpec] = None
    topology: Optional[TopologySpec] = None
    affinity: Optional[AffinitySpec] = None
    expose: bool = False
    network: Optional[str] = None
    #: Extra fields that don't map to a known attribute (kept for formatter).
    extra: Tuple[Tuple[str, Expression], ...] = ()
    decorators: Tuple[Decorator, ...] = ()


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


class DatabaseType(str, Enum):
    POSTGRES = "postgres"
    MYSQL = "mysql"
    MONGODB = "mongodb"
    REDIS = "redis"
    MARIADB = "mariadb"
    SQLITE = "sqlite"


@dataclass(frozen=True)
class BackupSpec(ASTNode):
    """Database backup configuration."""

    enabled: bool = False
    schedule: Optional[str] = None
    retention: Optional[Duration] = None
    storage: Optional[str] = None


@dataclass(frozen=True)
class DbUserSpec(ASTNode):
    """A database user."""

    name: str
    password: Optional[str] = None


@dataclass(frozen=True)
class DatabaseDef(ASTNode):
    """A top-level ``database`` definition."""

    name: str
    type: str = "postgres"
    version: Optional[str] = None
    replicas: int = 1
    ha: bool = False
    ssl: Optional[bool] = None
    size: Optional[ResourceValue] = None
    storage: Optional[ResourceValue] = None
    backup: Optional[BackupSpec] = None
    users: Tuple[DbUserSpec, ...] = ()
    extra: Tuple[Tuple[str, Expression], ...] = ()
    decorators: Tuple[Decorator, ...] = ()


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


class CacheType(str, Enum):
    REDIS = "redis"
    VALKEY = "valkey"
    MEMCACHED = "memcached"


@dataclass(frozen=True)
class CacheDef(ASTNode):
    """A top-level ``cache`` definition."""

    name: str
    type: str = "redis"
    version: Optional[str] = None
    maxmemory: Optional[ResourceValue] = None
    policy: Optional[str] = None
    persistence: bool = False
    replicas: int = 1
    extra: Tuple[Tuple[str, Expression], ...] = ()
    decorators: Tuple[Decorator, ...] = ()


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------


class QueueType(str, Enum):
    RABBITMQ = "rabbitmq"
    KAFKA = "kafka"
    NATS = "nats"


@dataclass(frozen=True)
class TopicSpec(ASTNode):
    """A Kafka topic (or RabbitMQ queue) definition."""

    name: str
    partitions: Optional[int] = None
    replication: Optional[int] = None
    retention: Optional[Duration] = None


@dataclass(frozen=True)
class QueueConfigSpec(ASTNode):
    """Arbitrary queue configuration entries."""

    entries: Tuple[Tuple[str, Expression], ...] = ()


@dataclass(frozen=True)
class MqUserSpec(ASTNode):
    """A message-queue user."""

    name: str
    password: Optional[str] = None


@dataclass(frozen=True)
class QueueDef(ASTNode):
    """A top-level ``queue`` definition."""

    name: str
    type: str = "rabbitmq"
    version: Optional[str] = None
    replicas: int = 1
    topics: Tuple[TopicSpec, ...] = ()
    config: Optional[QueueConfigSpec] = None
    users: Tuple[MqUserSpec, ...] = ()
    extra: Tuple[Tuple[str, Expression], ...] = ()
    decorators: Tuple[Decorator, ...] = ()


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


class StorageType(str, Enum):
    S3 = "s3"
    GCS = "gcs"
    AZURE_BLOB = "azure_blob"
    MINIO = "minio"
    PVC = "pvc"
    EFS = "efs"


class AccessMode(str, Enum):
    READ_WRITE_ONCE = "ReadWriteOnce"
    READ_ONLY_MANY = "ReadOnlyMany"
    READ_WRITE_MANY = "ReadWriteMany"


@dataclass(frozen=True)
class StorageLifecycle(ASTNode):
    """Storage object lifecycle rules."""

    retention: Optional[Duration] = None
    prefix: Optional[str] = None
    transition: Optional[str] = None
    expiration: Optional[Duration] = None


@dataclass(frozen=True)
class StorageDef(ASTNode):
    """A top-level ``storage`` definition."""

    name: str
    type: str = "s3"
    size: Optional[ResourceValue] = None
    storage_class: Optional[str] = None
    access_mode: Optional[str] = None
    bucket: Optional[str] = None
    region: Optional[str] = None
    lifecycle: Optional[StorageLifecycle] = None
    extra: Tuple[Tuple[str, Expression], ...] = ()
    decorators: Tuple[Decorator, ...] = ()


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SubnetSpec(ASTNode):
    """A subnet within a network."""

    name: str
    cidr: Optional[str] = None
    az: Optional[str] = None


@dataclass(frozen=True)
class PolicyRule(ASTNode):
    """A single network-policy rule."""

    name: str
    from_: Optional[str] = None
    to: Optional[str] = None
    ports: Tuple[int, ...] = ()
    selector: Tuple[Tuple[str, str], ...] = ()


@dataclass(frozen=True)
class NetworkPolicy(ASTNode):
    """A collection of network-policy rules."""

    rules: Tuple[PolicyRule, ...] = ()


@dataclass(frozen=True)
class NetworkDef(ASTNode):
    """A top-level ``network`` definition."""

    name: str
    cidr: Optional[str] = None
    subnets: Tuple[SubnetSpec, ...] = ()
    policy: Optional[NetworkPolicy] = None
    extra: Tuple[Tuple[str, Expression], ...] = ()
    decorators: Tuple[Decorator, ...] = ()


# ---------------------------------------------------------------------------
# Secret & Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SecretEntry(ASTNode):
    """A single entry within a ``secret`` definition.

    Exactly one source field is set; otherwise ``value`` holds a literal.
    """

    name: str
    value: Optional[str] = None
    from_env: Optional[str] = None
    from_file: Optional[str] = None
    from_vault: Optional[str] = None
    from_aws: Optional[str] = None
    from_gcp: Optional[str] = None
    key: Optional[str] = None


@dataclass(frozen=True)
class SecretDef(ASTNode):
    """A top-level ``secret`` definition."""

    name: str
    entries: Tuple[SecretEntry, ...] = ()
    decorators: Tuple[Decorator, ...] = ()


@dataclass(frozen=True)
class ConfigEntry(ASTNode):
    """A single entry within a ``config`` definition."""

    name: str
    value: Optional[Expression] = None
    from_file: Optional[str] = None


@dataclass(frozen=True)
class ConfigDef(ASTNode):
    """A top-level ``config`` definition."""

    name: str
    entries: Tuple[ConfigEntry, ...] = ()
    decorators: Tuple[Decorator, ...] = ()


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TriggerSpec(ASTNode):
    """CI/CD pipeline triggers."""

    branches: Tuple[str, ...] = ()
    tags: Tuple[str, ...] = ()
    paths: Tuple[str, ...] = ()
    schedule: Optional[str] = None
    manual: bool = False
    events: Tuple[str, ...] = ()


@dataclass(frozen=True)
class MatrixSpec(ASTNode):
    """Build/test matrix dimensions."""

    dimensions: Tuple[Tuple[str, Tuple[str, ...]], ...] = ()


@dataclass(frozen=True)
class StepSpec(ASTNode):
    """A single CI step."""

    name: str
    run: Optional[str] = None
    uses: Optional[str] = None
    with_args: Tuple[Tuple[str, Expression], ...] = ()
    condition: Optional[str] = None
    continue_on_error: bool = False
    timeout: Optional[Duration] = None
    env: Tuple[Tuple[str, str], ...] = ()


@dataclass(frozen=True)
class StageSpec(ASTNode):
    """A pipeline stage (job)."""

    name: str
    image: Optional[str] = None
    runs_on: Optional[str] = None
    needs: Tuple[str, ...] = ()
    condition: Optional[str] = None
    env: Tuple[Tuple[str, str], ...] = ()
    timeout: Optional[Duration] = None
    matrix: Optional[MatrixSpec] = None
    parallel: Tuple[StageSpec, ...] = ()
    steps: Tuple[StepSpec, ...] = ()


@dataclass(frozen=True)
class ArtifactsSpec(ASTNode):
    """Artifact upload/download configuration."""

    upload: Tuple[str, ...] = ()
    download: Tuple[str, ...] = ()


@dataclass(frozen=True)
class PipelineCacheSpec(ASTNode):
    """Cache configuration for a pipeline."""

    path: Optional[str] = None
    key: Optional[str] = None
    restore_keys: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ConcurrencySpec(ASTNode):
    """Pipeline concurrency control."""

    group: Optional[str] = None
    cancel_in_progress: bool = False


@dataclass(frozen=True)
class PipelineDef(ASTNode):
    """A top-level ``pipeline`` definition."""

    name: str
    trigger: Optional[TriggerSpec] = None
    stages: Tuple[StageSpec, ...] = ()
    artifacts: Optional[ArtifactsSpec] = None
    cache: Optional[PipelineCacheSpec] = None
    concurrency: Optional[ConcurrencySpec] = None
    extra: Tuple[Tuple[str, Expression], ...] = ()
    decorators: Tuple[Decorator, ...] = ()


# ---------------------------------------------------------------------------
# Environment & Cluster
# ---------------------------------------------------------------------------


class CloudProvider(str, Enum):
    AWS = "aws"
    GCP = "gcp"
    AZURE = "azure"


@dataclass(frozen=True)
class EnvironmentDef(ASTNode):
    """A top-level ``environment`` definition."""

    name: str
    extends: Optional[str] = None
    provider: Optional[str] = None
    region: Optional[str] = None
    resources: Optional[ResourcesSpec] = None
    namespace: Optional[str] = None
    quotas: Optional[QuotaSpec] = None
    labels: Tuple[Tuple[str, str], ...] = ()
    extra: Tuple[Tuple[str, Expression], ...] = ()
    decorators: Tuple[Decorator, ...] = ()


@dataclass(frozen=True)
class NodePoolSpec(ASTNode):
    """A Kubernetes node pool."""

    name: str
    machine_type: Optional[str] = None
    min: Optional[int] = None
    max: Optional[int] = None
    labels: Tuple[Tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ClusterNetworkingSpec(ASTNode):
    """Cluster networking options."""

    cidr: Optional[str] = None
    vpc: Optional[str] = None


@dataclass(frozen=True)
class ServiceAccountSpec(ASTNode):
    """A Kubernetes service account."""

    name: Optional[str] = None
    policy: Tuple[Tuple[str, str], ...] = ()


@dataclass(frozen=True)
class RoleSpec(ASTNode):
    """An IAM role definition."""

    name: Optional[str] = None
    actions: Tuple[str, ...] = ()
    resources: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ClusterIamSpec(ASTNode):
    """Cluster IAM configuration."""

    service_account: Optional[ServiceAccountSpec] = None
    role: Optional[RoleSpec] = None


@dataclass(frozen=True)
class ClusterDef(ASTNode):
    """A top-level ``cluster`` definition."""

    name: str
    provider: Optional[str] = None
    region: Optional[str] = None
    version: Optional[str] = None
    nodes: Tuple[NodePoolSpec, ...] = ()
    networking: Optional[ClusterNetworkingSpec] = None
    iam: Optional[ClusterIamSpec] = None
    extra: Tuple[Tuple[str, Expression], ...] = ()
    decorators: Tuple[Decorator, ...] = ()


# ---------------------------------------------------------------------------
# Enums (domain-specific)
# ---------------------------------------------------------------------------


class Protocol(str, Enum):
    TCP = "TCP"
    UDP = "UDP"
    SCTP = "SCTP"
    HTTP = "HTTP"
    HTTPS = "HTTPS"


class VolumeType(str, Enum):
    EMPTY_DIR = "emptyDir"
    HOST_PATH = "hostPath"
    PERSISTENT_VOLUME_CLAIM = "pvc"
    CONFIG_MAP = "configMap"
    SECRET = "secret"


class StrategyType(str, Enum):
    ROLLING = "rolling"
    RECREATE = "recreate"
    BLUE_GREEN = "blue_green"
    CANARY = "canary"


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

#: Every statement/declaration that can appear at the top level.
Statement = Union[Import, VariableDecl]
Definition = Union[
    ServiceDef,
    DatabaseDef,
    CacheDef,
    QueueDef,
    StorageDef,
    NetworkDef,
    SecretDef,
    ConfigDef,
    PipelineDef,
    EnvironmentDef,
    ClusterDef,
]
