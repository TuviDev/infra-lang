# ruff: noqa: E501
"""Terraform HCL backend (default provider: AWS).

Generates main.tf, variables.tf, outputs.tf, providers.tf, versions.tf and
terraform.tfvars.example from Infra definitions.
"""
# mypy: disable-error-code="no-untyped-def,no-untyped-call,no-any-return,index,attr-defined,type-arg,misc,union-attr,assignment"

from __future__ import annotations

from typing import List, Optional

from infra.backends.base import Backend, CompileResult, generated_header
from infra.parser import ast_nodes as n

_DB_ENGINES = {"postgres": "postgres", "mysql": "mysql", "mariadb": "mariadb"}
_RDS_IMAGES = {"postgres": "postgres", "mysql": "mysql", "mongodb": "mongodb"}


class TerraformBackend(Backend):
    name = "terraform"
    description = "Terraform HCL (AWS/GCP/Azure)"
    file_extension = ".tf"
    supports_multi_file = True

    def __init__(self, provider: str = "aws") -> None:
        self.provider = provider.lower()

    def get_version(self) -> str:
        return "1.5"

    def compile(self, program: n.Program, *, cli_vars=None, **kwargs) -> CompileResult:
        resources: List[str] = []
        variables: List[str] = []
        outputs: List[str] = []
        provider_conf = ""
        self._variables = variables
        # Detect provider from the first ClusterDef when not explicitly chosen.
        for stmt in program.statements:
            if isinstance(stmt, n.ClusterDef) and stmt.provider:
                self.provider = stmt.provider
                break
        result = CompileResult(metadata={"provider": self.provider})

        if self.provider == "aws":
            provider_conf = (
                'provider "aws" {\n'
                "  region = var.aws_region\n"
                "  default_tags {\n"
                "    tags = {\n"
                '      ManagedBy   = "infra-lang"\n'
                "      Environment = var.environment\n"
                "    }\n"
                "  }\n"
                "}\n"
            )
            variables.append('variable "aws_region" { default = "eu-west-1" }')
            variables.append('variable "environment" { default = "dev" }')
        elif self.provider == "gcp":
            provider_conf = (
                'provider "google" {\n'
                "  project = var.gcp_project\n"
                "  region  = var.gcp_region\n"
                "}\n"
            )
            variables.append('variable "gcp_project" { default = "my-project" }')
            variables.append('variable "gcp_region" { default = "europe-west1" }')
            variables.append('variable "environment" { default = "dev" }')
        elif self.provider == "azure":
            provider_conf = 'provider "azurerm" {\n  features {}\n}\n'
            variables.append('variable "azure_location" { default = "westeurope" }')
            variables.append('variable "azure_resource_group" { default = "rg-infra" }')
            variables.append('variable "environment" { default = "dev" }')

        # v0.4.5: only when at least one service declares `depends_on` do we
        # materialize services as kubernetes_deployment resources, so that
        # Terraform can express the ordering. Programs without depends_on
        # produce byte-identical output to previous versions.
        deps_active = any(
            isinstance(s, n.ServiceDef) and s.depends_on for s in program.statements
        )
        # v0.5.0: secret stores pull in extra providers
        vault_store = next(
            (
                s
                for s in program.statements
                if isinstance(s, n.SecretStoreDef) and s.provider == "vault"
            ),
            None,
        )
        k8s_secret_store = any(
            isinstance(s, n.SecretStoreDef) and s.provider == "kubernetes"
            for s in program.statements
        )
        needs_kubernetes = deps_active or k8s_secret_store

        for stmt in program.statements:
            if isinstance(stmt, n.ClusterDef):
                resources.extend(self._cluster(stmt))
            elif isinstance(stmt, n.ServiceDef):
                if deps_active:
                    resources.append(self._service_deployment(stmt, program))
            elif isinstance(stmt, n.DatabaseDef):
                resources.extend(self._database(stmt))
                outputs.extend(self._database_outputs(stmt))
            elif isinstance(stmt, n.StorageDef):
                resources.extend(self._storage(stmt))
                outputs.extend(self._storage_outputs(stmt))
            elif isinstance(stmt, n.NetworkDef):
                if self.provider == "aws":
                    resources.extend(self._network(stmt))
            elif isinstance(stmt, n.SecretDef):
                resources.extend(self._secret(stmt, program))
            elif isinstance(stmt, n.CustomResourceSpec):
                # v0.5.0 plugin system: CRDs are Kubernetes manifests; there
                # is no generic Terraform representation, so we emit a clear
                # notice instead of silently dropping the declaration.
                result.warnings.append(
                    f"Custom resource '{stmt.name}' ({stmt.kind_name}) is only "
                    "supported by the kubernetes backend and was skipped."
                )
            elif isinstance(stmt, n.QueueDef):
                resources.extend(self._queue(stmt))

        hdr = generated_header("terraform")
        main = hdr + "\n".join(resources) + "\n"
        result.files["main.tf"] = main
        if needs_kubernetes:
            provider_conf += 'provider "kubernetes" {}\n'
        if vault_store is not None:
            address = vault_store.address or "http://127.0.0.1:8200"
            provider_conf += (
                'provider "vault" {\n'
                f'  address = "{address}"\n'
                "}\n"
            )
        result.files["providers.tf"] = hdr + provider_conf
        result.files["variables.tf"] = hdr + "\n".join(variables) + "\n"
        result.files["outputs.tf"] = hdr + "\n".join(outputs) + "\n"
        required = self._required_providers()
        if needs_kubernetes:
            required += (
                '    kubernetes = { source = "hashicorp/kubernetes",'
                ' version = "~> 2.0" }\n'
            )
        if vault_store is not None:
            required += (
                '    vault = { source = "hashicorp/vault", version = "~> 4.0" }\n'
            )
        result.files["versions.tf"] = (
            hdr
            + "terraform {\n"
            '  required_version = ">= 1.5"\n'
            f"  required_providers {{\n{required}  }}\n"
            "}\n"
        )
        result.files["terraform.tfvars.example"] = hdr + self._tfvars_example()
        return result

    def _required_providers(self) -> str:
        if self.provider == "aws":
            return '    aws = { source = "hashicorp/aws" }\n'
        if self.provider == "gcp":
            return '    google = { source = "hashicorp/google", version = "~> 4.0" }\n'
        return '    azurerm = { source = "hashicorp/azurerm", version = "~> 3.0" }\n'

    def _tfvars_example(self) -> str:
        if self.provider == "aws":
            return 'aws_region = "eu-west-1"\nenvironment = "dev"\n'
        if self.provider == "gcp":
            return 'gcp_project = "my-project"\ngcp_region = "europe-west1"\nenvironment = "dev"\n'  # noqa: E501
        return 'azure_location = "westeurope"\nazure_resource_group = "rg-infra"\nenvironment = "dev"\n'  # noqa: E501

    # ------------------------------------------------------------------ #
    def compile_service(self, node: n.ServiceDef) -> str:
        return "# services are deployed via Kubernetes in this backend\n"

    def _service_deployment(self, node: n.ServiceDef, program: n.Program) -> str:
        """Render a ``kubernetes_deployment`` resource for *node* (v0.4.5).

        Emitted only when the program declares at least one ``depends_on``
        edge, so Terraform can express the ordering. Service targets become
        ``kubernetes_deployment.<name>`` references, database targets map to
        the provider's database resource; targets without a Terraform
        resource in this backend are preserved as comments.
        """
        if isinstance(node.image, str):
            image = node.image
        elif node.build is not None:
            image = "built-from-dockerfile"
        else:
            image = "unknown"
        replicas = node.replicas if isinstance(node.replicas, int) else 1
        lines = [
            f'resource "kubernetes_deployment" "{node.name}" {{',
            "  metadata {",
            f'    name = "{node.name}"',
            "  }",
            "  spec {",
            f"    replicas = {replicas}",
            "    selector {",
            "      match_labels = {",
            f'        app = "{node.name}"',
            "      }",
            "    }",
            "    template {",
            "      metadata {",
            "        labels = {",
            f'          app = "{node.name}"',
            "        }",
            "      }",
            "      spec {",
            "        container {",
            f'          name  = "{node.name}"',
            f'          image = "{image}"',
            "        }",
            "      }",
            "    }",
            "  }",
        ]
        defs = {getattr(s, "name", ""): s for s in program.statements}
        refs: List[str] = []
        comments: List[str] = []
        for dep in node.dependencies:
            target = defs.get(dep)
            if isinstance(target, n.ServiceDef):
                refs.append(f"kubernetes_deployment.{dep}")
            elif isinstance(target, n.DatabaseDef):
                ref = self._database_tf_ref(target)
                if ref:
                    refs.append(ref)
                else:
                    comments.append(
                        f"depends_on target '{dep}' (database '{target.type}') "
                        f"has no Terraform resource for provider '{self.provider}'"
                    )
            elif target is not None:
                comments.append(
                    f"depends_on target '{dep}' has no Terraform resource "
                    "in this backend"
                )
            else:
                comments.append(f"depends_on target '{dep}' is not declared")
        if refs:
            lines.append("  depends_on = [")
            lines.extend(f"    {r}," for r in refs)
            lines.append("  ]")
        lines.extend(f"  # {c}" for c in comments)
        lines.append("}")
        return "\n".join(lines) + "\n"

    def _database_tf_ref(self, node: n.DatabaseDef) -> Optional[str]:
        """Terraform address of the provider resource backing *node*."""
        if self.provider == "gcp":
            if node.type in ("postgres", "mysql"):
                return f"google_sql_database_instance.{node.name}"
            return None
        if self.provider == "azure":
            if node.type == "postgres":
                return f"azurerm_postgresql_server.{node.name}"
            return None
        if node.type == "mongodb":
            return f"aws_docdb_cluster.{node.name}"
        return f"aws_db_instance.{node.name}"

    def compile_database(self, node: n.DatabaseDef) -> str:
        return "\n".join(self._database(node)) + "\n"

    def _cluster(self, node: n.ClusterDef) -> List[str]:
        if self.provider == "gcp":
            return self._gcp_cluster(node)
        if self.provider == "azure":
            return self._azure_cluster(node)
        return self._aws_cluster(node)

    def _aws_cluster(self, node: n.ClusterDef) -> List[str]:
        name = node.name
        region = node.region or "eu-west-1"
        out = [
            f'resource "aws_vpc" "{name}" {{\n  cidr_block = "10.0.0.0/16"\n}}\n',
            f'resource "aws_eks_cluster" "{name}" {{\n'
            f'  name     = "{name}"\n'
            f"  role_arn = aws_iam_role.{name}.arn\n"
            f"  vpc_config {{\n"
            f"    subnet_ids = [aws_subnet.{name}_a.id, aws_subnet.{name}_b.id]\n"
            f"  }}\n}}\n",
            f'resource "aws_iam_role" "{name}" {{\n  name = "{name}-cluster-role"\n}}\n',  # noqa: E501
        ]
        for np in node.nodes:
            nname = np.name
            out.append(
                f'resource "aws_eks_node_group" "{name}_{nname}" {{\n'
                f"  cluster_name  = aws_eks_cluster.{name}.name\n"
                f'  node_group_name = "{nname}"\n'
                f'  instance_types = ["{np.machine_type or "t3.medium"}"]\n'
                f"  scaling_config {{\n"
                f"    desired_size = {np.min or 1}\n"
                f"    max_size     = {np.max or 3}\n"
                f"    min_size     = {np.min or 1}\n"
                f"  }}\n"
                f"}}\n"
            )
        out.append(
            f'resource "aws_subnet" "{name}_a" {{\n  vpc_id = aws_vpc.{name}.id\n  cidr_block = "10.0.1.0/24"\n  availability_zone = "{region}a"\n}}\n'  # noqa: E501
        )
        out.append(
            f'resource "aws_subnet" "{name}_b" {{\n  vpc_id = aws_vpc.{name}.id\n  cidr_block = "10.0.2.0/24"\n  availability_zone = "{region}b"\n}}\n'  # noqa: E501
        )
        return out

    def _gcp_cluster(self, node: n.ClusterDef) -> List[str]:
        name = node.name
        out = [
            f'resource "google_container_cluster" "{name}" {{\n'
            f'  name     = "{name}"\n'
            "  location = var.gcp_region\n"
            "  deletion_protection = false\n",
        ]
        for np_ in node.nodes or [
            type(
                "X",
                (),
                {"name": "default", "machine_type": None, "min": None, "max": None},
            )()
        ]:
            out.append(
                f"  node_pool {{\n"
                f'    name = "{np_.name}"\n'
                f"    node_count = {np_.min or 1}\n"
                "    node_config {\n"
                f'      machine_type = "{np_.machine_type or "e2-standard-2"}"\n'
                "      oauth_scopes = [\n"
                '        "https://www.googleapis.com/auth/cloud-platform"\n'
                "      ]\n"
                "    }\n"
                "    autoscaling {\n"
                f"      min_node_count = {np_.min or 1}\n"
                f"      max_node_count = {np_.max or (np_.min or 1) * 3}\n"
                "    }\n"
                "  }\n"
            )
        out.append(
            "  resource_labels = {\n"
            '    managed-by  = "infra-lang"\n'
            "    environment = var.environment\n"
            "  }\n"
            "}\n"
        )
        return out

    def _azure_cluster(self, node: n.ClusterDef) -> List[str]:
        name = node.name
        out = [
            f'resource "azurerm_kubernetes_cluster" "{name}" {{\n'
            f'  name                = "{name}"\n'
            "  location            = var.azure_location\n"
            "  resource_group_name = var.azure_resource_group\n"
            f'  dns_prefix          = "{name}"\n',
        ]
        for np_ in node.nodes or [
            type(
                "X",
                (),
                {"name": "default", "machine_type": None, "min": None, "max": None},
            )()
        ]:
            out.append(
                "  default_node_pool {\n"
                f'    name       = "{np_.name}"\n'
                f"    node_count = {np_.min or 1}\n"
                f'    vm_size    = "{np_.machine_type or "Standard_D2_v2"}"\n'
                f"    enable_auto_scaling = {str(np_.min is not None).lower()}\n"
                f"    min_count  = {np_.min or 1}\n"
                f"    max_count  = {np_.max or 5}\n"
                "  }\n"
            )
        out.append(
            '  identity {\n    type = "SystemAssigned"\n  }\n'
            "  tags = {\n"
            '    ManagedBy   = "infra-lang"\n'
            "    Environment = var.environment\n"
            "  }\n"
            "}\n"
        )
        return out

    def _database(self, node: n.DatabaseDef) -> List[str]:
        if self.provider == "gcp":
            return self._gcp_database(node)
        if self.provider == "azure":
            return self._azure_database(node)
        if node.type == "mongodb":
            return [
                f'resource "aws_docdb_cluster" "{node.name}" {{\n'
                f'  cluster_identifier = "{node.name}"\n'
                f"}}\n"
            ]
        engine = _DB_ENGINES.get(node.type, "postgres")
        rds = (
            f'resource "aws_db_instance" "{node.name}" {{\n'
            f'  identifier     = "{node.name}"\n'
            f'  engine         = "{engine}"\n'
            f'  engine_version = "{node.version or "14"}"\n'
            f'  instance_class = "db.t3.micro"\n'
            f"  allocated_storage = 20\n"
        )
        if node.users:
            rds += f'  username = "{node.users[0].name}"\n'
            rds += f"  password = var.{node.name}_password\n"
            self._variables.append(
                f'variable "{node.name}_password" {{ sensitive = true }}'
            )
        rds += "}\n"
        return [rds]

    def _gcp_database(self, node: n.DatabaseDef) -> List[str]:
        if node.type not in ("postgres", "mysql"):
            return []
        db_version = "POSTGRES_15" if node.type == "postgres" else "MYSQL_8_0"
        backup = node.backup.enabled if node.backup else False
        out = [
            f'resource "google_sql_database_instance" "{node.name}" {{\n'
            f'  name             = "{node.name}"\n'
            f'  database_version = "{db_version}"\n'
            "  region           = var.gcp_region\n"
            "  settings {\n"
            '    tier = "db-f1-micro"\n'
            "    backup_configuration {\n"
            f"      enabled = {str(backup).lower()}\n"
            "    }\n"
            "  }\n"
            "  deletion_protection = false\n"
            "}\n"
        ]
        return out

    def _azure_database(self, node: n.DatabaseDef) -> List[str]:
        if node.type != "postgres":
            return []
        out = [
            f'resource "azurerm_postgresql_server" "{node.name}" {{\n'
            f'  name                = "{node.name}"\n'
            "  location            = var.azure_location\n"
            "  resource_group_name = var.azure_resource_group\n"
            '  sku_name            = "GP_Gen5_2"\n'
            '  version             = "14"\n'
            "  storage_mb          = 5120\n"
            f"  administrator_login          = var.{node.name}_admin\n"
            f"  administrator_login_password = var.{node.name}_password\n"
            "  ssl_enforcement_enabled      = true\n"
            '  tags = { ManagedBy = "infra-lang" }\n'
            "}\n"
        ]
        self._variables.append(f'variable "{node.name}_admin" {{ sensitive = true }}')
        self._variables.append(
            f'variable "{node.name}_password" {{ sensitive = true }}'
        )
        return out

    def _database_outputs(self, node: n.DatabaseDef) -> List[str]:
        return [
            f'output "{node.name}_endpoint" {{ value = aws_db_instance.{node.name}.endpoint }}\n',  # noqa: E501
            f'output "{node.name}_arn" {{ value = aws_db_instance.{node.name}.arn }}\n',
        ]

    def _storage(self, node: n.StorageDef) -> List[str]:
        if self.provider == "gcp":
            return self._gcp_storage(node)
        if self.provider == "azure":
            return self._azure_storage(node)
        if node.type in ("s3", "minio"):
            return [
                f'resource "aws_s3_bucket" "{node.name}" {{\n  bucket = "{node.bucket or node.name}"\n}}\n',  # noqa: E501
                f'resource "aws_s3_bucket_versioning" "{node.name}" {{\n  bucket = aws_s3_bucket.{node.name}.id\n  versioning_configuration {{ status = "Enabled" }}\n}}\n',  # noqa: E501
                f'resource "aws_s3_bucket_server_side_encryption_configuration" "{node.name}" {{\n  bucket = aws_s3_bucket.{node.name}.id\n  rule {{ apply_server_side_encryption_by_default {{ sse_algorithm = "AES256" }} }}\n}}\n',  # noqa: E501
            ]
        return []

    def _gcp_storage(self, node: n.StorageDef) -> List[str]:
        if node.type != "gcs":
            return []
        out = [
            f'resource "google_storage_bucket" "{node.name}" {{\n'
            f'  name     = "{node.bucket or node.name}"\n'
            "  location = var.gcp_region\n"
            "  versioning {\n    enabled = true\n  }\n"
            "  uniform_bucket_level_access = true\n"
            '  labels = {\n    managed-by = "infra-lang"\n  }\n'
            "}\n"
        ]
        return out

    def _azure_storage(self, node: n.StorageDef) -> List[str]:
        if node.type != "azure_blob":
            return []
        out = [
            f'resource "azurerm_storage_account" "{node.name}" {{\n'
            f'  name                     = "{node.name}"\n'
            "  resource_group_name      = var.azure_resource_group\n"
            "  location                 = var.azure_location\n"
            '  account_tier             = "Standard"\n'
            '  account_replication_type = "LRS"\n'
            '  tags = { ManagedBy = "infra-lang" }\n'
            "}\n"
        ]
        return out

    def _storage_outputs(self, node: n.StorageDef) -> List[str]:
        return [
            f'output "{node.name}_arn" {{ value = aws_s3_bucket.{node.name}.arn }}\n'
        ]

    def _network(self, node: n.NetworkDef) -> List[str]:
        out = [
            f'resource "aws_vpc" "{node.name}" {{\n  cidr_block = "{node.cidr or "10.0.0.0/16"}"\n}}\n'  # noqa: E501
        ]
        for sn in node.subnets:
            out.append(
                f'resource "aws_subnet" "{node.name}_{sn.name}" {{\n'
                f"  vpc_id = aws_vpc.{node.name}.id\n"
                f'  cidr_block = "{sn.cidr}"\n'
                f"}}\n"
            )
        out.append(
            f'resource "aws_internet_gateway" "{node.name}" {{\n  vpc_id = aws_vpc.{node.name}.id\n}}\n'  # noqa: E501
        )
        return out

    def _secret(
        self, node: n.SecretDef, program: Optional[n.Program] = None
    ) -> List[str]:
        if node.store is not None:
            store_defs = [
                s
                for s in (program.statements if program else ())
                if isinstance(s, n.SecretStoreDef)
            ]
            store = next((s for s in store_defs if s.name == node.store), None)
            return self._store_backed_secret(node, store)
        out = [
            f'resource "aws_secretsmanager_secret" "{node.name}" {{\n  name = "{node.name}"\n}}\n',  # noqa: E501
            f'resource "aws_secretsmanager_secret_version" "{node.name}" {{\n'
            f"  secret_id = aws_secretsmanager_secret.{node.name}.id\n"
            f"  secret_string = jsonencode({{"
            + ", ".join(f'"{e.name}" = var.{node.name}_{e.name}' for e in node.entries)
            + "})\n}}\n",
        ]
        for e in node.entries:
            self._variables.append(
                f'variable "{node.name}_{e.name}" {{ sensitive = true }}'
            )
        return out

    def _store_backed_secret(
        self, node: n.SecretDef, store: Optional[n.SecretStoreDef]
    ) -> List[str]:
        """Cloud/vault secret-manager resources for a store-backed secret
        (v0.5.0). Values are passed as sensitive variables; the remote key
        is the store ``path`` when set, else the secret name."""
        provider = store.provider if store else "aws"
        key = (store.path if store and store.path else None) or node.name
        entries_json = ", ".join(
            f'"{e.name}" = var.{node.name}_{e.name}' for e in node.entries
        )
        for e in node.entries:
            self._variables.append(
                f'variable "{node.name}_{e.name}" {{ sensitive = true }}'
            )
        if provider == "gcp":
            return [
                f'resource "google_secret_manager_secret" "{node.name}" {{\n'
                f'  secret_id = "{key}"\n'
                "  replication {\n    auto {}\n  }\n}\n",
                f'resource "google_secret_manager_secret_version" "{node.name}" {{\n'
                f"  secret = google_secret_manager_secret.{node.name}.id\n"
                f"  secret_data = jsonencode({{{entries_json}}})\n"
                "}\n",
            ]
        if provider == "vault":
            return [
                f'resource "vault_generic_secret" "{node.name}" {{\n'
                f'  path = "{key}"\n'
                f"  data_json = jsonencode({{{entries_json}}})\n"
                "}\n",
            ]
        if provider == "kubernetes":
            return [
                f'resource "kubernetes_secret" "{node.name}" {{\n'
                "  metadata {\n"
                f'    name = "{node.name}"\n'
                + (f'    namespace = "{store.namespace}"\n' if store and store.namespace else "")
                + "  }\n"
                "  data = {\n"
                + "".join(
                    f'    {e.name} = var.{node.name}_{e.name}\n' for e in node.entries
                )
                + "  }\n}\n",
            ]
        # aws (and fallback for undeclared stores)
        return [
            f'resource "aws_secretsmanager_secret" "{node.name}" {{\n  name = "{key}"\n}}\n',  # noqa: E501
            f'resource "aws_secretsmanager_secret_version" "{node.name}" {{\n'
            f"  secret_id = aws_secretsmanager_secret.{node.name}.id\n"
            f"  secret_string = jsonencode({{{entries_json}}})\n"
            "}\n",
        ]

    def _queue(self, node: n.QueueDef) -> List[str]:
        if node.type == "rabbitmq":
            return []
        out = []
        for t in node.topics:
            out.append(
                f'resource "aws_sqs_queue" "{node.name}_{t.name}" {{\n  name = "{t.name}"\n}}\n'  # noqa: E501
            )
        return out
