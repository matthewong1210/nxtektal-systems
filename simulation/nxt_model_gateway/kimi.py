"""Kimi Chat Completions structured-output adapter."""

from .adapters import PreparedRequest, _BaseAdapter, _malformed, _refused, _usage
from .contracts import GenerationRequest, Provider
from .serialization import decode_validated_json
from .transport import _KIMI_ENDPOINT


class KimiAdapter(_BaseAdapter):
    provider = Provider.KIMI
    _endpoint = _KIMI_ENDPOINT

    def prepare(self, request: GenerationRequest) -> PreparedRequest:
        payload = {
            'model': self.model_id,
            'messages': [{'role': m.role.value, 'content': m.content} for m in request.messages],
            'max_tokens': request.max_output_tokens,
            'response_format': {'type': 'json_schema', 'json_schema': {
                'name': 'structured_output', 'strict': True, 'schema': request.output_schema,
            }},
            'stream': False,
        }
        return self._prepare(request, payload, self._bearer_headers())

    def _parse(self, envelope, schema):
        choices = envelope['choices']
        if not isinstance(choices, tuple) or not choices:
            raise _malformed()
        choice = choices[0]
        message = choice['message']
        finish = choice['finish_reason']
        if finish == 'content_filter' or message.get('refusal'):
            raise _refused()
        if finish != 'stop' or type(message.get('content')) is not str:
            raise _malformed()
        decoded = decode_validated_json(message['content'].encode('utf-8'), schema=schema)
        return decoded, envelope.get('id'), finish, _usage(envelope, 'prompt_tokens', 'completion_tokens')
