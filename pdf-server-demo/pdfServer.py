import os
import re
from flask import Flask, request, jsonify
from flask_cors import CORS
import pdfplumber

app = Flask(__name__)
CORS(app)

PDF_DIR = "./pdfs" 

product_lookup = {}

def normalize_text(text):
    text = text.lower()
    text = re.sub(r"[™®]", "", text)  # remove symbols
    text = re.sub(r"[^a-z0-9\s\-]", "", text)  # keep alphanumerics, space, dash
    text = re.sub(r"\s+", " ", text).strip()  # collapse spaces

    # Remove leading keywords like "product name", "product identifier", etc.
    text = re.sub(r"^(product name|product identifier)\s+", "", text)
    return text


def extract_product_names_from_pdf(pdf_path):
    product_names = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            first_page = pdf.pages[0]
            text = first_page.extract_text()
            if not text:
                return []

            pattern = re.compile(r'(?:name|product name|product identifier)[:\s]*([^\n\r]+)', re.IGNORECASE)
            matches = pattern.findall(text)
            for match in matches:
                cleaned = match.strip()
                if cleaned:
                    product_names.append(cleaned)

            product_names = list(set([pn.lower().strip() for pn in product_names if pn]))
            return product_names

    except Exception as e:
        print(f"[ERROR] Failed to extract from {pdf_path}: {e}")
        return []

def clean_product_name(raw_name):
    name = raw_name.lower().strip()

    # Add all known trailing noise to remove
    keywords = [
        "application", "recommended use", "product description", "revision date",
        "product code", "company", "prepared by", "authorization number",
        "product id numbers", "other means of identification", "date issued",
        "by vol", "rq", "osha", "twa", "stel", "percent", "ww identifiers"
    ]

    for kw in keywords:
        if kw in name:
            name = name.split(kw)[0].strip()
    return name


def build_product_lookup():
    lookup = {}
    for filename in os.listdir(PDF_DIR):
        if filename.lower().endswith(".pdf"):
            full_path = os.path.join(PDF_DIR, filename)
            product_names = extract_product_names_from_pdf(full_path)
            for raw_product in product_names:
                key = normalize_text(clean_product_name(raw_product))
                if key:
                    print(f"[DEBUG] Adding key: '{key}' => file: '{filename}'")
                    lookup[key] = filename
    return lookup


@app.route('/mcp', methods=['POST'])
def find_pdf():
    data = request.get_json()
    product = normalize_text(clean_product_name(data.get("product", "")))
    print(f"[DEBUG] Looking for '{product}'. Keys: {list(product_lookup.keys())}")
    filename = product_lookup.get(product)
    if filename:
        return jsonify({"filename": filename})
    else:
        return "Product not found.", 404

if __name__ == "__main__":
    product_lookup = build_product_lookup()
    print("[DEBUG] Final product_lookup keys:", list(product_lookup.keys()))
    app.run(debug=True)

