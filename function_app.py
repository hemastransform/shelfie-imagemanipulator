import azure.functions as func
import logging
import os
import cv2
import numpy as np
import easyocr # <-- Changed from pytesseract 1
from azure.storage.blob import BlobServiceClient

# --- App Initialization & Configuration ---
app = func.FunctionApp()

AZURE_STORAGE_CONNECTION_STRING = os.environ.get("AzureWebJobsStorage")
RAW_IMAGES_CONTAINER = "raw-images"
PROCESSED_IMAGES_CONTAINER = "processed-images"

# --- Filter & Keyword Parameters ---
DISCOUNT_KEYWORDS = ['off', '%', 'save', 'free', 'discount', 'offer', 'rs', '!', 'deals', 'keels']
FINAL_MIN_AREA_PERCENTAGE = 0.2
FINAL_MAX_AREA_PERCENTAGE = 1.0

# --- Initialize the OCR Reader ---
# This is done once when the function app starts up, making it faster for subsequent runs.
logging.info("Initializing EasyOCR Reader...")
reader = easyocr.Reader(['en'])
logging.info("EasyOCR Reader initialized.")


# --- Main Blob Trigger Function ---
@app.blob_trigger(arg_name="inputBlob",
                  path=f"{RAW_IMAGES_CONTAINER}/{{name}}",
                  connection="AzureWebJobsStorage")
def ManipulateImage(inputBlob: func.InputStream):
    logging.info(f"Image manipulation triggered for blob: {inputBlob.name}")

    try:
        image_bytes = inputBlob.read()
        image = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)

        if image is None:
            logging.error("Failed to decode image.")
            return

        img_height, img_width, _ = image.shape
        total_area = img_width * img_height

        # --- Find Candidate Bounding Boxes using EasyOCR ---
        candidate_boxes = []
        # The 'reader.readtext' function returns a list of (bounding_box, text, confidence)
        results = reader.readtext(image_bytes)
        
        for (bbox, text, prob) in results:
            text = text.lower().strip()
            for keyword in DISCOUNT_KEYWORDS:
                if keyword in text:
                    # Get the top-left and bottom-right points of the bounding box
                    (tl, tr, br, bl) = bbox
                    # Calculate width and height
                    w = int(br[0] - tl[0])
                    h = int(br[1] - tl[1])
                    # Get top-left coordinates
                    x, y = int(tl[0]), int(tl[1])
                    candidate_boxes.append((x, y, w, h))
                    break # Move to the next detected text

        logging.info(f"Found {len(candidate_boxes)} candidate tags based on keywords.")

        # --- Filter candidates by area ---
        final_selected_tags = []
        final_min_area = total_area * (FINAL_MIN_AREA_PERCENTAGE / 100.0)
        final_max_area = total_area * (FINAL_MAX_AREA_PERCENTAGE / 1.0)

        for x, y, w, h in set(candidate_boxes): # Use set to remove duplicate boxes
            area = w * h
            if final_min_area < area < final_max_area:
                final_selected_tags.append((x, y, w, h))

        logging.info(f"Found {len(final_selected_tags)} final tags after filtering.")

        if not final_selected_tags:
            logging.info("No tags met the final criteria. Exiting.")
            return

        # --- Connect to Blob Storage to save the crops ---
        blob_service_client = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
        original_filename = os.path.basename(inputBlob.name)
        name, ext = os.path.splitext(original_filename)

        # --- Loop through final tags, expand, crop, and save ---
        for i, (x, y, w, h) in enumerate(final_selected_tags):
            # Expand the crop upwards to hopefully include the product
            expanded_y_start = max(0, y - h) 
            expanded_crop = image[expanded_y_start:y+h, x:x+w]

            if expanded_crop.size > 0:
                _, img_encoded = cv2.imencode('.jpg', expanded_crop)
                crop_filename = f"{name}_crop_{i+1}{ext}"

                blob_client = blob_service_client.get_blob_client(container=PROCESSED_IMAGES_CONTAINER, blob=crop_filename)
                blob_client.upload_blob(img_encoded.tobytes(), overwrite=True)
                logging.info(f"Successfully uploaded {crop_filename} to {PROCESSED_IMAGES_CONTAINER}.")

    except Exception as e:
        logging.error(f"An error occurred: {e}", exc_info=True)


@app.blob_trigger(arg_name="myblob", path="shelfproductimages",
                               connection="shelfproductimages_STORAGE") 
def BlobTrigger(myblob: func.InputStream):
    logging.info(f"Python blob trigger function processed blob"
                f"Name: {myblob.name}"
                f"Blob Size: {myblob.length} bytes")


# This example uses SDK types to directly access the underlying BlobClient object provided by the Blob storage trigger.
# To use, uncomment the section below and add azurefunctions-extensions-bindings-blob to your requirements.txt file
# Ref: aka.ms/functions-sdk-blob-python
#
# import azurefunctions.extensions.bindings.blob as blob
# @app.blob_trigger(arg_name="client", path="shelfproductimages",
#                   connection="shelfproductimages_STORAGE")
# def BlobTrigger(client: blob.BlobClient):
#     logging.info(
#         f"Python blob trigger function processed blob \n"
#         f"Properties: {client.get_blob_properties()}\n"
#         f"Blob content head: {client.download_blob().read(size=1)}"
#     )
