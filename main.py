#!/usr/bin/env python3
"""
Ultralytics YOLO11 + Google AI Studio (Gemini Vision) OCR
"""

import argparse
import json
import os
from io import BytesIO

import gradio as gr
import numpy as np
import PIL.Image as Image
from dotenv import load_dotenv
from google import genai
from google.genai import types
from ultralytics import YOLO
from ultralytics.utils.plotting import Annotator, save_one_box

load_dotenv()

# Parse command line arguments
parser = argparse.ArgumentParser(description="YOLO Model Configuration")
parser.add_argument("--model", type=str, default="alpr-yolo11s-aug.pt", help="YOLO model path.")
args = parser.parse_args()

# Load YOLO model
model = YOLO(args.model)


def pil_to_jpeg_part(pil_image: Image.Image) -> types.Part:
    """Convert a PIL Image into a Gemini API JPEG Part."""
    buffer = BytesIO()
    pil_image.convert("RGB").save(buffer, format="JPEG")
    return types.Part.from_bytes(data=buffer.getvalue(), mime_type="image/jpeg")


def extract_bounding_boxes(img: Image.Image, results) -> list[types.Part]:
    """Extract cropped images from bounding boxes detected by YOLO."""
    cropped_parts: list[types.Part] = []
    boxes = results.boxes
    if boxes is not None:
        np_img = np.array(img)
        for box in boxes:
            cropped_img = save_one_box(box.xyxy, np_img, save=False)
            pil_img = Image.fromarray(cropped_img[..., ::-1])
            cropped_parts.append(pil_to_jpeg_part(pil_img))
    return cropped_parts


def predict_image(img: Image.Image, conf_threshold: float, iou_threshold: float):
    """Predicts objects in an image using YOLO with adjustable confidence and IoU thresholds."""
    return model.predict(
        source=img,
        conf=conf_threshold,
        iou=iou_threshold,
        show_labels=True,
        show_conf=True,
        imgsz=640,
    )


def parse_ocr_response(raw_text: str, expected_count: int) -> list[str]:
    """Parse JSON OCR output from Gemini, handling optional markdown fences."""
    cleaned = (raw_text or "").strip()
    if "```json" in cleaned:
        cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in cleaned:
        cleaned = cleaned.split("```", 1)[1].split("```", 1)[0].strip()

    try:
        parsed = json.loads(cleaned)
        items = parsed.get("content", [])
        if not isinstance(items, list):
            items = []
    except (json.JSONDecodeError, AttributeError):
        items = []

    # Pad or trim so every detected bounding box has a label
    normalized = [str(item) for item in items[:expected_count]]
    while len(normalized) < expected_count:
        normalized.append("")
    return normalized


def predict(img, conf_threshold, iou_threshold, api_key_input, model_choice, custom_model):
    """Detects license plates with YOLO and extracts text using Google Gemini Vision API."""
    if img is None:
        raise gr.Error("Silakan unggah gambar terlebih dahulu.")

    effective_api_key = (api_key_input or "").strip() or os.getenv("GEMINI_API_KEY", "").strip()
    if not effective_api_key:
        raise gr.Error(
            "GEMINI_API_KEY belum ditemukan. Isi di kolom API Key atau atur environment variable GEMINI_API_KEY."
        )

    gemini_model = (custom_model or "").strip() if model_choice == "Custom" else model_choice
    if not gemini_model:
        raise gr.Error("Nama model Gemini tidak boleh kosong.")

    # Getting predictions and results (bs=1)
    results = predict_image(img, conf_threshold, iou_threshold)[0]
    cropped_parts = extract_bounding_boxes(img, results)

    if not cropped_parts:
        return img

    prompt = (
        f"There are {len(cropped_parts)} cropped license plate images in order. "
        "For each image, extract all license plate characters as a single string without spaces. "
        'If no text is visible for an image, use an empty string "". '
        "Return ONLY valid JSON with this exact schema:\n"
        '{"content": ["PLATE1", "PLATE2"]}'
    )

    client = genai.Client(api_key=effective_api_key)
    response = client.models.generate_content(
        model=gemini_model,
        contents=[*cropped_parts, prompt],
        config=types.GenerateContentConfig(
            system_instruction="You are an expert OCR model for license plates.",
            response_mime_type="application/json",
            temperature=0.0,
        ),
    )

    labels = parse_ocr_response(response.text, len(results.boxes.xyxy))
    print("Text extracted by Gemini:", labels)

    annotator = Annotator(img.copy(), pil=True)
    for i, box in enumerate(results.boxes.xyxy):
        annotator.box_label(box, label=labels[i], color=(255, 0, 0))

    return annotator.im


def update_custom_visibility(choice):
    """Show or hide the custom model textbox based on dropdown selection."""
    return gr.update(visible=(choice == "Custom"))


with gr.Blocks(title="Ultralytics Gradio with Google Gemini Vision OCR") as demo:
    gr.Markdown(
        """
# Ultralytics Gradio with Google Gemini Vision OCR

Upload images for OCR inference and License Plate Recognition. YOLO detects the regions of
interest and Google Gemini Vision extracts the text without requiring a local Ollama server.
"""
    )

    with gr.Row():
        with gr.Column():
            img_input = gr.Image(type="pil", label="Upload Image")

            with gr.Row():
                conf_slider = gr.Slider(minimum=0, maximum=1, value=0.25, label="Confidence threshold")
                iou_slider = gr.Slider(minimum=0, maximum=1, value=0.45, label="IoU threshold")

            api_key_box = gr.Textbox(
                label="Google AI Studio API Key (Opsional jika sudah ada di .env)",
                placeholder="Kosongkan jika GEMINI_API_KEY sudah diset di environment...",
                type="password",
                lines=1,
            )

            model_dropdown = gr.Dropdown(
                choices=["gemini-2.5-flash", "gemini-2.5-pro", "Custom"],
                value="gemini-2.5-flash",
                label="Gemini Vision Model",
                info="Pilih model Gemini Vision atau pilih 'Custom' untuk memasukkan ID model lain.",
            )

            custom_model_box = gr.Textbox(
                label="Custom Model Name",
                placeholder="Contoh: gemini-2.5-flash-lite",
                visible=False,
                interactive=True,
            )

            submit_btn = gr.Button("Process Image", variant="primary")

        with gr.Column():
            output_img = gr.Image(type="pil", label="Result")

    model_dropdown.change(
        fn=update_custom_visibility,
        inputs=model_dropdown,
        outputs=custom_model_box,
    )

    submit_btn.click(
        fn=predict,
        inputs=[
            img_input,
            conf_slider,
            iou_slider,
            api_key_box,
            model_dropdown,
            custom_model_box,
        ],
        outputs=output_img,
    )

    gr.Examples(
        examples=[
            ["assets/roboflowimage1.jpg", 0.25, 0.45, "", "gemini-2.5-flash", ""],
            ["assets/roboflowimage2.jpg", 0.25, 0.45, "", "gemini-2.5-flash", ""],
        ],
        inputs=[
            img_input,
            conf_slider,
            iou_slider,
            api_key_box,
            model_dropdown,
            custom_model_box,
        ],
    )

if __name__ == "__main__":
    demo.launch()
