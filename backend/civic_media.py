"""Validate decoded user images and discard metadata before durable storage."""
from io import BytesIO
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 20_000_000


class MediaError(ValueError):
    pass


def sanitize_image(content):
    if not isinstance(content, bytes) or not 1 <= len(content) <= MAX_UPLOAD_BYTES:
        raise MediaError('Photographs must be no larger than 5 MB.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as image:
                if image.format not in {'JPEG', 'PNG', 'WEBP'} or getattr(image, 'n_frames', 1) != 1:
                    raise MediaError('Choose a non-animated JPEG, PNG or WebP photograph.')
                if image.width * image.height > MAX_PIXELS:
                    raise MediaError('The image dimensions are too large.')
                image.verify()
            with Image.open(BytesIO(content)) as image:
                image.load()
                oriented = ImageOps.exif_transpose(image)
                oriented.thumbnail((2000, 2000))
                # A new RGB image has no EXIF, GPS, ICC, PNG text or other
                # inherited metadata, even when the input decoder preserves it.
                clean = Image.new('RGB', oriented.size, (255, 255, 255))
                if oriented.mode in {'RGBA', 'LA'} or 'transparency' in oriented.info:
                    rgba = oriented.convert('RGBA')
                    clean.paste(rgba, mask=rgba.getchannel('A'))
                else:
                    clean.paste(oriented.convert('RGB'))
                output = BytesIO()
                clean.save(output, format='JPEG', quality=85, optimize=True)
                result = output.getvalue()
                if len(result) > MAX_UPLOAD_BYTES:
                    raise MediaError('The processed photograph is too large.')
                return result
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombWarning, Image.DecompressionBombError) as error:
        if isinstance(error, MediaError):
            raise
        raise MediaError('The file is not a supported, complete photograph.') from None
