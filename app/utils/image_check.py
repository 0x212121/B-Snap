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


def detect_occlusion(image_bytes, entropy_thresh=1.0):
    """
    Detects whether the image is too monotonous (possibly covered)
    """
    img = Image.open(BytesIO(image_bytes)).convert("L")  # grayscale
    arr = np.array(img)

    hist = np.histogram(arr, bins=256)[0]
    prob = hist / hist.sum()
    entropy = -np.sum(prob * np.log2(prob + 1e-7))  # +1e-7 untuk mencegah log(0)

    is_occluded = entropy < entropy_thresh
    return is_occluded, entropy