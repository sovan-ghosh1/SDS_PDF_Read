import os 
import re 
import mysql.connector
from flask import Flask, request, jsonify 
from flask_cors import CORS
import pdfplumber 


app = Flask(__name__) 

CORS(app) 

PDF_DIR = "./pdfs" 


# MySQL DB config (update these values as needed) 
DB_CONFIG = { "host": "localhost",
 "user": "root", # ← change this
 "password": "user_123", # ← change this
 "database": "sds" # ← change this
   } 

def normalize_text(text): 
    text = text.lower() 
    text = re.sub(r"[™®]", "", text) 
    text = re.sub(r"[^a-z0-9\s\-]", "", text) 
    text = re.sub(r"\s+", " ", text).strip() 
    text = re.sub(r"^(product name|product identifier)\s+", "", text) 
    return text 
 
def extract_product_names_from_pdf(pdf_path):

    try:
        with pdfplumber.open(pdf_path) as pdf:
            text = pdf.pages[0].extract_text()
            if not text:
                return []

            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            section_lines = []
            in_section1 = False

            # Extract lines in Section 1: Identification
            for ln in lines:
                if not in_section1:
                    if re.search(r'identification', ln, re.IGNORECASE):
                        in_section1 = True
                        continue
                else:
                    if re.match(r'(section\s*[2-9]|2\.\s*hazard|composition|ingredients|first aid)', ln, re.IGNORECASE):
                        break
                    section_lines.append(ln)

            if not section_lines:
                return []

            # First priority: check for 'Name:' lines and fetch value, skip 'Date Issued' or 'Generic Name'
            for ln in section_lines:
                if re.match(r'^Name\s*:\s*(.+)', ln, re.IGNORECASE):
                    # Extract the value after "Name:"
                    match = re.match(r'^Name\s*:\s*(.+)', ln, re.IGNORECASE)
                    candidate = match.group(1).strip()
                    if candidate and candidate.lower() not in ['date issued', 'generic name']:
                        return [candidate]

            # If no 'Name:' line found, continue with existing logic
            label_keywords = [
                "product name",
                "product identifier",
                "product",
                "name",
                "1.1 product identifier product name"
            ]

            skip_labels = [
                "date issued",
                "generic name",
                "other means of identification",
                "product code",
                "registration number",
                "synonyms"
            ]

            product_name = None

            # Iterate lines, checking for labels and values
            for i, ln in enumerate(section_lines):
                ln_lower = ln.lower()

                # Skip lines that are date issued or other unwanted labels
                if any(skip_label in ln_lower for skip_label in skip_labels):
                    continue

                # Check for label + inline value (e.g. Name: Baby Pink Lavish)
                for label in label_keywords:
                    if ln_lower.startswith(label):
                        parts = re.split(rf'^{re.escape(label)}[:\s-]*', ln, flags=re.IGNORECASE)
                        if len(parts) > 1 and parts[1].strip():
                            candidate = parts[1].strip()
                            # Avoid 'identifier' or blank values
                            if candidate.lower() != 'identifier' and len(candidate.split()) <= 15:
                                product_name = candidate
                                break
                        else:
                            # No inline value, check next line for value if exists
                            if i + 1 < len(section_lines):
                                next_line = section_lines[i + 1].strip()
                                next_line_lower = next_line.lower()
                                # Skip if next line is another label or unwanted
                                if next_line and not any(skip_label in next_line_lower for skip_label in skip_labels) and \
                                   not any(k in next_line_lower for k in label_keywords) and \
                                   next_line_lower != 'identifier' and len(next_line.split()) <= 15:
                                    product_name = next_line
                                    break
                if product_name:
                    break

            if product_name:
                return [product_name]
            else:
                return []

    except Exception as e:
        print(f"[ERROR] Failed to extract from {pdf_path}: {e}")
        return []


def clean_product_name(raw_name):
    name = raw_name.lower().strip()
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

def init_db():
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INT AUTO_INCREMENT PRIMARY KEY,
            productname VARCHAR(255) NOT NULL UNIQUE,
            filename VARCHAR(255) NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    cursor.close()
    conn.close()

def insert_product(productname, filename):
    try:
        normalized = normalize_text(clean_product_name(productname))
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        cursor.execute("INSERT IGNORE INTO products (productname, filename) VALUES (%s, %s)", (normalized, filename))
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"[ERROR] Inserting product failed: {e}")

def build_product_lookup():
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT filename FROM products")
        existing_files = set(row[0] for row in cursor.fetchall())
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"[ERROR] Couldn't load existing filenames: {e}")
        existing_files = set()

    for filename in os.listdir(PDF_DIR):
        if filename.lower().endswith(".pdf") and filename not in existing_files:
            full_path = os.path.join(PDF_DIR, filename)
            product_names = extract_product_names_from_pdf(full_path)

            if not product_names:
                print(f"[INFO] No valid product name found in '{filename}'")
                continue

            for product in product_names:
                print(f"[DEBUG] Inserting product: '{product}' => file: '{filename}'")
                insert_product(product, filename)



@app.route('/mcp', methods=['POST'])
def find_pdf():
    data = request.get_json()
    product = normalize_text(clean_product_name(data.get("product", "")))
    print(f"[DEBUG] Looking for '{product}' in database.")
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        cursor.execute("SELECT filename FROM products WHERE productname LIKE %s", (f"%{product}%",))
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        if result:
            return jsonify({"filename": result[0]})
        else:
            return "Product not found.", 404
    except Exception as e:
        print(f"[ERROR] Database query failed: {e}")
        return "Internal server error.", 500

if __name__ == "__main__":
    init_db()
    build_product_lookup()
    app.run(debug=True)