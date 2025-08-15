import logging
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("snapshot")

def add_watermark(image_path: str, text: str,
                  opacity: float = 0.5,
                  color=(128, 128, 128),
                  outline_color=(255, 255, 255),
                  outline_width: int = 1):
    """Tambah watermark teks ke gambar dengan outline & transparansi."""
    base_image = temp_image = watermarked_image = None
    try:
        base_image = Image.open(image_path).convert("RGBA")
        temp_image = Image.new("RGBA", base_image.size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(temp_image)

        font = _get_font(int(min(base_image.size) * 0.05))
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        x = (base_image.width - text_width) / 2
        y = (base_image.height - text_height) / 2

        text_fill = color + (int(255 * opacity),)
        outline_fill = outline_color + (int(255 * opacity),)

        _draw_text_with_outline(draw, text, x, y, font, text_fill, outline_fill, outline_width)
        watermarked_image = Image.alpha_composite(base_image, temp_image)
        watermarked_image.convert("RGB").save(image_path)

        logger.info("Watermark added to %s", image_path)
    except Exception as e:
        logger.error("Failed to add watermark to %s: %s", image_path, e)
        raise
    finally:
        for img in (base_image, temp_image, watermarked_image):
            if img:
                img.close()


_font_cache = {}

def _get_font(size: int) -> ImageFont.FreeTypeFont:
    if size in _font_cache:
        return _font_cache[size]
    try:
        font_path = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
        font = ImageFont.truetype(font_path, size)
    except IOError:
        logger.warning("Liberation Sans not found, using default font.")
        font = ImageFont.load_default()
    _font_cache[size] = font
    return font



def _draw_text_with_outline(draw, text, x, y, font, fill, outline_fill, outline_width):
    for dx in range(-outline_width, outline_width + 1):
        for dy in range(-outline_width, outline_width + 1):
            if dx == 0 and dy == 0:
                continue
            draw.text((x + dx, y + dy), text, font=font, fill=outline_fill)
    draw.text((x, y), text, font=font, fill=fill)
