"""Optional OpenAI-compatible embeddings; stdlib only, never auto-download weights."""
import hashlib
import json
import math
import struct
import time
import urllib.request

from . import model_endpoint


def normalized(values):
    if (not isinstance(values, list) or not 1 <= len(values) <= 4096
            or any(type(v) not in (float, int) for v in values)):
        raise ValueError('invalid_embedding')
    try:
        values = [float(v) for v in values]
    except OverflowError as error:
        raise ValueError('invalid_embedding') from error
    if any(not math.isfinite(v) for v in values):
        raise ValueError('invalid_embedding')
    length = math.hypot(*values)
    if not math.isfinite(length) or length <= 0:
        raise ValueError('invalid_embedding')
    return [v / length for v in values]


def pack(values):
    return struct.pack('<' + 'f' * len(values), *values)


def unpack(blob):
    if not blob or len(blob) % 4 or len(blob) > 4096 * 4:
        raise ValueError('invalid_embedding')
    return normalized(list(struct.unpack('<' + 'f' * (len(blob) // 4), blob)))


class EmbeddingClient:
    def __init__(self, settings):
        self.config = {'base_url': model_endpoint.endpoint(settings['embeddingBaseUrl']),
                       'model': settings['embeddingModel'].strip(),
                       'api_key': settings.get('embeddingApiKey', '').strip()}
        if not self.config['model']:
            raise ValueError('missing_embedding_model')
        self.query_prefix = settings.get('embeddingQueryPrefix', '为这个句子生成表示以用于检索相关文章：')
        self.document_prefix = settings.get('embeddingDocumentPrefix', '')
        identity = [self.config['base_url'], self.config['model'],
                    settings.get('embeddingVersion', '1'), self.query_prefix, self.document_prefix]
        self.signature = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()

    def encode(self, text, deadline, query=False):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('embedding_deadline')
        prefix = self.query_prefix if query else self.document_prefix
        body = {'model': self.config['model'], 'input': prefix + text, 'encoding_format': 'float'}
        request = urllib.request.Request(self.config['base_url'] + '/embeddings',
            data=json.dumps(body).encode(), headers={'Content-Type': 'application/json',
                                                     **model_endpoint.headers(self.config)})
        with model_endpoint.opener().open(request, timeout=min(remaining, 5)) as response:
            raw = response.read(262145)
        if len(raw) > 262144:
            raise ValueError('invalid_embedding_response')
        data = json.loads(raw)
        vector = normalized(data['data'][0]['embedding'])
        if time.monotonic() > deadline:
            raise TimeoutError('embedding_deadline')
        return vector


def configured(settings):
    return EmbeddingClient(settings) if settings.get('embeddingBaseUrl', '').strip() else None
