import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from PIL import Image, ImageTk, ImageOps
from ttkthemes import ThemedTk
import threading
import os
import numpy as np
import cv2
from skimage import color, img_as_float, exposure
import scipy.ndimage
import re
import subprocess
import tkinter.font as font
import tifffile
import tempfile
import shutil
import math

RAW_EXTENSIONS = {'.cr2', '.nef', '.arw', '.dng', '.rw2', '.orf', '.raf'}

# --- Embedded processing functions (color + B&W) ---

cutoff_margin = 10
blur_size = (7, 7)
QuantumRange = 65535
GammaGlobal = 2.15
EPSILON = 1e-10  # Small value to prevent division by zero / log(0)

def sorted_nicely(l):  # Implement if not already
    import re
    convert = lambda text: int(text) if text.isdigit() else text.lower()
    alphanum_key = lambda key: [convert(c) for c in re.split('([0-9]+)', key)]
    return sorted(l, key=alphanum_key)

def normalize(image, low=2, high=99):
    float_img = img_as_float(image)
    p_low, p_high = np.percentile(float_img, (low, high))
    if p_low == p_high:
        p_low = max(0, p_low - EPSILON)
        p_high = p_high + EPSILON
    rescale = exposure.rescale_intensity(float_img, in_range=(p_low, p_high))
    max_val = rescale.max()
    if max_val < EPSILON:
        max_val = 1.0
    normalized = rescale * QuantumRange / max_val
    return normalized

def adjust_gamma(image, gamma):
    if gamma <= 0 or np.isnan(gamma) or np.isinf(gamma):
        gamma = 1.0
    inv_gamma = 1.0 / gamma
    table = ((np.arange(0, QuantumRange + 1) / QuantumRange) ** inv_gamma) * QuantumRange
    table = np.clip(table, 0, QuantumRange).astype(np.uint16)
    image_clipped = np.clip(image.astype(np.int32), 0, QuantumRange).astype(np.uint16)
    return table[image_clipped]

def adjust_channel(channel, minimum, gamma):
    normalized = channel / QuantumRange
    with np.errstate(divide='ignore', invalid='ignore'):
        power_term = np.power(normalized, -1.0)
    power_term = np.where(normalized > 0, power_term, 1.0)
    exponent = np.clip(minimum * power_term, 0, QuantumRange)
    output = exponent * QuantumRange
    clipped = np.clip(output, 0, QuantumRange)
    adjusted_channel = adjust_gamma(clipped, gamma)
    return adjusted_channel

def recompile_image(r_channel, g_channel, b_channel, gamma_subtract):
    image = cv2.merge([r_channel.astype(np.uint16), g_channel.astype(np.uint16), b_channel.astype(np.uint16)])
    image = adjust_gamma(image.astype(np.uint16), GammaGlobal)
    image = image.astype(np.int32) - gamma_subtract
    image = np.clip(image, 0, QuantumRange).astype(np.uint16)
    recompiled_image = cv2.normalize(image, None, alpha=0, beta=QuantumRange, norm_type=cv2.NORM_MINMAX)
    return recompiled_image

def autolevel_image(image):
    print(' Correcting levels...')
    image_gray = color.rgb2gray(image.astype(np.uint16))
    blackpoint = np.amin(image_gray) * QuantumRange
    whitepoint = np.amax(image_gray) * QuantumRange
    autolevel_mean = 100 * np.mean(image) / QuantumRange
    autolevel_midrange = 0.5
    if autolevel_mean <= 0:
        autolevel_mean = EPSILON
    autolevel_gamma = np.log(autolevel_mean / 100) / np.log(autolevel_midrange + EPSILON)
    image_hsv = color.rgb2hsv(image.astype(np.uint16))
    h_channel, s_channel, v_channel = cv2.split(image_hsv)
    v_channel = v_channel * QuantumRange
    v_channel = np.clip(v_channel, blackpoint, whitepoint)
    v_channel = cv2.normalize(v_channel, None, alpha=0, beta=QuantumRange, norm_type=cv2.NORM_MINMAX)
    v_channel = v_channel / QuantumRange
    output_hsv = cv2.merge((h_channel, s_channel, v_channel))
    autoleveled_image = color.hsv2rgb(output_hsv) * QuantumRange
    autoleveled_image = adjust_gamma(autoleveled_image.astype(np.uint16), autolevel_gamma)
    return autoleveled_image

def autocolor_image(image):
    print(' Correcting colors...')
    image_gray = color.rgb2gray(image.astype(np.uint16))
    r_channel, g_channel, b_channel = cv2.split(image)
    neutral_gray = np.mean(image_gray)
    r_mean = (np.mean(r_channel) / QuantumRange)
    g_mean = (np.mean(g_channel) / QuantumRange)
    b_mean = (np.mean(b_channel) / QuantumRange)
    r_mean = r_mean if r_mean > EPSILON else EPSILON
    g_mean = g_mean if g_mean > EPSILON else EPSILON
    b_mean = b_mean if b_mean > EPSILON else EPSILON
    r_ratio = neutral_gray / r_mean
    g_ratio = neutral_gray / g_mean
    b_ratio = neutral_gray / b_mean
    r_colorcorrected = np.clip((r_channel * r_ratio), 0, QuantumRange)
    r_colorcorrected = normalize(r_colorcorrected, 0.5, 99.5)
    g_colorcorrected = np.clip((g_channel * g_ratio), 0, QuantumRange)
    g_colorcorrected = normalize(g_colorcorrected, 0.5, 99.5)
    b_colorcorrected = np.clip((b_channel * b_ratio), 0, QuantumRange)
    b_colorcorrected = normalize(b_colorcorrected, 0.5, 99.5)
    autocolored_image = cv2.merge([r_colorcorrected.astype(np.uint16), g_colorcorrected.astype(np.uint16), b_colorcorrected.astype(np.uint16)])
    return autocolored_image

def negative_inversion(image_input):
    print(' Inverting negative...')
    r_input, g_input, b_input = cv2.split(image_input)
    image_crop = image_input[cutoff_margin:-cutoff_margin, cutoff_margin:-cutoff_margin]
    image_blur = cv2.blur(image_crop, blur_size)
    r_blur, g_blur, b_blur = cv2.split(image_blur)
    r_min = max(np.amin(r_blur) / QuantumRange, EPSILON)
    r_max = max(np.amax(r_blur) / QuantumRange, EPSILON)
    g_min = max(np.amin(g_blur) / QuantumRange, EPSILON)
    g_max = max(np.amax(g_blur) / QuantumRange, EPSILON)
    b_min = max(np.amin(b_blur) / QuantumRange, EPSILON)
    b_max = max(np.amax(b_blur) / QuantumRange, EPSILON)
    gamma_subtract = ((b_min / b_max) ** (1 / GammaGlobal)) * QuantumRange * 0.95
    try:
        gamma_for_r = np.log(r_max / r_min) / np.log(b_max / b_min)
    except Exception:
        gamma_for_r = 1.0
    try:
        gamma_for_g = np.log(g_max / g_min) / np.log(b_max / b_min)
    except Exception:
        gamma_for_g = 1.0
    gamma_for_b = 1.0
    r_adjusted = adjust_channel(r_input, r_min, gamma_for_r)
    g_adjusted = adjust_channel(g_input, g_min, gamma_for_g)
    b_adjusted = adjust_channel(b_input, b_min, gamma_for_b)
    inverted_negative = recompile_image(r_adjusted, g_adjusted, b_adjusted, gamma_subtract)
    autoleveled_negative = autolevel_image(inverted_negative)
    autocolored_negative = autocolor_image(autoleveled_negative)
    return autocolored_negative, r_input

def remove_noise(image, aggressiveness=7):
    normalized = image / QuantumRange
    inverted = 1 - normalized
    tmp = scipy.ndimage.convolve(inverted, np.ones((3, 3)), mode='constant')
    out = np.logical_and(tmp >= aggressiveness, inverted).astype(np.float32)
    reverted = 1 - out
    unnormalized = reverted * QuantumRange
    return unnormalized

def dust_removal(infrared_image, inverted_image, r_channel):
    print(' Removing dust...')
    normalized_infrared = normalize(infrared_image, 2, 99)
    normalized_red = normalize(r_channel, 2, 99)
    c = normalized_infrared / ((normalized_red.astype('float') + 1) / (QuantumRange + 1) + EPSILON)
    divided = c * (c < QuantumRange) + QuantumRange * np.ones(np.shape(c)) * (c >= QuantumRange)
    ret, threshold = cv2.threshold(divided.astype(np.uint16), QuantumRange - QuantumRange / 1, QuantumRange, cv2.THRESH_BINARY)
    inverted = QuantumRange - threshold
    kernel = np.ones((5, 5), np.uint8)
    noise_removed = cv2.morphologyEx(inverted, cv2.MORPH_OPEN, kernel)
    mask = cv2.dilate(noise_removed, kernel, iterations=1)
    mask = QuantumRange - mask
    return mask

def process_color_image(file_input):
    negative_input = cv2.imread(file_input, cv2.IMREAD_UNCHANGED)
    if len(negative_input) == 2:  # Silverfast images
        ir_silverfast = True
        ir_none = False
        ir_vuescan = False
    elif len(negative_input) == 1:  # Vuescan or no IR
        ir_silverfast = False
        ir_vuescan = False
        ir_none = True
        if negative_input[0].shape[2] == 4:  # IR layer in alpha channel
            ir_vuescan = True
            ir_none = False
    if ir_silverfast:
        print(' Identified image as Silverfast scan')
        inverted_image, r_channel = negative_inversion(negative_input[0])
        dust_removed = dust_removal(negative_input[1], inverted_image, r_channel)
        combined = np.dstack((inverted_image, dust_removed))
    elif ir_vuescan:
        print(' Identified image as VueScan scan')
        r_channel, g_channel, b_channel, ir_channel = cv2.split(negative_input[0])
        negative_input = cv2.merge([r_channel.astype(np.uint16), g_channel.astype(np.uint16), b_channel.astype(np.uint16)])
        inverted_image, r_channel = negative_inversion(negative_input)
        dust_removed = dust_removal(ir_channel, inverted_image, r_channel)
        combined = np.dstack((inverted_image, dust_removed))
    elif ir_none:
        print(' Identified image as non-infrared scan')
        inverted_image, r_channel = negative_inversion(negative_input[0])
        combined = inverted_image
    return combined

def process_bw_image(file_input):
    layered, negative_input = cv2.imreadmulti(file_input, [], cv2.IMREAD_UNCHANGED)
    print(' Identified image as black and white scan')
    inverted_image, r_channel = negative_inversion(negative_input[0])
    combined = inverted_image
    return combined

# --- End embedded functions ---


# --- New helper to convert RAW to TIFF using ImageMagick ---

RAW_EXTENSIONS = {'.cr2', '.nef', '.arw', '.dng', '.rw2', '.orf', '.raf'}

def convert_raw_to_tiff_imagemagick(input_path, output_path):
    try:
        subprocess.run([
            "magick", input_path,
            "-depth", "16",
            output_path
        ], check=True)
        return True
    except Exception as e:
        print(f"Failed to convert {input_path} with ImageMagick: {e}")
        return False

import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, font
from ttkthemes import ThemedTk
from PIL import Image, ImageTk
import numpy as np
import cv2

RAW_EXTENSIONS = ['.cr2', '.nef', '.arw', '.dng', '.rw2', '.orf', '.raf']

def sorted_nicely(l):
    # Your sorting function here
    import re
    convert = lambda text: int(text) if text.isdigit() else text.lower()
    alphanum_key = lambda key: [convert(c) for c in re.split('([0-9]+)', key)]
    return sorted(l, key=alphanum_key)

def process_color_image(path):
    # Dummy stub for your real function
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    return img

def process_bw_image(path):
    # Dummy stub for your real function
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if img.ndim == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return img

def preprocess_raw_files(input_dir, temp_dir):
    # Dummy stub
    return []
import os
import re
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, font, filedialog, messagebox

from ttkthemes import ThemedTk
from PIL import Image, ImageTk

import numpy as np
import cv2
import scipy.ndimage
from skimage import exposure, color, img_as_float

# --- Constants ---
RAW_EXTENSIONS = {".cr2", ".nef", ".arw", ".dng", ".rw2", ".orf", ".raf"}

cutoff_margin = 10
blur_size = (7, 7)
QuantumRange = 65535
GammaGlobal = 2.15
EPSILON = 1e-10  # Small value to prevent division by zero / log(0)

# --- Utility Functions ---

def sorted_nicely(l):
    convert = lambda text: int(text) if text.isdigit() else text.lower()
    alphanum_key = lambda key: [convert(c) for c in re.split('([0-9]+)', key)]
    return sorted(l, key=alphanum_key)

def normalize(image, low=2, high=99):
    float_img = img_as_float(image)
    p_low, p_high = np.percentile(float_img, (low, high))
    if p_low == p_high:
        p_low = max(0, p_low - EPSILON)
        p_high = p_high + EPSILON
    rescale = exposure.rescale_intensity(float_img, in_range=(p_low, p_high))
    max_val = rescale.max()
    if max_val < EPSILON:
        max_val = 1.0
    normalized = rescale * QuantumRange / max_val
    return normalized

def adjust_gamma(image, gamma):
    if gamma <= 0 or np.isnan(gamma) or np.isinf(gamma):
        gamma = 1.0
    inv_gamma = 1.0 / gamma
    table = ((np.arange(0, QuantumRange + 1) / QuantumRange) ** inv_gamma) * QuantumRange
    table = np.clip(table, 0, QuantumRange).astype(np.uint16)
    image_clipped = np.clip(image.astype(np.int32), 0, QuantumRange).astype(np.uint16)
    return table[image_clipped]

def adjust_channel(channel, minimum, gamma):
    normalized = channel / QuantumRange
    with np.errstate(divide='ignore', invalid='ignore'):
        power_term = np.power(normalized, -1.0)
    power_term = np.where(normalized > 0, power_term, 1.0)
    exponent = np.clip(minimum * power_term, 0, QuantumRange)
    output = exponent * QuantumRange
    clipped = np.clip(output, 0, QuantumRange)
    adjusted_channel = adjust_gamma(clipped, gamma)
    return adjusted_channel

def recompile_image(r_channel, g_channel, b_channel, gamma_subtract):
    image = cv2.merge([r_channel.astype(np.uint16), g_channel.astype(np.uint16), b_channel.astype(np.uint16)])
    image = adjust_gamma(image.astype(np.uint16), GammaGlobal)
    image = image.astype(np.int32) - gamma_subtract
    image = np.clip(image, 0, QuantumRange).astype(np.uint16)
    recompiled_image = cv2.normalize(image, None, alpha=0, beta=QuantumRange, norm_type=cv2.NORM_MINMAX)
    return recompiled_image

def autolevel_image(image):
    print(' Correcting levels...')
    image_gray = color.rgb2gray(image.astype(np.uint16))
    blackpoint = np.amin(image_gray) * QuantumRange
    whitepoint = np.amax(image_gray) * QuantumRange
    autolevel_mean = 100 * np.mean(image) / QuantumRange
    autolevel_midrange = 0.5
    if autolevel_mean <= 0:
        autolevel_mean = EPSILON
    autolevel_gamma = np.log(autolevel_mean / 100) / np.log(autolevel_midrange + EPSILON)
    image_hsv = color.rgb2hsv(image.astype(np.uint16))
    h_channel, s_channel, v_channel = cv2.split(image_hsv)
    v_channel = v_channel * QuantumRange
    v_channel = np.clip(v_channel, blackpoint, whitepoint)
    v_channel = cv2.normalize(v_channel, None, alpha=0, beta=QuantumRange, norm_type=cv2.NORM_MINMAX)
    v_channel = v_channel / QuantumRange
    output_hsv = cv2.merge((h_channel, s_channel, v_channel))
    autoleveled_image = color.hsv2rgb(output_hsv) * QuantumRange
    autoleveled_image = adjust_gamma(autoleveled_image.astype(np.uint16), autolevel_gamma)
    return autoleveled_image

def autocolor_image(image):
    print(' Correcting colors...')
    image_gray = color.rgb2gray(image.astype(np.uint16))
    r_channel, g_channel, b_channel = cv2.split(image)
    neutral_gray = np.mean(image_gray)
    r_mean = np.mean(r_channel) / QuantumRange
    g_mean = np.mean(g_channel) / QuantumRange
    b_mean = np.mean(b_channel) / QuantumRange
    r_mean = r_mean if r_mean > EPSILON else EPSILON
    g_mean = g_mean if g_mean > EPSILON else EPSILON
    b_mean = b_mean if b_mean > EPSILON else EPSILON
    r_ratio = neutral_gray / r_mean
    g_ratio = neutral_gray / g_mean
    b_ratio = neutral_gray / b_mean
    r_colorcorrected = np.clip((r_channel * r_ratio), 0, QuantumRange)
    r_colorcorrected = normalize(r_colorcorrected, 0.5, 99.5)
    g_colorcorrected = np.clip((g_channel * g_ratio), 0, QuantumRange)
    g_colorcorrected = normalize(g_colorcorrected, 0.5, 99.5)
    b_colorcorrected = np.clip((b_channel * b_ratio), 0, QuantumRange)
    b_colorcorrected = normalize(b_colorcorrected, 0.5, 99.5)
    autocolored_image = cv2.merge([r_colorcorrected.astype(np.uint16), g_colorcorrected.astype(np.uint16), b_colorcorrected.astype(np.uint16)])
    return autocolored_image

def negative_inversion(image_input):
    print(' Inverting negative...')
    r_input, g_input, b_input = cv2.split(image_input)
    image_crop = image_input[cutoff_margin:-cutoff_margin, cutoff_margin:-cutoff_margin]
    image_blur = cv2.blur(image_crop, blur_size)
    r_blur, g_blur, b_blur = cv2.split(image_blur)

    r_min = max(np.amin(r_blur) / QuantumRange, EPSILON)
    r_max = max(np.amax(r_blur) / QuantumRange, EPSILON)
    g_min = max(np.amin(g_blur) / QuantumRange, EPSILON)
    g_max = max(np.amax(g_blur) / QuantumRange, EPSILON)
    b_min = max(np.amin(b_blur) / QuantumRange, EPSILON)
    b_max = max(np.amax(b_blur) / QuantumRange, EPSILON)

    gamma_subtract = ((b_min / b_max) ** (1 / GammaGlobal)) * QuantumRange * 0.95

    try:
        gamma_for_r = np.log(r_max / r_min) / np.log(b_max / b_min)
    except Exception:
        gamma_for_r = 1.0
    try:
        gamma_for_g = np.log(g_max / g_min) / np.log(b_max / b_min)
    except Exception:
        gamma_for_g = 1.0
    gamma_for_b = 1.0

    r_adjusted = adjust_channel(r_input, r_min, gamma_for_r)
    g_adjusted = adjust_channel(g_input, g_min, gamma_for_g)
    b_adjusted = adjust_channel(b_input, b_min, gamma_for_b)
    inverted_negative = recompile_image(r_adjusted, g_adjusted, b_adjusted, gamma_subtract)
    autoleveled_negative = autolevel_image(inverted_negative)
    autocolored_negative = autocolor_image(autoleveled_negative)
    return autocolored_negative, r_input

def remove_noise(image, aggressiveness=7):
    normalized = image / QuantumRange
    inverted = 1 - normalized
    tmp = scipy.ndimage.convolve(inverted, np.ones((3, 3)), mode='constant')
    out = np.logical_and(tmp >= aggressiveness, inverted).astype(np.float32)
    reverted = 1 - out
    unnormalized = reverted * QuantumRange
    return unnormalized

def dust_removal(infrared_image, inverted_image, r_channel):
    print(' Removing dust...')
    normalized_infrared = normalize(infrared_image, 2, 99)
    normalized_red = normalize(r_channel, 2, 99)
    c = normalized_infrared / ((normalized_red.astype('float') + 1) / (QuantumRange + 1) + EPSILON)
    divided = c * (c < QuantumRange) + QuantumRange * np.ones(np.shape(c)) * (c >= QuantumRange)
    ret, threshold = cv2.threshold(divided.astype(np.uint16), QuantumRange - QuantumRange / 1, QuantumRange, cv2.THRESH_BINARY)
    inverted = QuantumRange - threshold
    kernel = np.ones((5, 5), np.uint8)
    noise_removed = cv2.morphologyEx(inverted, cv2.MORPH_OPEN, kernel)
    mask = cv2.dilate(noise_removed, kernel, iterations=1)
    mask = QuantumRange - mask
    return mask

def process_color_image(file_input):
    layered, negative_input = cv2.imreadmulti(file_input, [], cv2.IMREAD_UNCHANGED)

    if len(negative_input) == 2:  # Silverfast images
        ir_silverfast = True
        ir_none = False
        ir_vuescan = False

    elif len(negative_input) == 1:  # Vuescan or no IR
        ir_silverfast = False
        ir_vuescan = False
        ir_none = True

        if negative_input[0].shape[2] == 4:  # IR layer in alpha channel
            ir_vuescan = True
            ir_none = False

    if ir_silverfast:
        print(' Identified image as Silverfast scan')
        inverted_image, r_channel = negative_inversion(negative_input[0])
        dust_removed = dust_removal(negative_input[1], inverted_image, r_channel)
        combined = np.dstack((inverted_image, dust_removed))
    elif ir_vuescan:
        print(' Identified image as VueScan scan')
        r_channel, g_channel, b_channel, ir_channel = cv2.split(negative_input[0])
        negative_input = cv2.merge([r_channel.astype(np.uint16), g_channel.astype(np.uint16), b_channel.astype(np.uint16)])
        inverted_image, r_channel = negative_inversion(negative_input)
        dust_removed = dust_removal(ir_channel, inverted_image, r_channel)
        combined = np.dstack((inverted_image, dust_removed))
    elif ir_none:
        print(' Identified image as non-infrared scan')
        inverted_image, r_channel = negative_inversion(negative_input[0])
        combined = inverted_image
    else:
        combined = negative_input[0]

    return combined

# --- RAW to TIFF Conversion ---

def raw_to_tiff(raw_file, output_dir):
    filename = os.path.splitext(os.path.basename(raw_file))[0]
    tiff_path = os.path.join(output_dir, filename + ".tiff")
    try:
        # Using ImageMagick 'magick' command (ensure ImageMagick 7+ installed)
        result = subprocess.run(["magick", raw_file, "-depth", "16", tiff_path], capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ImageMagick conversion failed: {result.stderr}")
        return tiff_path
    except Exception as e:
        print(f"Failed to convert RAW to TIFF: {e}")
        return None

































class CropWindow(tk.Toplevel):
    def __init__(self, master, file_path):
        super().__init__(master.master)  # Pass the actual Tk root window here
        self.app = master  # Store reference to main app instance
        self.file_path = file_path
        self.title("m a s k i n g")
        self.original_img = Image.open(file_path)
        self.rect = None
        self.start_x = None
        self.start_y = None

        # Offsets for centering image inside canvas
        self.img_x_offset = 0
        self.img_y_offset = 0

        # Set default window size to 70% of screen width and height
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        default_width = int(screen_width * 0.7)
        default_height = int(screen_height * 0.7)
        self.geometry(f"{default_width}x{default_height}")

        # Create main frame to center contents vertically and horizontally
        main_frame = ttk.Frame(self)
        main_frame.pack(fill=tk.BOTH, expand=True)
        main_frame.columnconfigure(0, weight=1)  # Center contents horizontally
        main_frame.rowconfigure(0, weight=1)

        # Canvas inside main frame
        self.canvas = tk.Canvas(main_frame, cursor="cross", bg="white")
        self.canvas.grid(row=0, column=0, sticky="n")
        main_frame.columnconfigure(0, weight=1)  # This you already have

        # Buttons frame inside main frame, centered horizontally
        btn_frame = ttk.Frame(main_frame)
        btn_frame.grid(row=1, column=0, pady=10)

        apply_btn = ttk.Button(btn_frame, text="a p p l y   m a s k", command=self.apply_crop)
        apply_btn.pack(side=tk.LEFT, padx=5)
        cancel_btn = ttk.Button(btn_frame, text="a b o r t", command=self.destroy)
        cancel_btn.pack(side=tk.LEFT, padx=5)

        # Bind mouse events on canvas
        self.canvas.bind("<ButtonPress-1>", self.on_button_press)
        self.canvas.bind("<B1-Motion>", self.on_move_press)
        self.canvas.bind("<ButtonRelease-1>", self.on_button_release)

        # Bind window resize event with debouncing
        self._resize_job = None
        self.bind("<Configure>", self.on_resize)

        self.display_img_id = None
        self.display_img = None
        self.scale = 1
        self.display_size = (0, 0)
        self.crop_coords_original = None

        # Initially display the image
        self.show_image()

    def show_image(self):
        # Calculate size relative to current window size with padding
        win_width = max(self.winfo_width(), 100)
        win_height = max(self.winfo_height(), 100)

        # Leave room for buttons (approx 50px)
        max_width = win_width - 40  # some padding left/right
        max_height = win_height - 80  # padding for buttons + top/bottom

        img_width, img_height = self.original_img.size
        scale = min(max_width / img_width, max_height / img_height, 1)
        self.scale = scale

        new_width = int(img_width * scale)
        new_height = int(img_height * scale)
        self.display_size = (new_width, new_height)

        resized_img = self.original_img.resize(self.display_size, Image.Resampling.LANCZOS)
        self.display_img = ImageTk.PhotoImage(resized_img)

        # Set canvas width to image width only, so no extra space on right
        self.canvas.config(width=new_width, height=new_height)
        self.canvas.delete("all")

        # Reset horizontal offset to 0 since image fills canvas exactly
        self.img_x_offset = 0
        self.img_y_offset = 0  # Align top

        self.display_img_id = self.canvas.create_image(0, 0, anchor="nw", image=self.display_img)

        # Draw crop rectangle if exists, scaled accordingly and shifted by offset
        if self.crop_coords_original:
            left, upper, right, lower = self.crop_coords_original
            scaled_coords = (
                left * self.scale + self.img_x_offset,
                upper * self.scale + self.img_y_offset,
                right * self.scale + self.img_x_offset,
                lower * self.scale + self.img_y_offset,
            )
            self.rect = self.canvas.create_rectangle(*scaled_coords, outline='red', width=2)
        else:
            self.rect = None

        # Now center the canvas widget itself inside main_frame by adjusting grid options:
        # (You might want to update your __init__ to add this)
        self.canvas.grid_configure(padx=(max_width - new_width) // 2)

    def on_resize(self, event):
        # Debounce resize to avoid lag on rapid resizing
        if self._resize_job:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(100, self.show_image)

    def on_button_press(self, event):
        if self.rect:
            self.canvas.delete(self.rect)
            self.rect = None
            self.crop_coords_original = None

        # Get mouse position relative to image by subtracting offsets
        self.start_x = self.canvas.canvasx(event.x) - self.img_x_offset
        self.start_y = self.canvas.canvasy(event.y) - self.img_y_offset

        # Clamp start coordinates within image display area
        self.start_x = max(0, min(self.start_x, self.display_size[0]))
        self.start_y = max(0, min(self.start_y, self.display_size[1]))

        # Create rectangle starting at the corrected coordinates plus offset for display
        self.rect = self.canvas.create_rectangle(
            self.start_x + self.img_x_offset,
            self.start_y + self.img_y_offset,
            self.start_x + self.img_x_offset,
            self.start_y + self.img_y_offset,
            outline='red', width=2
        )

    def on_move_press(self, event):
        cur_x = self.canvas.canvasx(event.x) - self.img_x_offset
        cur_y = self.canvas.canvasy(event.y) - self.img_y_offset

        # Clamp current coordinates within image display area
        cur_x = max(0, min(cur_x, self.display_size[0]))
        cur_y = max(0, min(cur_y, self.display_size[1]))

        # Update rectangle coordinates with offsets added back for display
        self.canvas.coords(
            self.rect,
            self.start_x + self.img_x_offset,
            self.start_y + self.img_y_offset,
            cur_x + self.img_x_offset,
            cur_y + self.img_y_offset
        )

    def on_button_release(self, event):
        if self.rect:
            x1, y1, x2, y2 = self.canvas.coords(self.rect)

            # Convert back to image-relative coords by subtracting offset
            x1 -= self.img_x_offset
            x2 -= self.img_x_offset
            y1 -= self.img_y_offset
            y2 -= self.img_y_offset

            x1, x2 = sorted((max(0, x1), min(self.display_size[0], x2)))
            y1, y2 = sorted((max(0, y1), min(self.display_size[1], y2)))

            self.crop_coords_original = (
                x1 / self.scale,
                y1 / self.scale,
                x2 / self.scale,
                y2 / self.scale,
            )

    def apply_crop(self):
        if not self.rect or not self.crop_coords_original:
            messagebox.showwarning("Warning", "No crop area selected!")
            return

        left, upper, right, lower = self.crop_coords_original

        img_width, img_height = self.original_img.size
        left = max(0, min(left, img_width))
        right = max(0, min(right, img_width))
        upper = max(0, min(upper, img_height))
        lower = max(0, min(lower, img_height))

        width = right - left
        height = lower - upper

        if width <= 0 or height <= 0:
            messagebox.showwarning("Warning", "Invalid crop dimensions!")
            return

        cropped_img = self.original_img.crop((int(left), int(upper), int(right), int(lower)))

        try:
            cropped_img.save(self.file_path)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save cropped image: {e}")
            return

        messagebox.showinfo("m a s k e d", f"m a s k   s u c c e s f u l l y   a p p l i e d", parent=self)
        # Update main app preview and clear cache if applicable
        self.app.processed_images.pop(self.file_path, None)
        if self.app.current_index is not None:
            if self.app.file_list[self.app.current_index] == self.file_path:
                self.app.show_preview(self.file_path)

        self.destroy()
































class NegativeConverterApp:
    def __init__(self, master):
        self.master = master
        self.temp_dir = tempfile.mkdtemp(prefix="neg_converter_")
        self.master.protocol("WM_DELETE_WINDOW", self.on_close)

        master.title("Halida")

        self.file_list = []
        self.processed_images = {}
        self.current_index = None
        self.show_processed = True  # using boolean, per your current code

        # View mode: "list" or "grid"
        self.view_mode = tk.StringVar(value="list")

        # Create a container frame for everything *above* the progress bar
        self.top_frame = ttk.Frame(master)
        self.top_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # --- Left frame inside top_frame to hold buttons and file list side by side ---
        self.left_frame = ttk.Frame(self.top_frame)
        self.left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=5)

        # Frame for buttons stacked vertically on the left inside left_frame
        self.left_buttons_frame = ttk.Frame(self.left_frame)
        self.left_buttons_frame.pack(side=tk.LEFT, fill=tk.Y)

        self.right_frame = ttk.Frame(self.top_frame)
        self.right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=5, pady=5)

        button_width = 20

        # Your existing labels and buttons ...
        self.files_label = ttk.Label(self.left_buttons_frame, text="I M P O R T", font=('Impact', 16, 'bold'))
        self.files_label.pack(side=tk.TOP, pady=(40, 5))

        self.add_button = ttk.Button(self.left_buttons_frame, text="n e g a t i v e s", command=self.add_files, width=button_width)
        self.add_button.pack(side=tk.TOP, pady=(2))  # Extra space below

        self.add_dir_button = ttk.Button(self.left_buttons_frame, text="f o l d e r", command=self.add_directory, width=button_width)
        self.add_dir_button.pack(side=tk.TOP, pady=(2, 30))

        self.individual_label = ttk.Label(self.left_buttons_frame, text="A C T I O N", font=('Impact', 16, 'bold'))
        self.individual_label.pack(side=tk.TOP, pady=(0, 5))

        self.edit_frame = ttk.Frame(self.left_buttons_frame)    #CROP!!!!!!!!!!!!!!!!!!!!!! ROTATE!!!!!!!!!!
        self.edit_frame.pack(side=tk.TOP, pady=(5, 10))  # space before/after

        self.crop_button = ttk.Button(self.edit_frame, text="m a s k", command=self.crop_image, width=10)
        self.crop_button.pack(side=tk.LEFT, padx=2)

        self.rotate_button = ttk.Button(self.edit_frame, text="f l i p", command=self.rotate_image, width=10)
        self.rotate_button.pack(side=tk.LEFT, padx=2)

        self.process_button = ttk.Button(self.left_buttons_frame, text="d e v e l o p", command=self.process_selected, width=button_width)
        self.process_button.pack(side=tk.TOP, pady=2)

        self.save_button = ttk.Button(self.left_buttons_frame, text="p  r  i  n  t", command=self.save_processed, width=button_width)
        self.save_button.pack(side=tk.TOP, pady=(2, 30))  # Extra space below

        self.batch_label = ttk.Label(self.left_buttons_frame, text="B A T C H", font=('Impact', 16, 'bold'))
        self.batch_label.pack(side=tk.TOP, pady=(0, 5))

        self.batch_process_button = ttk.Button(self.left_buttons_frame, text="d e v e l o p", command=self.batch_process_all, width=button_width)
        self.batch_process_button.pack(side=tk.TOP, pady=2)

        self.batch_save_button = ttk.Button(self.left_buttons_frame, text="p  r  i  n  t", command=self.batch_save_all, width=button_width)
        self.batch_save_button.pack(side=tk.TOP, pady=(2, 30)) #new category

        #self.copyyear = ttk.Label(self.left_buttons_frame, text="2022", font=('Garamond', 10, 'italic'))
        #self.copyyear.pack(side=tk.BOTTOM, pady=(0, 40))

        self.vince_label = ttk.Label(self.left_buttons_frame, text="Vincenzo Mitchell Barroso", font=('Garamond', 10, 'italic'))
        self.vince_label.pack(side=tk.BOTTOM, pady=(0, 5))

        self.copy_label = ttk.Label(self.left_buttons_frame, text="Copyright 2022", font=('Garamond', 10, 'bold'))
        self.copy_label.pack(side=tk.BOTTOM, pady=(0, 5))




        # --- ADD: Toggle view button (above list/grid) ---
        self.toggle_view_button = ttk.Button(self.left_frame, text="c o n t a c t   s h e e t", command=self.toggle_view_mode)
        self.toggle_view_button.pack(side=tk.TOP, pady=(0, 10))

        # Listbox to the right of the buttons inside left_frame
        self.listbox = tk.Listbox(self.left_frame, width=40)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 0))
        self.listbox.bind("<<ListboxSelect>>", self.on_file_select)

        # --- NEW: Frame for the grid view (thumbnails) ---
        self.grid_frame = ttk.Frame(self.left_frame)
        # Do NOT pack here — will be packed/unpacked by toggle_view_mode

        # --- Right side inside top_frame: Image preview and toggle checkbox below it ---
        # Configure grid: 3 rows (label, canvas, button), 1 column
        self.right_frame.rowconfigure(0, weight=0)   # filename label (fixed height)
        self.right_frame.rowconfigure(1, weight=1)   # image panel (expands)
        self.right_frame.rowconfigure(2, weight=0)   # toggle button (fixed height)
        self.right_frame.columnconfigure(0, weight=1)

        self.filename_label = ttk.Label(self.right_frame, text="", font=("Arial", 14, "bold"))
        self.filename_label.grid(row=0, column=0, sticky="ew", pady=(40, 5), padx=5)

        self.image_panel = tk.Canvas(self.right_frame, width=400, height=300)
        self.image_panel.grid(row=1, column=0, sticky="nsew", padx=5)
        self.image_panel.bind("<Configure>", self.update_preview_image)

        self.toggle_button = ttk.Button(self.right_frame, text="s h o w   o r i g i n a l", command=self.toggle_processed)
        self.toggle_button.grid(row=2, column=0, sticky="ew", pady=5, padx=5)

        # --- Progress bar at the very bottom, outside top_frame, spanning full width ---
        self.progress = ttk.Progressbar(master, orient=tk.HORIZONTAL, mode='determinate')
        self.progress.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=5)



    def crop_image(self):
        if self.current_index is None:
            messagebox.showwarning("No Selection", "Please select an image first.")
            return

        file_path = self.file_list[self.current_index]

        # Open a new window for cropping
        CropWindow(self, file_path)
    
    def rotate_image(self):
        if self.current_index is None:
            messagebox.showwarning("No Selection", "Please select an image first.")
            return

        file_path = self.file_list[self.current_index]
        try:
            # Example rotate 90 degrees clockwise
            subprocess.run([
                "magick", file_path,
                "-rotate", "90",
                file_path
            ], check=True)
            self.show_preview(file_path)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to rotate image: {e}")


    def toggle_processed(self):
        self.show_processed = not self.show_processed
        # Update button text based on state
        if self.show_processed:
            self.toggle_button.config(text="s h o w   o r i g i n a l")
        else:
            self.toggle_button.config(text="s h o w   d e v e l o p e d")
        # Call whatever needs to happen on toggle
        self.update_preview_toggle()

    def toggle_view_mode(self):
        if self.view_mode.get() == "list":
            self.view_mode.set("grid")
            self.listbox.pack_forget()
            self.populate_grid_view()
            self.grid_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10,0))
            self.toggle_view_button.config(text="l i s t")
        else:
            self.view_mode.set("list")
            self.grid_frame.pack_forget()
            self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 0))
            self.toggle_view_button.config(text="c o n t a c t   s h e e t")

    def populate_grid_view(self):
        # Clear previous thumbnails
        for widget in self.grid_frame.winfo_children():
            widget.destroy()

        thumbnail_size = 250
        cols = 4
        padding = 5

        for idx, file_path in enumerate(self.file_list):
            try:
                img = Image.open(file_path)
                img.thumbnail((thumbnail_size, thumbnail_size))
                img_tk = ImageTk.PhotoImage(img)

                btn = ttk.Button(self.grid_frame, image=img_tk)
                btn.image = img_tk  # keep a reference to avoid GC
                btn.grid(row=idx // cols, column=idx % cols, padx=padding, pady=padding)

                # Bind to click to select and show preview
                btn.config(command=lambda i=idx: self.on_grid_thumbnail_click(i))

            except Exception as e:
                print(f"Failed to load thumbnail for {file_path}: {e}")

    def on_grid_thumbnail_click(self, index):
        self.current_index = index
        self.show_preview(self.file_list[index])

        # Sync listbox selection
        self.listbox.selection_clear(0, tk.END)
        self.listbox.selection_set(index)
        self.listbox.see(index)

    def prepare_image_for_editing(self, src_path):
        """Copy or convert image into temp_dir and return new path."""
        ext = os.path.splitext(src_path)[1].lower()
        base_name = os.path.basename(src_path)

        if ext in RAW_EXTENSIONS:
            # Convert RAW to 16-bit TIFF with ImageMagick
            dest_path = os.path.join(self.temp_dir, os.path.splitext(base_name)[0] + ".tif")
            try:
                subprocess.run(
                    ["magick", src_path, "-depth", "16", dest_path],
                    check=True
                )
            except subprocess.CalledProcessError as e:
                messagebox.showerror("Conversion Error", f"Failed to convert {base_name} to TIFF:\n{e}")
                return None
            return dest_path

        elif ext in {'.tif', '.tiff'}:
            # Just copy TIFF to temp_dir
            dest_path = os.path.join(self.temp_dir, base_name)
            shutil.copy2(src_path, dest_path)
            return dest_path

        else:
            # Unsupported extension
            return None


    def add_files(self):
        files = filedialog.askopenfilenames(
            title="c h o o s e   n e g a t i v e s",
            filetypes=[
                ("d i g i t a l   n e g a t i v e s", "*.tif *.tiff *.dng *.cr2 *.nef *.arw *.rw2 *.orf *.raf"),
                ("r a w", "*.dng *.cr2 *.nef *.arw *.rw2 *.orf *.raf"),
                ("t i f", "*.tif *.tiff"),
                ("a l l", "*")
            ]
        )
        if files:
            added = False
            for file in files:
                temp_path = self.prepare_image_for_editing(file)
                if temp_path and temp_path not in self.file_list:
                    self.file_list.append(temp_path)
                    added = True

            if added:
                self.file_list = sorted_nicely(self.file_list)
                self.listbox.delete(0, tk.END)
                for f in self.file_list:
                    self.listbox.insert(tk.END, os.path.basename(f))

    def add_directory(self):
            directory = filedialog.askdirectory(title="c h o o s e   f o l d e r")
            if not directory:
                return

            valid_exts = list(RAW_EXTENSIONS) + ['.tif', '.tiff']
            files_in_dir = [
                os.path.join(directory, f) for f in os.listdir(directory)
                if os.path.splitext(f)[1].lower() in valid_exts
            ]

            added = False
            for file in files_in_dir:
                temp_file = self.prepare_image_for_editing(file)
                if temp_file and temp_file not in self.file_list:
                    self.file_list.append(temp_file)
                    added = True

            if added:
                self.file_list = sorted_nicely(self.file_list)
                self.listbox.delete(0, tk.END)
                for f in self.file_list:
                    self.listbox.insert(tk.END, os.path.basename(f))


    def on_file_select(self, event):
        selection = event.widget.curselection()
        if selection:
            index = selection[0]
            self.current_index = index
            self.show_preview(self.file_list[index])

    def update_preview_toggle(self):
        # Refresh preview based on toggle state
        if self.current_index is not None:
            file_path = self.file_list[self.current_index]
            self.show_preview(file_path)


    def show_preview(self, file_path):
        try:
            self.filename_label.config(text=os.path.basename(file_path))

            # Load image based on toggle state
            if self.show_processed and file_path in self.processed_images:
                processed = self.processed_images[file_path]
                display_img = (processed / QuantumRange * 255).astype(np.uint8)
                pil_img = Image.fromarray(cv2.cvtColor(display_img, cv2.COLOR_BGR2RGB))
            else:
                ext = os.path.splitext(file_path)[1].lower()
                if ext in RAW_EXTENSIONS:
                    temp_dir = os.path.dirname(file_path)
                    tiff_file = raw_to_tiff(file_path, temp_dir)
                    if tiff_file is None:
                        messagebox.showerror("Error", "Failed to convert RAW to TIFF for preview.")
                        return
                    img = Image.open(tiff_file)
                else:
                    img = Image.open(file_path)

                arr = np.array(img)
                if arr.dtype in [np.uint16, np.int16]:
                    max_val = arr.max() or 1
                    arr_8bit = ((arr / max_val) * 255).astype(np.uint8)
                    if len(arr_8bit.shape) == 2:
                        pil_img = Image.fromarray(arr_8bit, mode='L')
                    elif len(arr_8bit.shape) == 3 and arr_8bit.shape[2] in [3,4]:
                        pil_img = Image.fromarray(arr_8bit, mode='RGB')
                    else:
                        pil_img = Image.fromarray(arr_8bit)
                else:
                    pil_img = img

            # Store the original PIL image for resizing
            self.preview_original_img = pil_img

            # Initial draw
            self.update_preview_image()

        except Exception as e:
            messagebox.showerror("Error", f"Failed to load image preview: {e}")


    def update_preview_image(self, event=None):
        if not hasattr(self, "preview_original_img"):
            return

        # Get canvas size
        canvas_width = self.image_panel.winfo_width()
        canvas_height = self.image_panel.winfo_height()

        if canvas_width < 2 or canvas_height < 2:
            return

        img_width, img_height = self.preview_original_img.size
        scale = min(canvas_width / img_width, canvas_height / img_height, 1)
        new_width = int(img_width * scale)
        new_height = int(img_height * scale)

        resized_img = self.preview_original_img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        self.preview_img_tk = ImageTk.PhotoImage(resized_img)

        # Clear and redraw centered
        self.image_panel.delete("all")
        self.image_panel.create_image(canvas_width // 2, canvas_height // 2, anchor="center", image=self.preview_img_tk)




    def process_selected(self):
        if self.current_index is None:
            messagebox.showwarning("Warning", "No file selected to process.")
            return
        file_path = self.file_list[self.current_index]
        threading.Thread(target=self._process_file_thread, args=(file_path,)).start()

    def _process_file_thread(self, file_path):
        self._set_buttons_state(False)
        try:
            self._process_file_internal(file_path)
        finally:
            self._set_buttons_state(True)

    def _process_file_internal(self, file_path):
        ext = os.path.splitext(file_path)[1].lower()
        if ext in RAW_EXTENSIONS:
            output_dir = os.path.dirname(file_path)
            tiff_file = raw_to_tiff(file_path, output_dir)
            if tiff_file is None:
                messagebox.showerror("Error", "Failed to convert RAW to TIFF for processing.")
                return
            file_path_to_process = tiff_file
        else:
            file_path_to_process = file_path

        processed = process_color_image(file_path_to_process)
        self.processed_images[file_path] = processed
        self.current_index = self.file_list.index(file_path)
        self.show_preview(file_path)

    def save_processed(self):
        if self.current_index is None:
            messagebox.showwarning("Warning", "No file selected to save.")
            return
        file_path = self.file_list[self.current_index]
        if file_path not in self.processed_images:
            messagebox.showwarning("Warning", "No processed image to save.")
            return
        self._save_processed_file(file_path)

    def _save_processed_file(self, file_path):
        save_path = filedialog.asksaveasfilename(title="Save Processed Image",
                                                 defaultextension=".tiff",
                                                 filetypes=[("TIFF files", "*.tiff")])
        if save_path:
            try:
                processed_img = self.processed_images[file_path]
                img_to_save = np.clip(processed_img, 0, QuantumRange).astype(np.uint16)

                if img_to_save.ndim == 3 and img_to_save.shape[2] == 3:
                    img_to_save = cv2.cvtColor(img_to_save, cv2.COLOR_BGR2RGB)

                tifffile.imwrite(save_path, img_to_save, photometric='rgb', compression='deflate')

            except Exception as e:
                messagebox.showerror("Error", f"Failed to save image: {e}")

    def batch_process_all(self):
        if not self.file_list:
            messagebox.showwarning("Warning", "No files to process.")
            return
        threading.Thread(target=self._batch_process_all_thread).start()

    def _batch_process_all_thread(self):
        self._set_buttons_state(False)
        self.progress.config(maximum=len(self.file_list))
        self.progress['value'] = 0
        try:
            for idx, file_path in enumerate(self.file_list, start=1):
                self._process_file_internal(file_path)
                self.progress['value'] = idx
            messagebox.showinfo("Info", "Batch processing completed.")
        except Exception as e:
            messagebox.showerror("Error", f"Batch processing failed: {e}")
        finally:
            self._set_buttons_state(True)
            self.progress['value'] = 0

    def batch_save_all(self):
        if not self.processed_images:
            messagebox.showwarning("Warning", "No processed images to save.")
            return
        folder = filedialog.askdirectory(title="Select Folder to Save All Processed Images")
        if not folder:
            return
        threading.Thread(target=self._batch_save_all_thread, args=(folder,)).start()

    def _batch_save_all_thread(self, folder):
        self._set_buttons_state(False)
        files_to_save = list(self.processed_images.keys())
        self.progress.config(maximum=len(files_to_save))
        self.progress['value'] = 0
        try:
            for idx, file_path in enumerate(files_to_save, start=1):
                processed_img = self.processed_images[file_path]
                img_to_save = np.clip(processed_img, 0, QuantumRange).astype(np.uint16)
                if img_to_save.ndim == 3 and img_to_save.shape[2] == 3:
                    img_to_save = cv2.cvtColor(img_to_save, cv2.COLOR_BGR2RGB)

                filename = os.path.splitext(os.path.basename(file_path))[0] + "_processed.tiff"
                save_path = os.path.join(folder, filename)
                tifffile.imwrite(save_path, img_to_save, photometric='rgb', compression='deflate')
                self.progress['value'] = idx
            messagebox.showinfo("Info", f"Saved {len(files_to_save)} processed images.")
        except Exception as e:
            messagebox.showerror("Error", f"Batch save failed: {e}")
        finally:
            self._set_buttons_state(True)
            self.progress['value'] = 0

    def on_close(self):
        """Cleanup temp files and close the app."""
        try:
            if hasattr(self, "temp_dir") and os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir)
        except Exception as e:
            print(f"Failed to remove temp dir: {e}")
        self.master.destroy()


    def _set_buttons_state(self, state: bool):
        # Helper to enable/disable all buttons during processing
        for btn in [self.add_button, self.process_button, self.save_button,
                    self.batch_process_button, self.batch_save_button]:
            btn.config(state=tk.NORMAL if state else tk.DISABLED)

def main():
    root = ThemedTk(theme="arc")
    app = NegativeConverterApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
