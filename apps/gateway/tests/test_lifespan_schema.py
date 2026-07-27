import inspect

from apps.gateway import lifespan as lifespan_module


def test_gateway_lifespan_does_not_apply_orm_or_enum_schema_ddl():
    source = inspect.getsource(lifespan_module)

    assert "Base.metadata.create_all" not in source
    assert "ALTER TYPE" not in source
    assert "_ensure_deployment_type_enum_values" not in source
    assert "require_schedule_dispatch_migration_ready" in source
    assert "require_mail_credential_keyring_ready" in source
    assert "require_llm_credential_keyring_ready" in source
    assert "require_connector_test_security_ready" in source
    assert "require_outbound_proxy_security_ready" in source
    assert "require_public_conversation_schema_ready" in source

    lifespan_source = inspect.getsource(lifespan_module.lifespan)
    assert lifespan_source.index("require_outbound_proxy_security_ready()") < (
        lifespan_source.index("require_schedule_dispatch_migration_ready(")
    )
    assert lifespan_source.index("require_mail_credential_keyring_ready()") < (
        lifespan_source.index("require_schedule_dispatch_migration_ready(")
    )
    assert lifespan_source.index("require_llm_credential_keyring_ready()") < (
        lifespan_source.index("require_schedule_dispatch_migration_ready(")
    )
    assert lifespan_source.index("require_connector_test_security_ready()") < (
        lifespan_source.index("require_schedule_dispatch_migration_ready(")
    )
    assert lifespan_source.index("require_public_conversation_schema_ready(db)") < (
        lifespan_source.index("seed_placeholder_user(db)")
    )
