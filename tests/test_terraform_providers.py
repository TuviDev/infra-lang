"""Terraform GCP/Azure provider tests."""

from __future__ import annotations

from infra import parse
from infra.backends.terraform import TerraformBackend


def compile_tf(source: str) -> dict:
    program = parse(source)
    return TerraformBackend().compile(program).files


class TestGCPProvider:
    def test_gcp_cluster(self):
        files = compile_tf('cluster main { provider: gcp region: "europe-west1" }')
        content = "\n".join(files.values())
        assert "google_container_cluster" in content

    def test_gcp_database(self):
        files = compile_tf("database db { type: postgres }\ncluster main { provider: gcp }")
        content = "\n".join(files.values())
        assert "google_sql_database_instance" in content

    def test_gcp_storage(self):
        files = compile_tf('storage assets { type: gcs bucket: "my-bucket" }\ncluster main { provider: gcp }')
        content = "\n".join(files.values())
        assert "google_storage_bucket" in content

    def test_gcp_provider_conf(self):
        files = compile_tf("cluster main { provider: gcp }")
        content = "\n".join(files.values())
        assert "google" in content.lower()


class TestAzureProvider:
    def test_azure_cluster(self):
        files = compile_tf('cluster main { provider: azure region: "westeurope" }')
        content = "\n".join(files.values())
        assert "azurerm_kubernetes_cluster" in content

    def test_azure_database(self):
        files = compile_tf("database db { type: postgres }\ncluster main { provider: azure }")
        content = "\n".join(files.values())
        assert "azurerm_postgresql_server" in content

    def test_azure_storage(self):
        files = compile_tf("storage blob { type: azure_blob }\ncluster main { provider: azure }")
        content = "\n".join(files.values())
        assert "azurerm_storage_account" in content

    def test_azure_provider_conf(self):
        files = compile_tf("cluster main { provider: azure }")
        content = "\n".join(files.values())
        assert "azurerm" in content.lower()


class TestAWSProvider:
    def test_aws_still_works(self):
        files = compile_tf("cluster main { provider: aws }")
        content = "\n".join(files.values())
        assert "aws" in content.lower()

    def test_aws_rds(self):
        files = compile_tf("database db { type: postgres storage: 20Gi }\ncluster main { provider: aws }")
        content = "\n".join(files.values())
        assert "aws_db_instance" in content
