import base64
import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from django.conf import settings
from django.db.utils import OperationalError, ProgrammingError

from apps.security.crypto import SecretDecryptionError, decrypt_secret

from .models import AIProviderConfiguration


class AdmiezoAIError(RuntimeError):
    pass


def _stored_api_key(tenant_id):
    if not tenant_id:
        return ""
    try:
        configuration = AIProviderConfiguration.objects.filter(tenant_id=tenant_id, is_active=True).first()
    except (OperationalError, ProgrammingError):
        return ""
    if not configuration:
        return ""
    try:
        return decrypt_secret(configuration.api_key_ciphertext)
    except SecretDecryptionError:
        return ""


def _provider_model(model):
    if not model or model.startswith("admiezo-"):
        return settings.ADMIEZO_AI_PROVIDER_MODEL
    return model


class AdmiezoAIClient:
    def __init__(self, *, tenant_id=None, api_key=None, base_url=None, timeout=None):
        self.api_key = api_key if api_key is not None else _stored_api_key(tenant_id)
        self.base_url = (base_url or settings.ADMIEZO_AI_PROVIDER_BASE).rstrip("/")
        self.timeout = timeout or settings.ADMIEZO_AI_REQUEST_TIMEOUT_SECONDS

    @property
    def configured(self):
        return bool(self.api_key)

    def validate_model(self, model):
        if not self.configured:
            raise AdmiezoAIError("ADMIEZO AI Assistant API key is not configured")
        model = _provider_model(model)
        request = Request(
            f"{self.base_url}/models/{quote(model, safe='-_.')}",
            method="GET",
            headers={"x-goog-api-key": self.api_key},
        )
        try:
            with urlopen(request, timeout=min(float(self.timeout), 5.0)) as response:
                if response.status != 200:
                    raise AdmiezoAIError("ADMIEZO AI Assistant validation failed")
        except HTTPError as exc:
            raise AdmiezoAIError(f"ADMIEZO AI Assistant rejected the configured API key ({exc.code})") from exc
        except (URLError, TimeoutError) as exc:
            raise AdmiezoAIError("ADMIEZO AI Assistant could not be validated") from exc

    def _generate(self, *, model, prompt, media, schema, system_instruction):
        if not self.configured:
            raise AdmiezoAIError("ADMIEZO AI Assistant is not configured by the platform administrator")
        model = _provider_model(model)
        parts = [{"text": prompt}]
        parts.extend(
            {"inlineData": {"mimeType": item["mime_type"], "data": base64.b64encode(item["data"]).decode("ascii")}}
            for item in media
        )
        payload = {
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": 0.1,
                "responseMimeType": "application/json",
                "responseSchema": schema,
            },
        }
        request = Request(
            f"{self.base_url}/models/{quote(model, safe='-_.')}:generateContent",
            data=json.dumps(payload).encode(),
            method="POST",
            headers={"Content-Type": "application/json", "x-goog-api-key": self.api_key},
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode())
        except HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:400]
            raise AdmiezoAIError(f"ADMIEZO AI Assistant request failed ({exc.code}): {detail}") from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise AdmiezoAIError(f"ADMIEZO AI Assistant request failed: {exc}") from exc
        try:
            text = body["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(text)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise AdmiezoAIError("ADMIEZO AI Assistant returned an unusable structured response") from exc

    def evaluate(self, *, model, prompt, media):
        schema = {
            "type": "OBJECT",
            "properties": {
                "overall_confidence": {"type": "NUMBER", "minimum": 0, "maximum": 100},
                "summary": {"type": "STRING"},
                "assessments": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "question_id": {"type": "STRING"},
                            "marks": {"type": "NUMBER"},
                            "confidence": {"type": "NUMBER", "minimum": 0, "maximum": 100},
                            "feedback": {"type": "STRING"},
                            "reasoning": {"type": "STRING"},
                        },
                        "required": ["question_id", "marks", "confidence", "feedback", "reasoning"],
                    },
                },
            },
            "required": ["overall_confidence", "summary", "assessments"],
        }
        return self._generate(
            model=model,
            prompt=prompt,
            media=media,
            schema=schema,
            system_instruction=(
                "You are an independent university answer-script evaluator. Work only from the supplied masked script, "
                "question configuration, marking guidance, question paper, and reference answers. Award defensible "
                "question-wise marks, never infer candidate identity, and lower confidence whenever pages or answers are unclear."
            ),
        )

    def extract_questions(self, *, model, media):
        schema = {
            "type": "OBJECT",
            "properties": {
                "questions": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "number": {"type": "STRING"},
                            "sub_question": {"type": "STRING"},
                            "text": {"type": "STRING"},
                        },
                        "required": ["number", "sub_question", "text"],
                    },
                }
            },
            "required": ["questions"],
        }
        return self._generate(
            model=model,
            prompt="Extract every question exactly as printed. Preserve question and sub-question labels.",
            media=media,
            schema=schema,
            system_instruction="Transcribe the supplied university question paper accurately. Do not answer the questions.",
        )
