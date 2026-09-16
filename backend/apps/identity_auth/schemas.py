from ninja import Schema


class LoginIn(Schema):
    email: str
    password: str
    device_id: str = ""
    device_label: str = "Current device"
    platform: str = ""
    browser: str = ""
    location: str = ""


class SsoStartIn(Schema):
    provider_id: str
    device_id: str = ""
    device_label: str = "Institutional browser"
    platform: str = ""
    browser: str = ""
    location: str = ""


class MfaVerifyIn(Schema):
    code: str


class MfaEnrollmentConfirmIn(Schema):
    method_id: str
    code: str


class TotpSetupIn(Schema):
    label: str = "Authenticator"


class TotpConfirmIn(Schema):
    method_id: str
    code: str


class StepUpIn(Schema):
    password: str = ""
    code: str = ""


class PasswordChangeIn(Schema):
    new_password: str


class DeviceTrustIn(Schema):
    trusted_days: int = 30


class PasskeyCompleteIn(Schema):
    label: str = "Passkey"
    credential: dict


class PasskeyLoginBeginIn(Schema):
    email: str
    device_id: str = ""
    device_label: str = "Current device"
    platform: str = ""
    browser: str = ""


class PasskeyLoginCompleteIn(Schema):
    credential: dict
