from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_connector_demo_is_an_explicit_compose_override() -> None:
    base_compose = _read("docker/docker-compose.yml")
    demo_compose = _read("docker/docker-compose.connector-demo.yml")

    assert "CONNECTOR_TEST_TRUSTED_LOCAL_TARGETS" not in base_compose
    assert "CONNECTOR_TEST_LOCAL_PROFILE_ENABLED" not in base_compose
    assert "connector-test-postgres:" not in base_compose
    assert "connector-test-tls-init:" not in base_compose
    assert "connector-demo-network:" not in base_compose
    assert "connector-test-tls-init:" in demo_compose
    assert "connector-test-postgres:" in demo_compose
    assert "connector-test-runtime-verifier:" in demo_compose
    assert 'profiles: ["connector-demo"]' in demo_compose
    assert 'profiles: ["connector-demo-verify"]' in demo_compose
    assert (
        'CONNECTOR_TEST_LOCAL_PROFILE_ENABLED: "true"'
        in demo_compose
    )
    assert (
        'CONNECTOR_TEST_TRUSTED_LOCAL_TARGETS: "connector-test-postgres:5432"'
        in demo_compose
    )
    assert 'CONNECTOR_TEST_ALLOWED_PORTS: "5432"' in demo_compose
    assert (
        'CONNECTOR_TEST_REDIS_URL: "redis://connector-test-redis:6379/15"'
        in demo_compose
    )
    assert "CONNECTOR_TEST_REDIS_URL" not in base_compose
    assert "54322" not in demo_compose
    assert 'CONNECTOR_TEST_TRUSTED_LOCAL_CA_FILE: "/run/connector-test-ca/ca.crt"' in (
        demo_compose
    )
    assert "../local/connector-test-tls/docker:/run/connector-test-ca:ro" in (
        demo_compose
    )


def test_host_run_demo_uses_separate_profile_and_published_port() -> None:
    development_compose = _read("dev/docker-compose.yml")
    development_environment = _read("dev/.env.example")

    assert "connector-test-tls-init:" in development_compose
    assert "connector-test-postgres:" in development_compose
    assert "connector-test-redis:" in development_compose
    assert 'profiles: ["connector-demo"]' in development_compose
    assert '"127.0.0.1:55432:5432"' in development_compose
    assert '"127.0.0.1:56379:6379"' in development_compose
    assert (
        "CONNECTOR_TEST_LOCAL_PROFILE_ENABLED=true"
        in development_environment
    )
    assert (
        "CONNECTOR_TEST_TRUSTED_LOCAL_TARGETS=localhost:55432"
        in development_environment
    )
    assert "CONNECTOR_TEST_ALLOWED_PORTS=5432,55432" in development_environment
    assert (
        "CONNECTOR_TEST_TRUSTED_LOCAL_CA_FILE=" in development_environment
    )


def test_demo_host_ports_are_bound_to_loopback_only() -> None:
    development_compose = _read("dev/docker-compose.yml")
    demo_compose = _read("docker/docker-compose.connector-demo.yml")

    assert '"127.0.0.1:55432:5432"' in development_compose
    assert '"127.0.0.1:56379:6379"' in development_compose
    assert '"127.0.0.1:55432:5432"' in demo_compose
    assert '\n      - "55432:5432"' not in development_compose
    assert '\n      - "56379:6379"' not in development_compose
    assert '\n      - "55432:5432"' not in demo_compose


def test_private_material_mounts_follow_least_privilege() -> None:
    demo_compose = _read("docker/docker-compose.connector-demo.yml")
    gateway_section = demo_compose.split("  gateway:", 1)[1].split("\nvolumes:", 1)[0]
    postgres_section = demo_compose.split("  connector-test-postgres:", 1)[1].split(
        "  connector-test-runtime-verifier:", 1
    )[0]
    verifier_section = demo_compose.split(
        "  connector-test-runtime-verifier:", 1
    )[1].split("  gateway:", 1)[0]

    assert "connector_test_ca_private" not in demo_compose
    assert "connector_test_server_tls" not in gateway_section
    assert "connector_test_demo_credentials" not in gateway_section
    assert "connector_test_postgres_admin_credentials" not in gateway_section
    assert "connector_test_server_tls:/tls/server:ro" in postgres_section
    assert (
        "connector_test_postgres_admin_credentials:/tls/admin-credentials:ro"
        in postgres_section
    )
    assert (
        "connector_test_demo_credentials:/tls/connector-credentials:ro"
        in postgres_section
    )
    assert "connector_test_server_tls" not in verifier_section
    assert "connector_test_demo_credentials" in verifier_section
    assert "connector_test_postgres_admin_credentials" not in verifier_section
    assert "postgres-password" not in verifier_section
    assert "CONNECTOR_DEMO_POSTGRES_PASSWORD_FILE" in verifier_section
    assert 'user: "999:999"' in verifier_section


def test_demo_services_use_a_dedicated_network() -> None:
    development_compose = _read("dev/docker-compose.yml")
    demo_compose = _read("docker/docker-compose.connector-demo.yml")

    for compose in (development_compose, demo_compose):
        assert "connector-demo-network:" in compose
        assert 'network_mode: "none"' in compose
    gateway_section = demo_compose.split("  gateway:", 1)[1].split(
        "\nvolumes:", 1
    )[0]
    assert "connector-demo-network" in gateway_section
    postgres_section = demo_compose.split(
        "  connector-test-postgres:", 1
    )[1].split("  gateway:", 1)[0]
    assert "connector-demo-network" in postgres_section
    assert "moduly-network" not in postgres_section


def test_demo_postgres_is_tls_only_and_connector_role_is_least_privileged() -> None:
    development_compose = _read("dev/docker-compose.yml")
    demo_compose = _read("docker/docker-compose.connector-demo.yml")
    hba = _read("docker/connector-test-postgres/pg_hba.conf")
    role_init = _read("docker/connector-test-postgres/init-demo-role.sh")
    dockerfile = _read("docker/connector-test-postgres/Dockerfile")

    for compose in (development_compose, demo_compose):
        assert "POSTGRES_USER: postgres" in compose
        assert "POSTGRES_USER: connector_demo_user" not in compose
        assert "ssl_min_protocol_version=TLSv1.2" in compose
        assert "password_encryption=scram-sha-256" in compose
        assert "hba_file=/etc/postgresql/connector-demo-pg_hba.conf" in compose
        assert "current_setting('transaction_read_only')::boolean" in compose
        assert "rolsuper OR rolcreatedb OR rolcreaterole" in compose
    assert "hostnossl all" in hba
    assert "reject" in hba
    assert "hostssl connector_demo  connector_demo_user" in hba
    for privilege in (
        "NOSUPERUSER",
        "NOCREATEDB",
        "NOCREATEROLE",
        "NOINHERIT",
        "NOREPLICATION",
        "NOBYPASSRLS",
    ):
        assert privilege in role_init
    assert "default_transaction_read_only" in role_init
    assert "init-demo-role.sh" in dockerfile
    assert "pg_hba.conf" in dockerfile


def test_production_templates_do_not_expose_trusted_local_overrides() -> None:
    forbidden_tokens = (
        "CONNECTOR_TEST_LOCAL_PROFILE_ENABLED",
        "CONNECTOR_TEST_TRUSTED_LOCAL_TARGETS",
        "CONNECTOR_TEST_TRUSTED_LOCAL_CA_FILE",
        "connector-test-tls-init",
        "connector-test-postgres",
        "connector-demo-network",
        "connector_test_server_tls",
        "connector_test_postgres_admin_credentials",
        "connector_test_demo_credentials",
        "/run/connector-test-ca",
        "/tls/server",
        "/tls/admin-credentials",
        "/tls/connector-credentials",
    )
    helm_root = ROOT / "infra/helm/moduly"
    production_sources = [
        path
        for path in helm_root.rglob("*")
        if path.is_file() and path.suffix in {".yaml", ".yml", ".tpl"}
    ]

    assert production_sources
    for source_path in production_sources:
        source_bytes = source_path.read_bytes()
        source = source_bytes.decode(
            "utf-16" if source_bytes.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8"
        )
        for token in forbidden_tokens:
            assert token not in source, f"{token} leaked into {source_path}"


def test_certificate_material_is_generated_at_runtime_only() -> None:
    dockerfile = _read("docker/connector-test-postgres/Dockerfile")
    generator = _read("docker/connector-test-postgres/generate-certificates.sh")

    assert "openssl" in dockerfile
    assert "openssl genpkey" in generator
    assert "basicConstraints=critical,CA:TRUE,pathlen:0" in generator
    assert "subjectAltName=DNS:connector-test-postgres,DNS:localhost" in generator
    assert 'mktemp -d "${TMPDIR:-/tmp}/connector-test-generate.' in generator
    assert '"$server_dir/ca.key"' in generator
    assert '"$admin_credential_dir/demo-password"' in generator
    assert '"$connector_credential_dir/postgres-password"' in generator
    assert 'generate_password_file "$admin_credential_dir/postgres-password"' in generator
    assert 'generate_password_file "$connector_credential_dir/demo-password"' in generator
    assert "cmp -s" in generator
    assert 'mv -f "$work_dir/ca.key"' not in generator
    assert "BEGIN PRIVATE KEY" not in generator
    assert "BEGIN CERTIFICATE" not in generator
    assert "cat /tls/server/demo-password" not in generator


def test_local_generated_material_is_excluded_from_docker_build_context() -> None:
    dockerignore = _read(".dockerignore")

    assert "local/" in dockerignore.splitlines()


def test_demo_credential_helper_never_prints_the_credential() -> None:
    helper = _read("scripts/copy_connector_demo_password.ps1")

    assert "Set-Clipboard" in helper
    assert 'Write-Output "Connector demo credential copied to the clipboard."' in helper
    assert "Write-Output $password" not in helper


def test_docker_runtime_verifier_projects_only_canonical_status() -> None:
    verifier = _read("scripts/verify_connector_demo_runtime.py")

    assert 'print("connector-demo-runtime=ok")' in verifier
    assert "connector-demo-runtime=failed:" in verifier
    assert "print(password" not in verifier
    assert "print(redis_url" not in verifier
    assert "print(ca_file" not in verifier
    assert "flushdb" not in verifier.lower()
    assert "CONNECTOR_DEMO_POSTGRES_PASSWORD_FILE" in verifier
    assert "CONNECTOR_DEMO_POSTGRES_PASSWORD\"" not in verifier
    assert 'private_mode != 0o600' in verifier
    assert 'Path("/tls/server").exists()' in verifier
    assert 'Path("/tls/admin-credentials").exists()' in verifier
