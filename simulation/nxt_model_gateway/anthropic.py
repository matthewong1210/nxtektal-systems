"""Anthropic Messages adapter; tool input is validated data, never executed."""

from collections.abc import Mapping

from .adapters import PreparedRequest, _BaseAdapter, _malformed, _refused, _usage
from .contracts import FailureCode, GatewayContractError, GenerationRequest, MessageRole, Provider
from .serialization import canonical_json, decode_validated_json
from .transport import _ANTHROPIC_ENDPOINT


class AnthropicAdapter(_BaseAdapter):
    provider = Provider.ANTHROPIC
    _endpoint = _ANTHROPIC_ENDPOINT

    def prepare(self, request: GenerationRequest) -> PreparedRequest:
        messages = request.messages
        system = None
        if messages[0].role is MessageRole.SYSTEM:
            system = messages[0].content
            messages = messages[1:]
        if any(message.role is MessageRole.SYSTEM for message in messages):
            raise GatewayContractError(FailureCode.INVALID_PROVIDER_REQUEST,
                                       'only one leading system message is allowed')
        payload = {
            'model': self.model_id, 'max_tokens': request.max_output_tokens,
            'messages': [{'role': m.role.value, 'content': m.content} for m in messages],
            'tools': [{'name': 'emit_structured_output',
                       'description': 'Return only the JSON object requested by the caller.',
                       'strict': True,
                       'input_schema': request.output_schema}],
            'tool_choice': {'type': 'tool', 'name': 'emit_structured_output'},
        }
        if system is not None:
            payload['system'] = system
        headers = {'x-api-key': self._config.api_key, 'anthropic-version': '2023-06-01',
                   'Content-Type': 'application/json', 'Accept': 'application/json'}
        return self._prepare(request, payload, headers)

    def _parse(self, envelope, schema):
        stop = envelope.get('stop_reason')
        if stop == 'refusal':
            raise _refused()
        if stop != 'tool_use' or not isinstance(envelope.get('content'), tuple):
            raise _malformed()
        tools = [block for block in envelope['content'] if block['type'] == 'tool_use']
        if len(tools) != 1 or tools[0].get('name') != 'emit_structured_output':
            raise _malformed()
        value = tools[0].get('input')
        if not isinstance(value, Mapping):
            raise _malformed()
        decoded = decode_validated_json(canonical_json(value).encode('utf-8'), schema=schema)
        return decoded, envelope.get('id'), stop, _usage(envelope, 'input_tokens', 'output_tokens', derive_total=True)
