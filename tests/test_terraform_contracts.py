"""Contract tests for the Terraform backend — assert the *content* of the
generated HCL, not just that a provider was selected."""

from __future__ import annotations

from infra import parse
from infra.backends.terraform import TerraformBackend


def _tf(source: str):
    prog = parse(source, filename="app.infra")
    result = TerraformBackend().compile(prog)
    return result.files


def _tf_with_cluster(source: str):
    return _tf("cluster c { " + source + " }")


class TestAwsCluster:
    def test_aws_provider_conf(self):
        files = _tf("cluster c { provider: aws }")
        providers = files["providers.tf"]
        assert 'provider "aws"' in providers
        assert "region = var.aws_region" in providers

    def test_aws_vpc_and_subnets(self):
        main = _tf("cluster c { provider: aws }")["main.tf"]
        assert 'resource "aws_vpc" "c"' in main
        assert 'resource "aws_subnet" "c_a"' in main
        assert 'resource "aws_subnet" "c_b"' in main

    def test_aws_eks_cluster(self):
        main = _tf("cluster c { provider: aws }")["main.tf"]
        assert 'resource "aws_eks_cluster" "c"' in main
        assert 'name     = "c"' in main

    def test_aws_node_group(self):
        main = _tf('cluster c { provider: aws nodes { main: { min: 2 max: 5 } } }')["main.tf"]
        assert 'resource "aws_eks_node_group" "c_main"' in main

    def test_aws_iam_role(self):
        main = _tf("cluster c { provider: aws }")["main.tf"]
        assert 'resource "aws_iam_role" "c"' in main
        assert 'name = "c-cluster-role"' in main

    def test_aws_variables(self):
        files = _tf("cluster c { provider: aws }")
        assert 'variable "aws_region"' in files["variables.tf"]
        assert 'variable "environment"' in files["variables.tf"]


class TestGcpCluster:
    def test_gcp_provider_conf(self):
        providers = _tf("cluster c { provider: gcp }")["providers.tf"]
        assert 'provider "google"' in providers
        assert "project = var.gcp_project" in providers

    def test_gcp_cluster_resource(self):
        main = _tf("cluster c { provider: gcp }")["main.tf"]
        assert 'resource "google_container_cluster" "c"' in main

    def test_gcp_variables(self):
        files = _tf("cluster c { provider: gcp }")
        assert 'variable "gcp_project"' in files["variables.tf"]
        assert 'variable "gcp_region"' in files["variables.tf"]


class TestAzureCluster:
    def test_azure_provider_conf(self):
        providers = _tf("cluster c { provider: azure }")["providers.tf"]
        assert 'provider "azurerm"' in providers
        assert "features {}" in providers

    def test_azure_cluster_resource(self):
        main = _tf("cluster c { provider: azure }")["main.tf"]
        assert 'resource "azurerm_kubernetes_cluster" "c"' in main

    def test_azure_variables(self):
        files = _tf("cluster c { provider: azure }")
        assert 'variable "azure_location"' in files["variables.tf"]
        assert 'variable "azure_resource_group"' in files["variables.tf"]


class TestDatabaseAndStorage:
    def test_aws_rds_database(self):
        main = _tf("cluster c { provider: aws }\ndatabase db { type: postgres }")["main.tf"]
        assert "aws_db_instance" in main

    def test_database_outputs(self):
        files = _tf("cluster c { provider: aws }\ndatabase db { type: postgres }")
        assert "output" in files["outputs.tf"]

    def test_aws_storage_s3(self):
        main = _tf('cluster c { provider: aws }\nstorage s { type: s3 bucket: "b" }')["main.tf"]
        assert "aws_s3_bucket" in main

    def test_gcp_storage_bucket(self):
        main = _tf('cluster c { provider: gcp }\nstorage s { type: gcs bucket: "b" }')["main.tf"]
        assert "google_storage_bucket" in main

    def test_versions_file(self):
        files = _tf("cluster c { provider: aws }")
        assert "required_version" in files["versions.tf"]
        assert 'source = "hashicorp/aws"' in files["versions.tf"]

    def test_tfvars_example(self):
        files = _tf("cluster c { provider: aws }")
        assert "aws_region" in files["terraform.tfvars.example"]
        assert "environment" in files["terraform.tfvars.example"]


class TestProviderCombinations:
    def test_gcp_provider_config(self):
        files = _tf("cluster c { provider: gcp }")
        providers = files["providers.tf"]
        assert 'provider "google"' in providers
        assert "var.gcp_project" in providers
        assert "var.gcp_region" in providers

    def test_azure_provider_config(self):
        files = _tf("cluster c { provider: azure }")
        providers = files["providers.tf"]
        assert 'provider "azurerm"' in providers
        assert "features {}" in providers

    def test_provider_detected_from_cluster(self):
        # no explicit provider -> detected from the ClusterDef
        files = _tf_with_cluster("provider: gcp")
        assert 'provider "google"' in files["providers.tf"]

    def test_gcp_variables_declared(self):
        files = _tf("cluster c { provider: gcp }")
        assert 'variable "gcp_project"' in files["variables.tf"]
        assert 'variable "gcp_region"' in files["variables.tf"]

    def test_azure_variables_declared(self):
        files = _tf("cluster c { provider: azure }")
        assert 'variable "azure_location"' in files["variables.tf"]
        assert 'variable "azure_resource_group"' in files["variables.tf"]

    def test_aws_default_region_variable(self):
        files = _tf("cluster c { provider: aws }")
        assert 'variable "aws_region" { default = "eu-west-1" }' in files["variables.tf"]

    def test_aws_provider_has_default_tags(self):
        files = _tf("cluster c { provider: aws }")
        assert "ManagedBy" in files["providers.tf"]
        assert "infra-lang" in files["providers.tf"]


class TestMissingOptionalFields:
    def test_cluster_without_nodes(self):
        # nodes block optional; must not crash, must emit vpc+eks
        files = _tf("cluster c { provider: aws }")
        assert 'resource "aws_vpc"' in files["main.tf"]
        assert 'resource "aws_eks_cluster"' in files["main.tf"]

    def test_database_without_size(self):
        files = _tf("cluster c { provider: aws }\ndatabase db { type: postgres }")
        main = files["main.tf"]
        assert 'resource "aws_db_instance"' in main

    def test_empty_cluster_body(self):
        files = _tf("cluster c { provider: aws }")
        assert files["providers.tf"]


class TestVariableInterpolation:
    def test_top_level_database_compiles(self):
        files = _tf("cluster c { provider: aws }\ndatabase db { type: postgres }")
        assert files["main.tf"]

    def test_aws_storage_with_bucket_compiles(self):
        main = _tf(
            'cluster c { provider: aws }\nstorage s { type: s3 bucket: "b" }'
        )["main.tf"]
        assert "aws_s3_bucket" in main


class TestTerraformNetworkSecretQueue:
    def test_network_vpc_subnets(self):
        main = _tf(
            'network n { cidr: "10.0.0.0/16" '
            'subnets { a: { cidr: "10.0.1.0/24" } } }'
        )["main.tf"]
        assert 'resource "aws_vpc" "n"' in main
        assert 'resource "aws_subnet" "n_a"' in main

    def test_secret_secretsmanager(self):
        files = _tf("secret s { k: 'v' }")
        assert "aws_secretsmanager_secret" in files["main.tf"]
        assert "variable" in files["variables.tf"]

    def test_queue_rabbitmq_no_sqs(self):
        main = _tf("queue q { type: rabbitmq }")["main.tf"]
        assert "aws_sqs" not in main
