"""One image-format contract shared by inbound capture and outbound sending.

Inbound accepts a superset (BMP/TIFF are decoded to PNG when saved), so what
the project stores and what it can send are the same four containers.
"""
MAX_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 50_000_000

INBOUND_IMAGE = ('PNG', 'JPEG', 'GIF', 'WEBP', 'BMP', 'TIFF')
OUTBOUND_IMAGE = ('PNG', 'JPEG', 'GIF', 'WEBP')

SUFFIX = {'PNG': '.png', 'JPEG': '.jpg', 'GIF': '.gif', 'WEBP': '.webp'}
INBOUND_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.tif', '.tiff'}
OUTBOUND_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}


def save_suffix(fmt):
    """Suffix used on disk; BMP/TIFF are stored as PNG so storage matches outbound."""
    if fmt in SUFFIX:
        return SUFFIX[fmt]
    if fmt in ('BMP', 'TIFF'):
        return '.png'
    return ''


def outbound_suffix(fmt):
    return SUFFIX.get(fmt, '')


def stored_as_is(fmt):
    """True when the incoming bytes can be written unchanged."""
    return fmt in SUFFIX


def outbound_extension_ok(path):
    return str(path).lower().endswith(tuple(OUTBOUND_EXTENSIONS))
