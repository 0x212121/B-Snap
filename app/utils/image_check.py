import cv2
import numpy as np
from PIL import Image
from io import BytesIO


def detect_blur(image_bytes, blur_thresh=100.0):
    img = Image.open(BytesIO(image_bytes)).convert("L")
    arr = np.array(img)
    laplacian_var = cv2.Laplacian(arr, cv2.CV_64F).var()  # pylint: disable=no-member
    return laplacian_var < blur_thresh, float(laplacian_var)


def detect_brightness(image_bytes, dark_thresh=30, bright_thresh=220):
    img = Image.open(BytesIO(image_bytes)).convert("L")
    arr = np.array(img)
    mean_val = np.mean(arr)
    if mean_val < dark_thresh:
        return True, "too_dark"
    elif mean_val > bright_thresh:
        return True, "too_bright"
    return False, None


def sobel_energy(img):
    # Pastikan grayscale dan tipe uint8
    if len(img.shape) == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    img = img.astype(np.uint8)

    # Hitung gradien horizontal dan vertikal
    grad_x = cv2.Sobel(img, cv2.CV_64F, 1, 0, ksize=3)
    grad_y = cv2.Sobel(img, cv2.CV_64F, 0, 1, ksize=3)

    # Hitung magnitude energi
    energy = np.sqrt(grad_x**2 + grad_y**2)
    mean_energy = np.mean(energy)
    return mean_energy


def detect_occlusion(
    image_bytes,
    entropy_thresh=5.0,
    blur_thresh=100.0,
    stddev_thresh=20.0,
    sobel_thresh=10.0
):
    """
    Deteksi occlusion dengan kombinasi entropy, blur, kontras global, dan energi Sobel.
    """
    nparr = np.frombuffer(image_bytes, np.uint8)
    img_arr = cv2.imdecode(nparr, cv2.IMREAD_GRAYSCALE)

    if img_arr is None:
        raise ValueError("Failed to decode image bytes.")

    # Entropy
    hist = np.histogram(img_arr, bins=256, range=(0, 255))[0]
    prob = hist / hist.sum()
    entropy = -np.sum(prob * np.log2(prob + 1e-7))

    # Laplacian (blur detection)
    lap_var = cv2.Laplacian(img_arr, cv2.CV_64F).var()

    # Standard deviation (global contrast)
    std_dev = np.std(img_arr)

    # Sobel-based energy (local edge strength)
    sobel_mean = sobel_energy(img_arr)

    # Combine rules
    is_occluded = (
        (entropy < entropy_thresh and lap_var < blur_thresh)
        or std_dev < stddev_thresh
        or sobel_mean < sobel_thresh
    )

    return is_occluded, {
        "entropy": entropy,
        "lap_var": lap_var,
        "std_dev": std_dev,
        "sobel_mean": sobel_mean
    }