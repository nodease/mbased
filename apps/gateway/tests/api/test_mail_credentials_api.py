from apps.gateway.main import app
from apps.gateway.api.v1.endpoints.mail_credentials import gmail_oauth_result


def test_mail_credential_routes_and_safe_response_schema_are_registered():
    schema = app.openapi()
    paths = schema["paths"]

    assert {"get", "post"} <= set(paths["/api/v1/mail/credentials"])
    assert {"get", "patch", "delete"} <= set(
        paths["/api/v1/mail/credentials/{credential_id}"]
    )
    assert {"get"} <= set(paths["/api/v1/mail/credentials/{credential_id}/permissions"])
    assert {"put", "delete"} <= set(
        paths["/api/v1/mail/credentials/{credential_id}/permissions/users/{user_id}"]
    )
    assert {"put", "delete"} <= set(
        paths["/api/v1/mail/credentials/{credential_id}/permissions/teams/{team_id}"]
    )
    assert {"post"} <= set(
        paths["/api/v1/mail/credentials/oauth/google/start"]
    )
    assert {"get"} <= set(
        paths["/api/v1/mail/credentials/oauth/google/callback"]
    )
    assert "/api/v1/mail/credentials/oauth/google/result" not in paths

    properties = schema["components"]["schemas"]["MailCredentialResponse"]["properties"]
    assert "email_preview" in properties
    assert {
        "email_address",
        "secret",
        "encrypted_secret",
        "encryption_key_version",
    }.isdisjoint(properties)

    access_parameters = paths[
        "/api/v1/organizations/{organization_id}/members/{user_id}/resource-access"
    ]["get"]["parameters"]
    resource_type = next(
        parameter
        for parameter in access_parameters
        if parameter["name"] == "resourceType"
    )
    assert "mail_credential" in resource_type["schema"]["enum"]

    option_properties = schema["components"]["schemas"]["MailCredentialOptionResponse"][
        "properties"
    ]
    assert set(option_properties) == {
        "id",
        "credential_name",
        "provider",
        "auth_type",
        "email_preview",
        "status",
    }

    update_properties = schema["components"]["schemas"]["MailCredentialUpdate"][
        "properties"
    ]
    assert set(update_properties) == {"credential_name", "secret"}


def test_gmail_oauth_result_is_static_and_non_cacheable():
    response = gmail_oauth_result()

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert b"credential_id" not in response.body
    assert b"token" not in response.body
