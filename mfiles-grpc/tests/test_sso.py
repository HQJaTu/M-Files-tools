import base64
import hashlib
import socket
from unittest import mock
from urllib.parse import parse_qs, urlparse

import pytest

from mfiles_grpc import sso
from mfiles_grpc.proto import pb

AUTHORIZE = "https://login.example.com/tenant/oauth2/v2.0/authorize"
TOKEN = "https://login.example.com/tenant/oauth2/v2.0/token"


def named(**values) -> pb.NamedValueArray:
    return pb.NamedValueArray(value=[
        pb.NamedValue(key=k, value=pb.TypedValue(type=pb.DATATYPE_TEXT, data=pb.TypedValueUnion(text=v)))
        for k, v in values.items()])


def oauth_plugin(name="SSO", is_default=True, system=None, **config) -> pb.PluginInfo:
    settings = {"ClientID": "client-1", "AuthorizationEndpoint": AUTHORIZE, "TokenEndpoint": TOKEN}
    settings.update(config)
    return pb.PluginInfo(name=name, is_default=is_default, assembly_name=sso.OAUTH_ASSEMBLY,
                         plugin_configuration=named(**settings),
                         system_configuration=named(**(system or {})))


def config(**overrides) -> sso.OAuthConfig:
    return sso.oauth_config_from_plugins([oauth_plugin(**overrides)])


# --- Reading the plugin configuration -------------------------------------------------------

def test_config_is_read_from_the_oauth_plugin():
    c = sso.oauth_config_from_plugins([
        pb.PluginInfo(name="Other", is_default=True, assembly_name="Some.Other.Plugin"),
        oauth_plugin(name="Entra", system={"Scope": "vault-scope", "VaultGUID": "{V}"}),
    ])
    assert c.plugin_name == "Entra"
    assert c.configuration_scope == "vault-scope"
    assert c.vault_guid == "{V}"
    assert c.client_id == "client-1"
    assert c.authorization_endpoint == AUTHORIZE
    assert c.token_endpoint == TOKEN


def test_default_oauth_plugin_wins_over_the_first():
    c = sso.oauth_config_from_plugins([oauth_plugin(name="First", is_default=False),
                                       oauth_plugin(name="Default", is_default=True)])
    assert c.plugin_name == "Default"


def test_without_an_oauth_plugin_the_error_names_what_was_found():
    with pytest.raises(sso.SsoError, match="Some.Other.Plugin"):
        sso.oauth_config_from_plugins([pb.PluginInfo(name="X", assembly_name="Some.Other.Plugin")])


def test_redirect_defaults_to_localhost_like_the_desktop_client():
    assert config().redirect_uri == "http://localhost"
    assert config(RedirectURI="http://localhost:8912/cb").redirect_uri == "http://localhost:8912/cb"


def test_scope_defaults_depend_on_the_endpoint_version():
    assert config().scopes == ("openid", "profile", "email", "offline_access")
    v1 = config(AuthorizationEndpoint="https://adfs.example.com/adfs/oauth2/authorize")
    assert v1.scopes == ("Web.Read", "openid")
    assert config(Scope="openid api://x/.default").scopes == ("openid", "api://x/.default")


def test_id_token_is_sent_unless_the_plugin_asks_for_the_access_token():
    assert config().use_access_token is False
    assert config(UseAccessTokenInWeb="True").use_access_token is True


def test_repr_hides_client_secret():
    assert "s3cr3t" not in repr(config(ClientSecret="s3cr3t"))


# --- Discovery -----------------------------------------------------------------------------

def test_discover_asks_by_vault_and_host_for_mfiles_accounts_first():
    client = mock.Mock(host="vault.example.com")
    client.login_service.GetAuthenticationConfiguration.return_value = \
        pb.GetAuthenticationConfigurationResponse(plugin_infos=[oauth_plugin()])
    assert sso.discover(client, "{V}").plugin_name == "SSO"
    (request,), _ = client.login_service.GetAuthenticationConfiguration.call_args
    assert request.host_name == "vault.example.com"
    assert request.vault_guid == "{V}"
    assert request.type == pb.ACCOUNT_TYPE_MFILES


def test_discover_falls_back_to_windows_accounts_as_m_files_cloud_needs():
    # Seen live on M-Files Cloud: the M-Files account type answers nothing.
    client = mock.Mock(host="vault.example.com")
    client.login_service.GetAuthenticationConfiguration.side_effect = [
        pb.GetAuthenticationConfigurationResponse(),
        pb.GetAuthenticationConfigurationResponse(plugin_infos=[oauth_plugin(name="MFLoginService.Default")])]
    assert sso.discover(client, "{V}").plugin_name == "MFLoginService.Default"
    types = [c.args[0].type for c in client.login_service.GetAuthenticationConfiguration.call_args_list]
    assert types == [pb.ACCOUNT_TYPE_MFILES, pb.ACCOUNT_TYPE_WINDOWS]


def test_discover_without_any_oauth_plugin_is_an_error():
    client = mock.Mock(host="vault.example.com")
    client.login_service.GetAuthenticationConfiguration.return_value = \
        pb.GetAuthenticationConfigurationResponse()
    with pytest.raises(sso.SsoError, match="none"):
        sso.discover(client, "{V}")


# --- The OAuth authorization code flow ------------------------------------------------------

def test_pkce_challenge_is_the_s256_of_the_verifier():
    verifier, challenge = sso.pkce_pair()
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    assert challenge == base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    assert 43 <= len(verifier) <= 128
    assert sso.pkce_pair()[0] != verifier


def test_authorization_url_carries_pkce_state_and_redirect():
    url = sso.authorization_url(config(), "http://localhost:5555", "state-1", "challenge-1")
    parsed = urlparse(url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == AUTHORIZE
    q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    assert q["response_type"] == "code"
    assert q["client_id"] == "client-1"
    assert q["redirect_uri"] == "http://localhost:5555"
    assert q["state"] == "state-1"
    assert q["code_challenge"] == "challenge-1"
    assert q["code_challenge_method"] == "S256"
    assert q["scope"] == "openid profile email offline_access"


def test_authorization_url_keeps_existing_query_parameters():
    c = config(AuthorizationEndpoint=AUTHORIZE + "?p=policy")
    q = parse_qs(urlparse(sso.authorization_url(c, "http://localhost", "s", "c")).query)
    assert q["p"] == ["policy"]
    assert q["state"] == ["s"]


def test_v1_endpoint_gets_the_resource_parameter():
    c = config(AuthorizationEndpoint="https://adfs.example.com/adfs/oauth2/authorize", Resource="urn:mfiles")
    q = parse_qs(urlparse(sso.authorization_url(c, "http://localhost", "s", "c")).query)
    assert q["resource"] == ["urn:mfiles"]


def test_callback_returns_the_code():
    assert sso.parse_callback("code=abc&state=s1", "s1") == "abc"


def test_callback_with_the_wrong_state_is_refused():
    with pytest.raises(sso.SsoError, match="state"):
        sso.parse_callback("code=abc&state=forged", "s1")


def test_callback_error_is_reported():
    with pytest.raises(sso.SsoError, match="access_denied.*User cancelled"):
        sso.parse_callback("error=access_denied&error_description=User+cancelled&state=s1", "s1")


def test_code_exchange_sends_the_verifier_and_returns_the_id_token():
    post = mock.Mock(return_value={"id_token": "ID", "access_token": "ACCESS"})
    assert sso.exchange_code(config(), "abc", "verifier-1", "http://localhost:5555", post=post) == "ID"
    (url, form), _ = post.call_args
    assert url == TOKEN
    assert form["grant_type"] == "authorization_code"
    assert form["code"] == "abc"
    assert form["code_verifier"] == "verifier-1"
    assert form["redirect_uri"] == "http://localhost:5555"
    assert form["client_id"] == "client-1"
    assert "client_secret" not in form


def test_code_exchange_returns_the_access_token_when_configured():
    post = mock.Mock(return_value={"id_token": "ID", "access_token": "ACCESS"})
    c = config(UseAccessTokenInWeb="true", ClientSecret="s3cr3t")
    assert sso.exchange_code(c, "abc", "v", "http://localhost", post=post) == "ACCESS"
    (_, form), _ = post.call_args
    assert form["client_secret"] == "s3cr3t"


def test_code_exchange_without_the_wanted_token_is_an_error():
    post = mock.Mock(return_value={"access_token": "ACCESS"})
    with pytest.raises(sso.SsoError, match="id_token"):
        sso.exchange_code(config(), "abc", "v", "http://localhost", post=post)


def test_loopback_address_takes_the_redirects_own_port():
    # The IdP compares the redirect exactly, so no other port would do.
    assert sso.loopback_address("http://localhost/signin-oidc") == (80, "/signin-oidc")
    assert sso.loopback_address("http://localhost") == (80, "/")
    assert sso.loopback_address("http://localhost:8912/cb") == (8912, "/cb")


def test_non_loopback_redirect_is_refused():
    with pytest.raises(sso.SsoError, match="signin-oidc"):
        sso.loopback_address("https://vault.example.com/signin-oidc")


def test_acquire_token_runs_the_browser_round_trip():
    # The fake browser plays the IdP: it follows the authorization URL straight back
    # to the loopback listener with a code, as a real sign-in would.
    import urllib.request

    def browser(url):
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        seen.update(q)
        urllib.request.urlopen(f"{q['redirect_uri']}?code=the-code&state={q['state']}", timeout=5).read()

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    redirect = f"http://localhost:{port}/signin-oidc"
    seen = {}
    post = mock.Mock(return_value={"id_token": "ID"})
    token = sso.acquire_token(config(RedirectURI=redirect), open_browser=browser, post=post, timeout=10)
    assert token == "ID"
    assert seen["redirect_uri"] == redirect
    (_, form), _ = post.call_args
    assert form["code"] == "the-code"
    assert form["redirect_uri"] == redirect
