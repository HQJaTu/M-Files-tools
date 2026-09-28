# vim: autoindent tabstop=4 shiftwidth=4 expandtab softtabstop=4 filetype=python

"""
Single sign-on: get an OAuth token for the vault's identity provider.

M-Files does not run the OAuth flow for a client; the client does, then hands the
token to LogIn (Client.log_in_with_token). What follows mirrors the M-Files web
client (26.10, mfapp-bundle-mfwebui: loginToServer, doPluginLogin, getConfig):

  1. GetAuthenticationConfiguration, asked by vault GUID and host name once per
     account type (M-Files, then Windows), names the authentication plugins. The
     one with assembly MFiles.AuthenticationProviders.OAuth carries the IdP
     settings as named values: ClientID, AuthorizationEndpoint, TokenEndpoint,
     Scope, RedirectURI ... On M-Files Cloud it answers only for the Windows type.
  2. Authorization code flow with PKCE. As a native client, like M-Files Desktop,
     the redirect is RedirectURI (http://localhost/signin-oidc on M-Files Cloud),
     or http://localhost when there is none. The IdP compares it exactly, so a
     one-shot listener takes its very port on the loopback interface: 80 when
     the URI names none.
  3. The ID token is what LogIn gets, unless UseAccessTokenInWeb asks for the
     access token.
"""

import base64
import hashlib
import http.server
import json
import logging
import secrets
import socket
import threading
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from typing import Callable, Iterable, Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from .proto import pb
from .values import to_python

log = logging.getLogger(__name__)

OAUTH_ASSEMBLY = "MFiles.AuthenticationProviders.OAuth"

# The web client's scopes when the plugin names none: v2.0 endpoints are Entra ID's.
DEFAULT_SCOPES_V2 = ("openid", "profile", "email", "offline_access")
DEFAULT_SCOPES_V1 = ("Web.Read", "openid")

LOOPBACK_HOSTS = ("localhost", "127.0.0.1")

PostForm = Callable[[str, dict], dict]


class SsoError(Exception):
    """The vault has no usable SSO configuration, or the sign-in failed."""


@dataclass(frozen=True)
class OAuthConfig:
    plugin_name: str
    configuration_scope: str
    vault_guid: str
    client_id: str
    authorization_endpoint: str
    token_endpoint: str
    scopes: tuple[str, ...]
    redirect_uri: str
    resource: str = ""
    client_secret: Optional[str] = None
    use_access_token: bool = False

    def __repr__(self) -> str:
        # Never let the client secret reach a log line or a traceback.
        secret = "None" if self.client_secret is None else f"<{len(self.client_secret)} chars>"
        return (f"OAuthConfig(plugin_name={self.plugin_name!r}, configuration_scope={self.configuration_scope!r}, "
                f"vault_guid={self.vault_guid!r}, client_id={self.client_id!r}, "
                f"authorization_endpoint={self.authorization_endpoint!r}, "
                f"token_endpoint={self.token_endpoint!r}, scopes={self.scopes!r}, "
                f"redirect_uri={self.redirect_uri!r}, resource={self.resource!r}, "
                f"client_secret={secret}, use_access_token={self.use_access_token})")


def _named_values(array: pb.NamedValueArray) -> dict[str, str]:
    return {nv.key: str(to_python(nv.value) or "") for nv in array.value}


def oauth_config_from_plugins(plugins: Iterable[pb.PluginInfo]) -> OAuthConfig:
    """
    :param plugins: PluginInfo list from GetAuthenticationConfiguration or GetAuthenticationPlugins
    :return: Settings of the default OAuth plugin, or of the first one when none is the default
    :raises SsoError: No OAuth plugin is configured
    """
    plugins = list(plugins)
    oauth = [p for p in plugins if p.assembly_name == OAUTH_ASSEMBLY]
    if not oauth:
        found = ", ".join(f"{p.name} ({p.assembly_name})" for p in plugins) or "none"
        raise SsoError(f"No {OAUTH_ASSEMBLY} plugin is configured; plugins found: {found}")
    plugin = next((p for p in oauth if p.is_default), oauth[0])

    settings = _named_values(plugin.plugin_configuration)
    system = _named_values(plugin.system_configuration)
    authorization_endpoint = settings.get("AuthorizationEndpoint", "")
    client_id = settings.get("ClientID", "")
    if not client_id or not authorization_endpoint or not settings.get("TokenEndpoint"):
        raise SsoError(f"Plugin {plugin.name!r} lacks ClientID, AuthorizationEndpoint or TokenEndpoint")

    scopes = tuple(settings.get("Scope", "").split())
    if not scopes:
        scopes = DEFAULT_SCOPES_V2 if "/v2.0/" in authorization_endpoint else DEFAULT_SCOPES_V1
    return OAuthConfig(
        plugin_name=plugin.name,
        configuration_scope=system.get("Scope", ""),
        vault_guid=system.get("VaultGUID", ""),
        client_id=client_id,
        authorization_endpoint=authorization_endpoint,
        token_endpoint=settings["TokenEndpoint"],
        scopes=scopes,
        redirect_uri=settings.get("RedirectURI") or "http://localhost",
        resource=settings.get("Resource", ""),
        client_secret=settings.get("ClientSecret") or None,
        use_access_token=settings.get("UseAccessTokenInWeb", "").lower() == "true",
    )


def discover(client, vault: str) -> OAuthConfig:
    """
    Read the vault's SSO settings. Needs no session.

    GetAuthenticationPlugins is no substitute: anonymously it names the plugin
    but leaves its configuration out.

    :param client: A Client, logged in or not
    :param vault: Vault GUID
    :return: See oauth_config_from_plugins()
    """
    found: list[pb.PluginInfo] = []
    # Asked the way the web client asks, and in its order.
    for account_type in (pb.ACCOUNT_TYPE_MFILES, pb.ACCOUNT_TYPE_WINDOWS):
        response = client.login_service.GetAuthenticationConfiguration(pb.GetAuthenticationConfigurationRequest(
            vault_guid=vault, host_name=client.host, type=account_type))
        if any(p.assembly_name == OAUTH_ASSEMBLY for p in response.plugin_infos):
            return oauth_config_from_plugins(response.plugin_infos)
        log.debug("No OAuth plugin for account type %s", pb.AccountType.Name(account_type))
        found.extend(response.plugin_infos)
    return oauth_config_from_plugins(found)


def pkce_pair() -> tuple[str, str]:
    """
    :return: (code_verifier, code_challenge) for PKCE method S256
    """
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def authorization_url(config: OAuthConfig, redirect_uri: str, state: str, challenge: str) -> str:
    """
    :param redirect_uri: Where the IdP sends the browser back, with the listener's port
    :return: URL to open in the browser
    """
    parameters = {
        "response_type": "code",
        "client_id": config.client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(config.scopes),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    # v1 endpoints (AD FS, Entra v1) name the API by resource instead of by scope.
    if config.resource and "/v2.0/" not in config.authorization_endpoint:
        parameters["resource"] = config.resource
    endpoint = urlparse(config.authorization_endpoint)
    query = "&".join(q for q in (endpoint.query, urlencode(parameters)) if q)
    return urlunparse(endpoint._replace(query=query))


def parse_callback(query: str, expected_state: str) -> str:
    """
    :param query: Query string the IdP redirected the browser back with
    :return: The authorization code
    :raises SsoError: The IdP reported an error, or the state is not ours
    """
    q = {k: v[0] for k, v in parse_qs(query).items()}
    if q.get("state") != expected_state:
        raise SsoError("Sign-in answer carries the wrong state; ignoring it")
    if "error" in q:
        raise SsoError(f"Sign-in failed: {q['error']}: {q.get('error_description', '')}")
    if not q.get("code"):
        raise SsoError("Sign-in answer carries no authorization code")
    return q["code"]


def _post_form(url: str, form: dict) -> dict:
    request = urllib.request.Request(url, data=urlencode(form).encode("ascii"),
                                     headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as e:
        # The IdP explains a refused code in the body; the status alone says nothing.
        raise SsoError(f"Token endpoint answered {e.code}: {e.read().decode('utf-8', 'replace')}") from e


def exchange_code(config: OAuthConfig, code: str, verifier: str, redirect_uri: str,
                  post: PostForm = _post_form) -> str:
    """
    :param redirect_uri: The one sent in the authorization URL; the IdP compares them
    :return: The ID token, or the access token when the plugin asks for it
    """
    form = {
        "grant_type": "authorization_code",
        "client_id": config.client_id,
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
    }
    if config.client_secret:
        form["client_secret"] = config.client_secret
    tokens = post(config.token_endpoint, form)
    wanted = "access_token" if config.use_access_token else "id_token"
    if not tokens.get(wanted):
        raise SsoError(f"Token endpoint returned no {wanted}; it returned: {', '.join(sorted(tokens))}")
    return tokens[wanted]


def loopback_address(redirect_uri: str) -> tuple[int, str]:
    """
    :return: (port, path) to listen on
    :raises SsoError: The redirect does not point back at this machine
    """
    parsed = urlparse(redirect_uri)
    if parsed.scheme != "http" or parsed.hostname not in LOOPBACK_HOSTS:
        raise SsoError(f"Redirect URI {redirect_uri!r} is not a loopback address, so a command line client "
                       "cannot receive the sign-in. Set MFILES_GRPC_TOKEN to a token obtained elsewhere.")
    return parsed.port or 80, parsed.path or "/"


class _IPv6Server(http.server.HTTPServer):
    address_family = socket.AF_INET6


def _listen(port: int, handler) -> list[http.server.HTTPServer]:
    # A browser may resolve localhost to either address, so answer on both.
    servers, errors = [], []
    for server_class, interface in ((http.server.HTTPServer, "127.0.0.1"), (_IPv6Server, "::1")):
        try:
            servers.append(server_class((interface, port), handler))
        except OSError as e:
            errors.append(f"{interface}: {e}")
    if not servers:
        raise SsoError(f"Cannot listen for the sign-in on port {port}: {'; '.join(errors)}")
    return servers


def acquire_token(config: OAuthConfig, open_browser: Callable[[str], object] = webbrowser.open,
                  post: PostForm = _post_form, timeout: float = 300) -> str:
    """
    Sign in through the browser.

    :param timeout: Seconds to wait for the user to finish signing in
    :return: See exchange_code()
    """
    port, path = loopback_address(config.redirect_uri)
    redirect_uri = config.redirect_uri
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)
    answer: dict[str, str] = {}
    done = threading.Event()

    class Callback(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlparse(self.path)
            if url.path != path or "state=" not in url.query:
                self.send_error(404)
                return
            answer["query"] = url.query
            done.set()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("Sign-in received; you can close this window.".encode("utf-8"))

        def log_message(self, format, *args):
            log.debug("Loopback: " + format, *args)

    servers = _listen(port, Callback)
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = authorization_url(config, redirect_uri, state, challenge)
        print(f"Opening the browser to sign in. If it does not open, visit:\n  {url}")
        open_browser(url)
        if not done.wait(timeout):
            raise SsoError(f"No sign-in within {timeout:.0f} s")
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
    code = parse_callback(answer["query"], state)
    return exchange_code(config, code, verifier, redirect_uri, post=post)
