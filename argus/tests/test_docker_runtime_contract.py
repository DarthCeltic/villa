from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_python_runtime_image_includes_declarative_config() -> None:
    """Container services must ship the policy files their read routes load."""

    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY config/ ./config/" in dockerfile
    assert (ROOT / "config" / "upstream_sources.json").is_file()


def test_docker_context_does_not_exclude_declarative_config() -> None:
    ignored = {
        line.strip().rstrip("/")
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert "config" not in ignored


def test_published_review_ports_are_loopback_only_and_share_the_operator_key() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert '"127.0.0.1:18787:8787"' in compose
    assert '"127.0.0.1:8792:8792"' in compose
    assert "ARGUS_UI_ACCESS_KEY_FILE: /argus-home/state/ui_access_key" in compose
    assert compose.count("ARGUS_UI_ACCESS_KEY_FILE: /argus-home/state/ui_access_key") == 2
