"""One image-format contract shared by inbound capture and outbound sending.

Inbound accepts a superset (BMP/TIFF are decoded to PNG when saved), so what
the project stores and what it can send are the same four containers.
"""
MAX_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 50_000_000

INBOUND_IMAGE = ('PNG', 'JPEG', 'GIF', 'WEBP', 'BMP', 'TIFF', 'HEIF')
OUTBOUND_IMAGE = ('PNG', 'JPEG', 'GIF', 'WEBP')

SUFFIX = {'PNG': '.png', 'JPEG': '.jpg', 'GIF': '.gif', 'WEBP': '.webp'}
INBOUND_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.tif', '.tiff', '.heic', '.heif'}
OUTBOUND_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}


def save_suffix(fmt):
    """Suffix used on disk; BMP/TIFF are stored as PNG so storage matches outbound."""
    if fmt in SUFFIX:
        return SUFFIX[fmt]
    if fmt in ('BMP', 'TIFF', 'HEIF'):
        return '.png'
    return ''


def outbound_suffix(fmt):
    return SUFFIX.get(fmt, '')


def stored_as_is(fmt):
    """True when the incoming bytes can be written unchanged."""
    return fmt in SUFFIX


def outbound_extension_ok(path):
    return str(path).lower().endswith(tuple(OUTBOUND_EXTENSIONS))


def open_inbound(data):
    """Detect HEIC by bytes (SDK often labels it JPEG), without trusting suffix."""
    import io
    from PIL import Image
    if data[4:8]==b'ftyp' and any(brand in data[8:40] for brand in (b'heic',b'heix',b'hevc',b'hevx',b'mif1',b'msf1')):
        try:
            from pillow_heif import register_heif_opener
        except ImportError:raise ValueError('HEIC decoder missing: install pillow-heif') from None
        register_heif_opener(thumbnails=False)
    return Image.open(io.BytesIO(data))
