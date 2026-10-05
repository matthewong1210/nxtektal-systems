"""OpenAI Responses adapter; reasoning and tool calls are never answer sources."""

from .adapters import PreparedRequest, _BaseAdapter, _malformed, _refused, _usage
from .contracts import GenerationRequest, Provider
from .serialization import decode_validated_json
from .transport import _OPENAI_ENDPOINT


class OpenAIAdapter(_BaseAdapter):
    provider = Provider.OPENAI
    _endpoint = _OPENAI_ENDPOINT

    def prepare(self, request: GenerationRequest) -> PreparedRequest:
        payload = {
            'model': self.model_id,
            'input': [{'role': m.role.value, 'content': m.content} for m in request.messages],
            'max_output_tokens': request.max_output_tokens,
            'text': {'format': {'type': 'json_schema', 'name': 'structured_output',
                                'strict': True, 'schema': request.output_schema}},
            'store': False,
        }
        return self._prepare(request, payload, self._bearer_headers())

    def _parse(self, envelope, schema):
        if 'choices' in envelope:
            raise _malformed()
        if envelope.get('status') != 'completed':
            raise _malformed()
        output = envelope['output']
        if not isinstance(output, tuple):
            raise _malformed()
        documents = []
        for item in output:
            if item['type'] != 'message':
                continue
            if 'status' in item and item['status'] != 'completed':
                raise _malformed()
            content = item['content']
            if not isinstance(content, tuple):
                raise _malformed()
            for block in content:
                if block['type'] == 'refusal':
                    raise _refused()
                if block['type'] == 'output_text':
                    documents.append(block['text'])
        if len(documents) != 1 or type(documents[0]) is not str:
            raise _malformed()
        decoded = decode_validated_json(documents[0].encode('utf-8'), schema=schema)
        return decoded, envelope.get('id'), envelope['status'], _usage(envelope, 'input_tokens', 'output_tokens')
