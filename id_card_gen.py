VERSION = 1.5
AUTH = "ZD"

import hashlib
import copy
import io
import qrcode
import os
import time
import multiprocessing as mp
from datetime import datetime
from pathlib import Path
import pandas as pd
from tqdm import tqdm
from PIL import Image, ImageDraw, ImageFont
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from PyPDF2 import PdfWriter, PdfReader

from PIL import features

print("RAQM:", features.check("raqm"))
print("HarfBuzz:", features.check_feature("harfbuzz"))
print("FriBidi:", features.check_feature("fribidi"))


TEMPLATE = r"BBA Identity Card_Final v4.pdf"
SECRET_KEY = 'UzFOaGJYbEpTVlJOVUU5RQ'

def generate_student_hash(roll_number, secret_key=SECRET_KEY):
    """Generate a hash for student ID verification"""
    key = str(roll_number) + str(secret_key)
    m = hashlib.md5()
    m.update(key.encode("utf-8"))
    return m.hexdigest()

def create_placeholder_image():
    """Create a placeholder image and return as PIL Image"""
    img = Image.new('RGB', (300, 400), (240, 240, 240))
    draw = ImageDraw.Draw(img)
    draw.text((150, 200), "Photo\nNot Available", fill=(100, 100, 100), anchor="mm")
    return img

def generate_qr_code(data):
    """Generate a QR code and return as PIL Image"""
    try:
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size= 10, #10,
            border=4,#4
        )
        qr.add_data(data)
        qr.make(fit=True)
        return qr.make_image(fill_color="black", back_color="white")
    except Exception as e:
        print(f"Error generating QR code: {e}")
        return create_placeholder_image()


def format_address_lines(student_data):
    """Return the address as up to three compact display lines."""
    address_line1 = str(student_data.get('street_address_line_1', '')).strip() if not pd.isna(student_data.get('street_address_line_1', '')) else ""
    address_line2 = str(student_data.get('street_address_line_2', '')).strip() if not pd.isna(student_data.get('street_address_line_2', '')) else ""
    city = str(student_data.get('city', '')).strip().title() if not pd.isna(student_data.get('city', '')) else ""
    state = str(student_data.get('state', '')).strip().title() if not pd.isna(student_data.get('state', '')) else ""
    country = str(student_data.get('country', '')).strip().title() if not pd.isna(student_data.get('country', '')) else ""
    pincode = str(student_data.get('pincode', '')).strip() if not pd.isna(student_data.get('pincode', '')) else ""
    
    location = ", ".join(part for part in [city, state, country, pincode] if part)
    lines = [part.title() for part in [address_line1, address_line2, location] if part]
    lines += [""] * (3 - len(lines))
    if not any(lines):
        lines[0] = "NaN"
    return {
        "address_line1": lines[0],
        "address_line2": lines[1],
        "address_line3": lines[2],
    }




def fix_image_orientation(img):
    """Fix image orientation based on EXIF data"""
    try:
        # Check if image has EXIF data
        if hasattr(img, '_getexif') and img._getexif() is not None:
            exif = img._getexif()
            orientation = exif.get(0x0112)  # Orientation tag
            
            if orientation == 2:
                img = img.transpose(Image.FLIP_LEFT_RIGHT)
            elif orientation == 3:
                img = img.rotate(180, expand=True)
            elif orientation == 4:
                img = img.rotate(180, expand=True).transpose(Image.FLIP_LEFT_RIGHT)
            elif orientation == 5:
                img = img.rotate(-90, expand=True).transpose(Image.FLIP_LEFT_RIGHT)
            elif orientation == 6:
                img = img.rotate(-90, expand=True)
            elif orientation == 7:
                img = img.rotate(90, expand=True).transpose(Image.FLIP_LEFT_RIGHT)
            elif orientation == 8:
                img = img.rotate(90, expand=True)
        
        return img
    except Exception as e:
        print(f"Error fixing image orientation: {e}")
        return img

def _image_to_buffer(img: Image.Image) -> io.BytesIO:
    """Return a BytesIO buffer for ReportLab. Uses PNG if image has alpha."""
    try:
        has_alpha = (img.mode in ("RGBA", "LA")) or ("transparency" in img.info)
        buf = io.BytesIO()
        if has_alpha:
            img.convert("RGBA").save(buf, format="PNG")
        else:
            img.convert("RGB").save(buf, format="JPEG", quality=90)
        buf.seek(0)
        return buf
    except Exception as e:
        # Fallback: force RGB PNG
        print(f"Image buffer fallback due to error: {e}")
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="PNG")
        buf.seek(0)
        return buf


BASE_DIR = Path(__file__).resolve().parent
INPUT_CSV = BASE_DIR / "input.csv"
OUTPUT_PDF = BASE_DIR / "generated_id_cards.pdf"
OUTPUT_DIR = BASE_DIR / "IIMU_ID_CARDS"
PHOTO_DIR = BASE_DIR / "resources" / "pics"
SIGN_DIR = BASE_DIR / "resources" / "sins"

# Coordinates are PDF points from the bottom-left of the portrait A4 page.
PHOTO_BOX = (8, 36, 56.2, 69.3)
SIGN_BOX = (101, 25, 58, 20)
QR_BOX = (180, 53, 43, 43)

level_traslate_dict ={"CERTIFICATE":"प्रमाणपत्र",
                      "DIPLOMA":"डिप्लोमा",
                      "BBA DEGREE": "बीबीए डिग्री",
                      "BBA HONOURS": "बीबीए सम्मान"}
LEVEL2_FONT_PATH = (BASE_DIR / "Noto_Sans_Devanagari" / "NotoSansDevanagari-VariableFont_wdth,wght.ttf")

PAGE_1_TEXT = {
    "level": (92, 59.6, 5),
    "level2": (89, 67.3, 6),
    "enrollment_no": (138, 49.7, 5),
}
PAGE_2_TEXT = {
    "dob": (68, 104.5, 5),
    "mobile": (53, 94.7, 5),
    "student_email": (44, 85, 5),
    "address": [(15, 61, 5), (15, 55, 5), (15, 49, 5)],
}


def _value(student, key):
    """Return a CSV value as clean display text."""
    value = student.get(key, "")
    return "" if pd.isna(value) else str(value).strip()


def _find_resource(directory, filename):
    """Find a resource whether the CSV includes its file extension or not."""
    if not filename:
        return None
    path = directory / filename
    if path.is_file():
        return path
    matches = list(directory.glob(f"{filename}.*"))
    return matches[0] if matches else None


def _fit_image(image, width_mm, height_mm):
    """Resize and crop an image to exactly fill a placement box."""
    target_width = max(1, round(width_mm * 4))
    target_height = max(1, round(height_mm * 4))
    image = fix_image_orientation(image).convert("RGB")
    source_ratio = image.width / image.height
    target_ratio = target_width / target_height
    if source_ratio > target_ratio:
        crop_width = round(image.height * target_ratio)
        left = (image.width - crop_width) // 2
        image = image.crop((left, 0, left + crop_width, image.height))
    else:
        crop_height = round(image.width / target_ratio)
        top = (image.height - crop_height) // 2
        image = image.crop((0, top, image.width, top + crop_height))
    return image.resize((target_width, target_height), Image.Resampling.LANCZOS)


def _resize_image(image, width, height):
    """Resize an image to the placement box without cropping it."""
    target_width = max(1, round(width * 4))
    target_height = max(1, round(height * 4))
    return fix_image_orientation(image).convert("RGB").resize(
        (target_width, target_height), Image.Resampling.LANCZOS
    )


def _draw_image(pdf, image, box):
    x, y, width, height = box
    pdf.drawImage(
        ImageReader(_image_to_buffer(image)),
        x,
        y,
        width,
        height,
        preserveAspectRatio=False,
        mask="auto",
    )


def _draw_text(pdf, value, position):
    x, y, font_size = position
    pdf.setFont("Helvetica", font_size)
    pdf.drawString(x, y, value)


def _draw_centered_text(pdf, value, y, font_size, page_width):
    pdf.setFont("Helvetica", font_size)
    pdf.drawCentredString((page_width / 2)+30, y, value)


def _draw_level2_text(pdf, value, position):
    """Render translated Hindi text as an image to preserve its glyph layout."""
    if not LEVEL2_FONT_PATH.is_file():
        _draw_text(pdf, value, position)
        return

    x, y, font_size = position
    scale = 8
    font = ImageFont.truetype(str(LEVEL2_FONT_PATH), max(1, round(font_size * scale)))
    bbox = font.getbbox(value, anchor="ls")
    padding = scale * 2
    image = Image.new(
        "RGBA",
        (
            max(1, bbox[2] - bbox[0] + padding * 2),
            max(1, bbox[3] - bbox[1] + padding * 2),
        ),
        (255, 255, 255, 0),
    )
    draw = ImageDraw.Draw(image)
    draw.text(
        (padding - bbox[0], padding - bbox[1]),
        value,
        font=font,
        fill="black",
        anchor="ls",
    )
    image_width = image.width / scale
    image_height = image.height / scale
    _draw_image(pdf, image, (x, y - image_height + font_size, image_width, image_height))


def _format_mobile(value):
    return value.split("_", 1)[0].strip()


def _format_dob(value):
    date_text = value.split(" UTC", 1)[0].strip()
    try:
        return datetime.strptime(date_text, "%Y-%m-%d %H:%M:%S.%f").strftime("%d-%m-%Y")
    except ValueError:
        try:
            return datetime.strptime(date_text, "%Y-%m-%d").strftime("%d-%m-%Y")
        except ValueError:
            return value


def _load_image(directory, filename, placeholder_size):
    path = _find_resource(directory, filename)
    if path is None:
        return create_placeholder_image().resize(placeholder_size)
    try:
        with Image.open(path) as image:
            return image.copy()
    except (OSError, ValueError) as error:
        print(f"Could not read {path}: {error}")
        return create_placeholder_image().resize(placeholder_size)


def _make_overlay(student, page_number, page_size):
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=page_size)
    if page_number == 1:
        photo = _load_image(PHOTO_DIR, _value(student, "photo"), (300, 400))
        sign = _load_image(SIGN_DIR, _value(student, "sign"), (300, 150))
        _draw_image(pdf, _fit_image(photo, PHOTO_BOX[2], PHOTO_BOX[3]), PHOTO_BOX)
        _draw_image(pdf, _resize_image(sign, SIGN_BOX[2], SIGN_BOX[3]), SIGN_BOX)
        _draw_centered_text(pdf, _value(student, "name"), 100, 8, page_size[0])
        for key, positions in PAGE_1_TEXT.items():
            if not isinstance(positions, list):
                positions = [positions]
            for position in positions:
                value = _value(student, "level") if key == "level2" else _value(student, key)
                if key == "level2":
                    value = level_traslate_dict.get(value, value)
                    _draw_level2_text(pdf, value, position)
                else:
                    _draw_text(pdf, value, position)
    else:
        student_hash = generate_student_hash(_value(student, "enrollment_no"))
        # qr_url = f"https://iimu-pgm-apps.el.r.appspot.com/student_id/{student_hash}"
        qr_url = f"https://iimu-pgm-apps.el.r.appspot.com/document_verification/{student_hash}"
        qr = generate_qr_code(qr_url)
        _draw_image(pdf, _fit_image(qr, QR_BOX[2], QR_BOX[3]), QR_BOX)
        for key, positions in PAGE_2_TEXT.items():
            if key == "address":
                address = format_address_lines(student)
                values = [
                    address["address_line1"],
                    address["address_line2"],
                    address["address_line3"],
                ]
            else:
                value = _value(student, key)
                if key == "mobile":
                    value = _format_mobile(value)
                elif key == "dob":
                    value = _format_dob(value)
                values = [value]
            if not isinstance(positions, list):
                positions = [positions]
            for position, value in zip(positions, values):
                _draw_text(pdf, value, position)
    pdf.save()
    buffer.seek(0)
    return PdfReader(buffer).pages[0]


def generate_id_cards(input_csv=INPUT_CSV, output_dir=OUTPUT_DIR):
    """Create one two-page PDF and one hash record for every student row."""
    template_path = BASE_DIR / TEMPLATE
    template = PdfReader(str(template_path))
    if len(template.pages) < 2:
        raise ValueError(f"Template must contain two pages: {template_path}")

    students = pd.read_csv(input_csv, dtype=str).fillna("")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_hashes = []
    resource_status = []
    page_size = (
        float(template.pages[0].mediabox.width),
        float(template.pages[0].mediabox.height),
    )
    start_time = time.perf_counter()
    for _, student in tqdm(students.iterrows(), total=len(students), desc="Generating ID cards", unit="card"):
        enrollment_no = _value(student, "enrollment_no")
        if not enrollment_no:
            print("Skipping row without enrollment_no")
            continue
        photo_found = _find_resource(PHOTO_DIR, _value(student, "photo")) is not None
        sign_found = _find_resource(SIGN_DIR, _value(student, "sign")) is not None
        writer = PdfWriter()
        student_hash = generate_student_hash(enrollment_no)
        for page_number in (1, 2):
            page = copy.deepcopy(template.pages[page_number - 1])
            page.merge_page(_make_overlay(student, page_number, page_size))
            writer.add_page(page)
        pdf_path = output_dir / f"{enrollment_no}.pdf"
        with pdf_path.open("wb") as output:
            writer.write(output)
        generated_hashes.append({"email": _value(student, "email"), "generated_hash_id": student_hash})
        resource_status.append({
            "email": _value(student, "email"),
            "photo": "found" if photo_found else "missing",
            "sign": "found" if sign_found else "missing",
        })

    pd.DataFrame(generated_hashes, columns=["email", "generated_hash_id"]).to_csv(
        output_dir / "output.csv", index=False
    )
    pd.DataFrame(resource_status, columns=["email", "photo", "sign"]).to_csv(
        output_dir / "resource_status.csv", index=False
    )
    elapsed = time.perf_counter() - start_time
    rate = len(generated_hashes) / elapsed * 60 if elapsed else 0
    print(f"Created {len(generated_hashes)} ID card(s) in {output_dir}")
    print(f"Total time: {elapsed:.2f} seconds ({rate:.2f} cards/min)")


if __name__ == "__main__":
    generate_id_cards()
