"""Deep audit of the GitHub Actions backend output."""

from __future__ import annotations

import yaml

from infra import parse
from infra.backends.github import GitHubActionsBackend


def compile_github(source):
    result = GitHubActionsBackend().compile(parse(source))
    content = list(result.files.values())[0]
    return yaml.safe_load(content)


BASIC_PIPELINE = (
    'pipeline ci { trigger { branches: ["main"] } '
    'stages { test: { runsOn: "ubuntu-latest" steps { s: { run: "pytest" } } } } }'
)


class TestGithubActionsOutput:
    def test_workflow_has_on_trigger(self):
        data = compile_github(BASIC_PIPELINE)
        assert "on" in data or True in data

    def test_workflow_has_jobs(self):
        data = compile_github(BASIC_PIPELINE)
        assert "jobs" in data
        assert len(data["jobs"]) >= 1

    def test_step_run_command_present(self):
        data = compile_github(BASIC_PIPELINE)
        job = list(data["jobs"].values())[0]
        run_steps = [s for s in job.get("steps", []) if "run" in s]
        assert len(run_steps) >= 1
        assert "pytest" in str(run_steps)

    def test_matrix_strategy_generated(self):
        source = (
            'pipeline ci { trigger { branches: ["main"] } '
            "stages { test: { runsOn: \"ubuntu-latest\" "
            'matrix { python: ["3.10", "3.11", "3.12"] } '
            'steps { s: { run: "pytest" } } } } }'
        )
        job = list(compile_github(source)["jobs"].values())[0]
        assert "strategy" in job
        assert "python" in str(job["strategy"]["matrix"])

    def test_needs_dependency_preserved(self):
        source = (
            'pipeline ci { trigger { branches: ["main"] } stages { '
            'test: { runsOn: "ubuntu-latest" steps { s: { run: "pytest" } } } '
            'build: { needs: [test] runsOn: "ubuntu-latest" steps { s: { run: "docker build ." } } } } }'
        )
        jobs = compile_github(source)["jobs"]
        assert len(jobs) >= 2
        build_job = next((v for k, v in jobs.items() if "build" in k.lower()), None)
        if build_job:
            assert "needs" in build_job
            assert build_job["needs"] == ["test"]

    def test_output_is_valid_yaml(self):
        result = GitHubActionsBackend().compile(parse(BASIC_PIPELINE))
        for name, content in result.files.items():
            if name.endswith((".yml", ".yaml")):
                assert isinstance(yaml.safe_load(content), dict)

    def test_uses_action_in_step(self):
        source = (
            'pipeline ci { trigger { branches: ["main"] } '
            "stages { ci: { runsOn: \"ubuntu-latest\" steps { "
            'c: { uses: "actions/checkout@v4" } '
            't: { run: "pytest" } } } } }'
        )
        content = list(GitHubActionsBackend().compile(parse(source)).files.values())[0]
        assert "actions/checkout" in content
