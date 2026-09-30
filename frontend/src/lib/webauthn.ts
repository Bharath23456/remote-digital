function decodeBase64Url(value: string) {
  const normalized = value.replaceAll("-", "+").replaceAll("_", "/");
  const binary = window.atob(normalized.padEnd(Math.ceil(normalized.length / 4) * 4, "="));
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

function encodeBase64Url(value: ArrayBuffer) {
  const bytes = new Uint8Array(value);
  let binary = "";
  bytes.forEach((byte) => { binary += String.fromCharCode(byte); });
  return window.btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
}

import { safeRandomUUID } from "./crypto-compat";

export function deviceContext() {
  const key = "admiezo-device-id";
  let deviceId = window.localStorage.getItem(key);
  if (!deviceId) {
    deviceId = safeRandomUUID();
    window.localStorage.setItem(key, deviceId);
  }
  return {
    device_id: deviceId,
    device_label: `${navigator.platform || "Device"} · ${navigator.userAgent.includes("Mobile") ? "Mobile browser" : "Browser"}`,
    platform: navigator.platform || "Web",
    browser: navigator.userAgent.slice(0, 80),
  };
}

type RegistrationOptions = PublicKeyCredentialCreationOptions & {
  challenge: string;
  user: PublicKeyCredentialUserEntity & { id: string };
  excludeCredentials?: (PublicKeyCredentialDescriptor & { id: string })[];
};

type AuthenticationOptions = PublicKeyCredentialRequestOptions & {
  challenge: string;
  allowCredentials?: (PublicKeyCredentialDescriptor & { id: string })[];
};

export async function createPasskey(options: RegistrationOptions) {
  const publicKey: PublicKeyCredentialCreationOptions = {
    ...options,
    challenge: decodeBase64Url(options.challenge),
    user: { ...options.user, id: decodeBase64Url(options.user.id) },
    excludeCredentials: options.excludeCredentials?.map((item) => ({ ...item, id: decodeBase64Url(String(item.id)) })),
  };
  const credential = await navigator.credentials.create({ publicKey }) as PublicKeyCredential | null;
  if (!credential) throw new Error("Passkey registration was cancelled");
  const response = credential.response as AuthenticatorAttestationResponse;
  return {
    id: credential.id,
    rawId: encodeBase64Url(credential.rawId),
    type: credential.type,
    authenticatorAttachment: credential.authenticatorAttachment,
    clientExtensionResults: credential.getClientExtensionResults(),
    response: {
      attestationObject: encodeBase64Url(response.attestationObject),
      clientDataJSON: encodeBase64Url(response.clientDataJSON),
      transports: response.getTransports?.() || [],
    },
  };
}

export async function getPasskey(options: AuthenticationOptions) {
  const publicKey: PublicKeyCredentialRequestOptions = {
    ...options,
    challenge: decodeBase64Url(options.challenge),
    allowCredentials: options.allowCredentials?.map((item) => ({ ...item, id: decodeBase64Url(String(item.id)) })),
  };
  const credential = await navigator.credentials.get({ publicKey }) as PublicKeyCredential | null;
  if (!credential) throw new Error("Passkey authentication was cancelled");
  const response = credential.response as AuthenticatorAssertionResponse;
  return {
    id: credential.id,
    rawId: encodeBase64Url(credential.rawId),
    type: credential.type,
    authenticatorAttachment: credential.authenticatorAttachment,
    clientExtensionResults: credential.getClientExtensionResults(),
    response: {
      authenticatorData: encodeBase64Url(response.authenticatorData),
      clientDataJSON: encodeBase64Url(response.clientDataJSON),
      signature: encodeBase64Url(response.signature),
      userHandle: response.userHandle ? encodeBase64Url(response.userHandle) : null,
    },
  };
}
