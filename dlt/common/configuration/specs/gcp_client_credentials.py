import sys
from typing import Any, ClassVar, Final, List, Tuple, Union
from deprecated import deprecated

from dlt.common import json
from dlt.common.configuration.specs.api_credentials import OAuth2Credentials
from dlt.common.configuration.specs.exceptions import InvalidGoogleNativeCredentialsType, InvalidGoogleOauth2Json, InvalidGoogleServicesJson, NativeValueError, OAuth2ScopesRequired
from dlt.common.exceptions import MissingDependencyException
from dlt.common.typing import DictStrAny, TSecretValue, StrAny
from dlt.common.configuration.specs.base_configuration import CredentialsConfiguration, CredentialsWithDefault, configspec
from dlt.common.utils import is_interactive


@configspec
class GcpCredentialsBase(CredentialsConfiguration):
    token_uri: Final[str] = "https://oauth2.googleapis.com/token"
    auth_uri: Final[str] = "https://accounts.google.com/o/oauth2/auth"

    project_id: str = None
    location: str = "US"

    http_timeout: float = 15.0
    file_upload_timeout: float = 30 * 60.0
    retry_deadline: float = 60  # how long to retry the operation in case of error, the backoff 60s

    __config_gen_annotations__: ClassVar[List[str]] = ["location"]

    def parse_native_representation(self, native_value: Any) -> None:
        if not isinstance(native_value, str):
            raise InvalidGoogleNativeCredentialsType(self.__class__, native_value)

    def to_native_representation(self) -> str:
        return json.dumps(dict(self))

    def to_google_credentials(self) -> Any:
        pass

    def _from_info_dict(self, info: StrAny) -> None:
        self.update(info)
        self.__is_resolved__ = not self.is_partial()

    def __str__(self) -> str:
        return f"{self.project_id}[{self.location}]"


@configspec
class GcpDefaultCredentials(CredentialsWithDefault, GcpCredentialsBase):

    def parse_native_representation(self, native_value: Any) -> None:
        """Accepts google credentials as native value"""
        try:
            from google.auth.credentials import Credentials as GoogleCredentials
            if isinstance(native_value, GoogleCredentials):
                self.project_id = self.project_id or native_value.quota_project_id
                self._set_default_credentials(native_value)
                # is resolved
                self.__is_resolved__ = True
                return
        except ImportError:
            pass
        raise NativeValueError(self.__class__, native_value, "Default Google Credentials not present")

    def on_partial(self) -> None:
        """Looks for default google credentials and resolves configuration if found. Otherwise continues as partial"""
        try:
            from google.auth import default as default_credentials
            from google.auth.exceptions import DefaultCredentialsError

            # if config is missing check if credentials can be obtained from defaults
            try:
                default, project_id = default_credentials()
                # set the project id - it needs to be known by the client
                self.project_id = self.project_id or project_id or default.quota_project_id
                self._set_default_credentials(default)
                # is resolved
                self.__is_resolved__ = True
            except DefaultCredentialsError:
                # re-raise preventing exception
                pass

        except ImportError:
            # raise the exception that caused partial (typically missing config fields)
            pass

    def to_google_credentials(self) -> Any:
        if self.has_default_credentials():
            return self.default_credentials()
        else:
            return super().to_google_credentials()


@configspec
class GcpClientCredentials(GcpCredentialsBase):
    private_key: TSecretValue = None
    client_email: str = None
    type: Final[str] = "service_account"  # noqa: A003

    def parse_native_representation(self, native_value: Any) -> None:
        """Accepts ServiceAccountCredentials as native value. In other case reverts to serialized services.json"""
        service_dict: DictStrAny = None
        try:
            from google.oauth2.service_account import Credentials as ServiceAccountCredentials
            if isinstance(native_value, ServiceAccountCredentials):
                # extract credentials
                service_dict = {
                    "project_id": native_value.project_id,
                    "client_email": native_value.service_account_email,
                    "private_key": native_value  # keep native credentials in private key
                }
        except ImportError:
            pass

        if service_dict is None:
            # check if type is str
            GcpCredentialsBase.parse_native_representation(self, native_value)
            # if not instance of service account credentials then check type and try to parse native value
            try:
                service_dict = json.loads(native_value)
            except Exception:
                raise InvalidGoogleServicesJson(self.__class__, native_value)

        self._from_info_dict(service_dict)

    def on_resolved(self) -> None:
        if self.private_key and self.private_key[-1] != "\n":
            # must end with new line, otherwise won't be parsed by Crypto
            self.private_key = TSecretValue(self.private_key + "\n")

    @deprecated(reason="Use 'to_google_credentials' method instead")
    def to_service_account_credentials(self) -> Any:
        return self.to_google_credentials()

    def to_google_credentials(self) -> Any:
        from google.oauth2.service_account import Credentials as ServiceAccountCredentials
        if isinstance(self.private_key, ServiceAccountCredentials):
            # private key holds the native instance if it was passed to parse_native_representation
            return self.private_key
        else:
            return ServiceAccountCredentials.from_service_account_info(self)

    def __str__(self) -> str:
        return f"{self.client_email}@{self.project_id}[{self.location}]"


@configspec
class GcpClientCredentialsWithDefault(GcpDefaultCredentials, GcpClientCredentials):
    def parse_native_representation(self, native_value: Any) -> None:
        try:
            GcpDefaultCredentials.parse_native_representation(self, native_value)
        except NativeValueError:
            pass
        GcpClientCredentials.parse_native_representation(self, native_value)


@configspec
class GcpOAuthCredentials(GcpCredentialsBase, OAuth2Credentials):
    # only desktop app supported
    refresh_token: TSecretValue
    client_type: Final[str] = "installed"

    def parse_native_representation(self, native_value: Any) -> None:
        """Accepts Google OAuth2 credentials as native value. In other case reverts to serialized oauth client secret json"""
        oauth_dict: DictStrAny = None
        try:
            from google.oauth2.credentials import Credentials as GoogleOAuth2Credentials
            if isinstance(native_value, GoogleOAuth2Credentials):
                # extract credentials, project id may not be present
                oauth_dict = {
                    "project_id": native_value.quota_project_id or "",
                    "client_id": native_value.client_id,
                    "client_secret": native_value.client_secret,
                    "refresh_token": native_value.refresh_token,
                    "scopes": native_value.scopes,
                    "token": native_value.token
                }
        except ImportError:
            pass

        if oauth_dict is None:
            # check if type is str
            GcpCredentialsBase.parse_native_representation(self, native_value)
            # if not instance of oauth2 credentials try to parse native value
            try:
                oauth_dict = json.loads(native_value)
                # if there's single element in the dict, this is probably "installed" or "web" etc. (app type)
                if len(oauth_dict) == 1:
                    oauth_dict = next(iter(oauth_dict.values()))
            except Exception:
                raise InvalidGoogleOauth2Json(self.__class__, native_value)
        self._from_info_dict(oauth_dict)

    def to_native_representation(self) -> str:
        return json.dumps(self._info_dict())

    def auth(self, scopes: Union[str, List[str]] = None, redirect_url: str = None) -> None:
        if not self.refresh_token:
            self.add_scopes(scopes)
            if not self.scopes:
                raise OAuth2ScopesRequired(self.__class__)
            assert sys.stdin.isatty() or is_interactive(), "Must have a tty or interactive mode for web flow"
            self.refresh_token, self.token = self._get_refresh_token(redirect_url or "http://localhost")
        else:
            # if scopes or redirect_url:
            #     logger.warning("Please note that scopes and redirect_url are ignored when getting access token")
            self.token = self._get_access_token()

    def on_partial(self) -> None:
        """Allows for an empty refresh token if the session is interactive or tty is attached"""
        if sys.stdin.isatty() or is_interactive():
            self.refresh_token = TSecretValue("")
            # still partial - raise
            if not self.is_partial():
                self.__is_resolved__ = True
            self.refresh_token = None

    def _get_access_token(self) -> TSecretValue:
        try:
            from requests_oauthlib import OAuth2Session
        except ImportError:
            raise MissingDependencyException("Requests-OAuthlib", ["requests_oauthlib"])

        google = OAuth2Session(client_id=self.client_id, scope=self.scopes)
        extra = {
            "client_id": self.client_id,
            "client_secret": self.client_secret
        }
        token = google.refresh_token(token_url=self.token_uri, refresh_token=self.refresh_token, **extra)["access_token"]
        return TSecretValue(token)

    def _get_refresh_token(self, redirect_url: str) -> Tuple[TSecretValue, TSecretValue]:
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
        except ImportError:
            raise MissingDependencyException("Google Auth library", ["google-auth-oauthlib"])
        flow = InstalledAppFlow.from_client_config(self._installed_dict(redirect_url), self.scopes)
        credentials = flow.run_local_server(port=0)
        return TSecretValue(credentials.refresh_token), TSecretValue(credentials.token)

    def to_google_credentials(self) -> Any:
        """
        Will convert the object to a Google oauth2 credentials object
        :returns: Google Credentials object
        """
        try:
            from google.oauth2.credentials import Credentials as GoogleOAuth2Credentials
        except ImportError:
            raise MissingDependencyException("Google OAuth Library", ["google-auth-oauthlib"])

        credentials = GoogleOAuth2Credentials.from_authorized_user_info(info=dict(self))
        return credentials

    def _installed_dict(self, redirect_url: str = "http://localhost") -> StrAny:
        installed_dict = {
            self.client_type: self._info_dict()
        }

        if redirect_url:
            installed_dict[self.client_type]["redirect_uris"] = [redirect_url]
        return installed_dict

    def _info_dict(self) -> DictStrAny:
        info_dict = dict(self)
        # for desktop app
        info_dict["redirect_uris"] = ["http://localhost"]
        return info_dict

    def __str__(self) -> str:
        return f"{self.client_id}@{self.project_id}[{self.location}]"


@configspec
class GcpOAuthCredentialsWithDefault(GcpDefaultCredentials, GcpOAuthCredentials):
    def parse_native_representation(self, native_value: Any) -> None:
        try:
            GcpDefaultCredentials.parse_native_representation(self, native_value)
        except NativeValueError:
            pass
        GcpOAuthCredentials.parse_native_representation(self, native_value)
