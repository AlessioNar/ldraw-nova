"""OpenAI device-code sign-in worker. stdout carries UI instructions, never credentials."""
import json
import os

from litellm.llms.chatgpt.authenticator import Authenticator
from litellm.llms.chatgpt.common_utils import CHATGPT_DEVICE_VERIFY_URL


def emit(**event):
    print(json.dumps(event), flush=True)


class DeviceAuthenticator(Authenticator):
    def login(self):
        code = self._request_device_code()
        emit(type="login", flow="device", url=CHATGPT_DEVICE_VERIFY_URL, code=code["user_code"])
        authorization = self._poll_for_authorization_code(code)
        tokens = self._exchange_code_for_tokens(authorization)
        self._write_auth_file(self._build_auth_record(tokens))


if __name__ == "__main__":
    os.umask(0o077)
    try:
        DeviceAuthenticator().login()
    except Exception:
        # Exceptions and runtime output may contain auth responses: never relay them.
        emit(type="login_error", message="OpenAI sign-in did not complete.")
        raise SystemExit(1)
