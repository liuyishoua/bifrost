"""Validated local image assets shared by tasks and conversations."""
import base64
import binascii
import io
import json
import re
import uuid

from PIL import Image, UnidentifiedImageError

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_UPLOAD_BODY = 4 * ((MAX_IMAGE_BYTES + 2) // 3) + 4096
FORMATS = {'PNG': ('.png', 'image/png'), 'JPEG': ('.jpg', 'image/jpeg'),
           'GIF': ('.gif', 'image/gif'), 'WEBP': ('.webp', 'image/webp')}


class Media:
    def __init__(self, service):
        self.directory = (service.db.directory / 'media').resolve()
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)

    def upload(self, data):
        if not isinstance(data, dict) or set(data) - {'data', 'name'}:
            raise ValueError('请上传本地图片文件')
        encoded, name = data.get('data'), data.get('name', '图片')
        if not isinstance(name, str) or len(name) > 255:
            raise ValueError('图片名称无效')
        if not isinstance(encoded, str) or not encoded or len(encoded) > 4 * ((MAX_IMAGE_BYTES + 2) // 3):
            raise ValueError('图片不能为空且不能超过 5 MiB')
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError('图片内容格式无效') from None
        if not raw or len(raw) > MAX_IMAGE_BYTES:
            raise ValueError('图片不能为空且不能超过 5 MiB')
        try:
            with Image.open(io.BytesIO(raw)) as img:
                fmt, width, height = img.format, img.width, img.height
                if fmt not in FORMATS:
                    raise ValueError('仅支持 PNG、JPEG、GIF、WebP 图片')
                if width * height > MAX_IMAGE_PIXELS:
                    raise ValueError('图片像素不能超过 2000 万')
                img.verify()
            # Decode after structural verification; animation has a total pixel budget.
            with Image.open(io.BytesIO(raw)) as img:
                pixels = 0
                for frame in range(getattr(img, 'n_frames', 1)):
                    img.seek(frame)
                    pixels += img.width * img.height
                    if pixels > MAX_IMAGE_PIXELS:
                        raise ValueError('图片总像素不能超过 2000 万')
                    img.load()
        except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError):
            raise ValueError('图片内容无效或无法完整解码') from None
        media_id = uuid.uuid4().hex
        name = name.replace('\\', '/').rsplit('/', 1)[-1]
        name = ''.join(c for c in name if c.isprintable()).strip() or '图片'
        metadata = {'id': media_id, 'name': name, 'width': width, 'height': height,
                    'size': len(raw), 'url': '/api/media/' + media_id}
        extension, _ = FORMATS[fmt]
        with (self.directory / (media_id + extension)).open('xb') as output:
            output.write(raw)
        with (self.directory / (media_id + '.json')).open('x', encoding='utf-8') as output:
            json.dump({**metadata, 'format': fmt}, output, ensure_ascii=False)
        return metadata

    def _record(self, media_id):
        if not isinstance(media_id, str) or not re.fullmatch('[0-9a-f]{32}', media_id):
            raise LookupError('图片不存在')
        try:
            return json.loads((self.directory / (media_id + '.json')).read_text(encoding='utf-8'))
        except FileNotFoundError:
            raise LookupError('图片不存在') from None

    def get(self, media_id):
        record = self._record(media_id)
        return {key: record[key] for key in ('id', 'name', 'width', 'height', 'size', 'url')}

    def path(self, media_id):
        record = self._record(media_id)
        path = self.directory / (media_id + FORMATS[record['format']][0])
        if not path.is_file():
            raise LookupError('图片不存在')
        return path
