import hashlib
import io
import os
import re
import sqlite3
from datetime import date, datetime

import pandas as pd
import streamlit as st
from pypdf import PdfReader

st.set_page_config(page_title="Invoice PDF Tracker", page_icon="🧾", layout="wide")
st.markdown(
    """
    <style>
    .stApp { background: linear-gradient(180deg, #f8fbff 0%, #eef5ff 100%); color: #132238; }
    .main > div { padding-top: 2rem; }
    h1 { color: #0f172a; font-size: 2.5rem !important; font-weight: 800 !important; }
    h3 { color: #0f172a; font-weight: 700 !important; }
    [data-testid="stMetricLabel"] { color: #475569 !important; font-size: 0.9rem !important; }
    [data-testid="stMetricValue"] { color: #0f172a !important; font-weight: 800 !important; }
    .stDataFrame { border-radius: 14px; overflow: hidden; }
    .stAlert { border-radius: 14px; }
    .stSidebar { background: linear-gradient(180deg, #f7fbff 0%, #edf4ff 100%); }
    .stSidebar .block-container { padding-top: 1.25rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

MAX_FILE_SIZE_MB = 20
MAX_PDF_PAGES = 100
VALID_STATUSES = ["Paid", "Unpaid", "Unknown"]
CURRENCIES = ["INR", "USD", "EUR", "GBP", "CAD", "AUD", "JPY", "Other"]
DB_PATH = os.environ.get("INVOICE_DB_PATH", "invoice_tracker.db")
EDITABLE_FIELDS = [
    "Invoice Number", "Client", "Invoice Date", "Due Date", "Amount", "Currency", "Status"
]
DB_FIELD_NAMES = {
    "Invoice Number": "invoice_number",
    "Client": "client",
    "Invoice Date": "invoice_date",
    "Due Date": "due_date",
    "Amount": "amount",
    "Currency": "currency",
    "Status": "status",
}

st.title("🧾 Invoice PDF Tracker")
st.write(
    "Upload invoices, review and correct extracted fields, approve validated records, "
    "and export the results."
)
st.divider()
st.sidebar.header("📂 Upload Invoices")
uploaded_files = st.sidebar.file_uploader(
    "Upload PDF invoices",
    type=["pdf"],
    accept_multiple_files=True,
    help=f"PDFs up to {MAX_FILE_SIZE_MB} MB and {MAX_PDF_PAGES} pages are supported.",
)
st.sidebar.info(
    "Scanned PDFs use optional OCR. Reviewed metadata and an edit history are "
    "stored in a local SQLite database; PDF contents are not saved to the database."
)


def db_connect():
    parent = os.path.dirname(os.path.abspath(DB_PATH))
    os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS invoices (
            doc_id TEXT PRIMARY KEY,
            file_name TEXT NOT NULL,
            original_invoice_number TEXT,
            original_client TEXT,
            original_invoice_date TEXT,
            original_due_date TEXT,
            original_amount REAL,
            original_currency TEXT,
            original_status TEXT,
            invoice_number TEXT,
            client TEXT,
            invoice_date TEXT,
            due_date TEXT,
            amount REAL,
            currency TEXT,
            status TEXT,
            approved INTEGER NOT NULL DEFAULT 0,
            confidence INTEGER NOT NULL DEFAULT 0,
            extraction TEXT,
            review_notes TEXT,
            updated_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id TEXT NOT NULL,
            changed_at TEXT NOT NULL,
            action TEXT NOT NULL,
            field_name TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT,
            FOREIGN KEY(doc_id) REFERENCES invoices(doc_id) ON DELETE CASCADE
        )"""
    )
    conn.commit()
    return conn


def date_for_db(value):
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).date().isoformat()


def value_for_db(field, value):
    if field in ("Invoice Date", "Due Date"):
        return date_for_db(value)
    if field == "Amount":
        return None if value is None or pd.isna(value) else float(value)
    if value is None or pd.isna(value):
        return None
    return str(value).strip()


def read_saved_records(conn, doc_ids):
    if not doc_ids:
        return {}
    placeholders = ",".join("?" for _ in doc_ids)
    rows = conn.execute(
        f"SELECT * FROM invoices WHERE doc_id IN ({placeholders})", tuple(doc_ids)
    ).fetchall()
    return {row["doc_id"]: dict(row) for row in rows}


def log_change(conn, doc_id, action, field, old_value, new_value, changed_at):
    conn.execute(
        """INSERT INTO audit_log
           (doc_id, changed_at, action, field_name, old_value, new_value)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (doc_id, changed_at, action, field, old_value, new_value),
    )


def extract_pdf_text(pdf_bytes):
    """Extract embedded PDF text; try optional OCR if no selectable text exists."""
    if len(pdf_bytes) > MAX_FILE_SIZE_MB * 1024 * 1024:
        raise ValueError(f"File exceeds the {MAX_FILE_SIZE_MB} MB limit.")
    reader = PdfReader(io.BytesIO(pdf_bytes), strict=False)
    if reader.is_encrypted:
        raise ValueError("The PDF is encrypted and cannot be read.")
    if len(reader.pages) > MAX_PDF_PAGES:
        raise ValueError(f"PDF exceeds the {MAX_PDF_PAGES}-page limit.")

    page_text = []
    for page in reader.pages:
        try:
            page_text.append(page.extract_text() or "")
        except Exception:
            page_text.append("")
    text = "\n".join(page_text).strip()
    if text:
        return text, "Text extraction"

    try:
        from pdf2image import convert_from_bytes
        import pytesseract

        images = convert_from_bytes(
            pdf_bytes, first_page=1, last_page=min(len(reader.pages), MAX_PDF_PAGES), timeout=60
        )
        text = "\n".join(pytesseract.image_to_string(image) for image in images).strip()
        if text:
            return text, "OCR"
        raise ValueError("OCR found no readable text in this PDF.")
    except ImportError as error:
        raise ValueError(
            "No selectable text found. Install pdf2image and pytesseract, plus the "
            "Tesseract and Poppler system tools, to process scanned PDFs."
        ) from error
    except Exception as error:
        if isinstance(error, ValueError) and str(error).startswith("OCR found"):
            raise
        raise ValueError(
            "No selectable text found and OCR could not run. Check that Tesseract "
            "and Poppler are installed and that the PDF is readable."
        ) from error


def clean_text(text):
    return re.sub(r"[ \t]+", " ", text.replace("\r", "\n"))


def extract_invoice_number(text):
    patterns = (
        r"\b(?:invoice\s*(?:number|no\.?|#|id)|inv\s*#)\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9\-_ /]{0,39})",
        r"\binvoice\s*#\s*([A-Za-z0-9][A-Za-z0-9\-_ /]{0,39})",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip().rstrip(".,;"), 0.9
    return "Not found", 0.0


def extract_date(text, labels):
    date_pattern = (
        r"(\d{4}[-/. ]\d{1,2}[-/. ]\d{1,2}|"
        r"\d{1,2}[-/. ]\d{1,2}[-/. ]\d{2,4}|"
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*"
        r"\s+\d{1,2},?\s+\d{4})"
    )
    label_pattern = "|".join(re.escape(label) for label in labels)
    match = re.search(rf"(?:{label_pattern})\s*[:\-]?\s*{date_pattern}", text, re.IGNORECASE)
    if not match:
        return pd.NaT, 0.0, False
    raw_value = match.group(1).strip()
    formats = (
        "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d",
        "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y",
        "%m-%d-%Y", "%m/%d/%Y", "%m.%d.%Y",
        "%d-%m-%y", "%d/%m/%y", "%m/%d/%y",
        "%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%b %d %Y",
    )
    parsed = None
    for fmt in formats:
        try:
            parsed = datetime.strptime(raw_value, fmt)
            break
        except ValueError:
            continue
    if parsed is None:
        return pd.NaT, 0.0, False

    numeric_date = bool(re.fullmatch(r"\d{1,2}[-/. ]\d{1,2}[-/. ]\d{2,4}", raw_value))
    parts = re.split(r"[-/. ]", raw_value)
    ambiguous = numeric_date and int(parts[0]) <= 12 and int(parts[1]) <= 12
    return pd.Timestamp(parsed), (0.65 if ambiguous else 0.95), ambiguous


def extract_client(text):
    match = re.search(
        r"(?:bill\s*to|billed\s*to|client|customer|customer\s*name|client\s*name)\s*[:\-]?\s*([^\n]{1,100})",
        text,
        re.IGNORECASE,
    )
    if match:
        client = re.sub(r"\s+", " ", match.group(1)).strip(" :,-")
        client = re.split(
            r"\b(?:invoice\s*(?:number|date|no\.?|#)|due\s*date|date|amount)\b",
            client,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip(" :,-")
        if client:
            return client, 0.75
    return "Not found", 0.0


def extract_amount(text):
    amount_pattern = r"(?:₹|Rs\.?|INR|\$|€|£)?\s*([\d,]+(?:\.\d{1,2})?)"
    label_pattern = r"(?:total\s*(?:amount|due)?|grand\s*total|balance\s*due|amount\s*due|invoice\s*total)"
    for line in text.splitlines():
        match = re.search(rf"\b{label_pattern}\b\s*[:\-]?\s*{amount_pattern}", line, re.IGNORECASE)
        if match:
            try:
                amount = float(match.group(1).replace(",", ""))
            except ValueError:
                continue
            if amount >= 0:
                return amount, 0.9
    return None, 0.0


def extract_currency(text):
    checks = (
        (r"\bINR\b|₹|\bRs\.?\s*\d", "INR"),
        (r"\bUSD\b|\$", "USD"),
        (r"\bEUR\b|€", "EUR"),
        (r"\bGBP\b|£", "GBP"),
        (r"\bCAD\b", "CAD"),
        (r"\bAUD\b", "AUD"),
        (r"\bJPY\b|¥", "JPY"),
    )
    for pattern, currency in checks:
        if re.search(pattern, text, re.IGNORECASE):
            return currency
    return "INR"


def extract_status(text):
    explicit = re.search(
        r"(?:payment\s*)?status\s*[:\-]\s*(paid|unpaid|overdue|pending|outstanding)\b",
        text,
        re.IGNORECASE,
    )
    if explicit:
        return ("Paid" if explicit.group(1).lower() == "paid" else "Unpaid"), 0.95
    if re.search(r"\b(?:unpaid|overdue|pending|outstanding)\b", text, re.IGNORECASE):
        return "Unpaid", 0.65
    if re.search(r"\bpaid\s+in\s+full\b", text, re.IGNORECASE):
        return "Paid", 0.75
    return "Unknown", 0.0


def process_invoice(file_name, pdf_bytes):
    raw_text, extraction_method = extract_pdf_text(pdf_bytes)
    text = clean_text(raw_text)
    if not text:
        raise ValueError("No readable text was extracted from this PDF.")
    invoice_number, invoice_confidence = extract_invoice_number(text)
    client, client_confidence = extract_client(text)
    invoice_date, invoice_date_confidence, invoice_date_ambiguous = extract_date(
        text, ("invoice date", "date of invoice")
    )
    due_date, due_date_confidence, due_date_ambiguous = extract_date(
        text, ("due date", "payment due", "expiry date", "expires")
    )
    amount, amount_confidence = extract_amount(text)
    status, status_confidence = extract_status(text)
    confidence = round(
        sum((invoice_confidence, client_confidence, invoice_date_confidence,
             due_date_confidence, amount_confidence, status_confidence)) / 6 * 100
    )
    notes = []
    if amount is None:
        notes.append("Total amount not identified")
    if status == "Unknown":
        notes.append("Payment status not identified")
    if invoice_date_ambiguous or due_date_ambiguous:
        notes.append("Numeric date may be ambiguous")
    if extraction_method == "OCR":
        notes.append("OCR extraction; verify all fields")
    return {
        "File": file_name,
        "Invoice Number": invoice_number,
        "Client": client,
        "Invoice Date": invoice_date,
        "Due Date": due_date,
        "Amount": amount,
        "Currency": extract_currency(text),
        "Status": status,
        "Confidence": confidence,
        "Extraction": extraction_method,
        "Review Notes": "; ".join(notes),
    }


def confidence_band(value):
    if value >= 75:
        return "High"
    if value >= 45:
        return "Medium"
    return "Low"


def validate_invoice(row):
    issues = []
    if not str(row["Invoice Number"]).strip() or row["Invoice Number"] == "Not found":
        issues.append("Missing invoice number")
    if not str(row["Client"]).strip() or row["Client"] == "Not found":
        issues.append("Missing client")
    if pd.isna(row["Amount"]):
        issues.append("Missing amount")
    elif row["Amount"] <= 0:
        issues.append("Amount must be greater than zero")
    if row["Status"] == "Unknown":
        issues.append("Confirm payment status")
    if row["Currency"] not in CURRENCIES:
        issues.append("Select a supported currency")
    if pd.notna(row["Invoice Date"]) and pd.notna(row["Due Date"]) and row["Due Date"] < row["Invoice Date"]:
        issues.append("Due date is before invoice date")
    return "; ".join(issues) if issues else "OK"


def load_row_value(saved, field, extracted):
    if saved is None:
        return extracted
    value = saved.get(DB_FIELD_NAMES[field])
    if field in ("Invoice Date", "Due Date"):
        return pd.to_datetime(value) if value else pd.NaT
    return value


def save_reviews(conn, edited, saved_records):
    now = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    messages = []
    conn.execute("BEGIN")
    try:
        for _, row in edited.iterrows():
            doc_id = row["Document ID"]
            saved = saved_records.get(doc_id)
            current = {field: value_for_db(field, row[field]) for field in EDITABLE_FIELDS}
            validation = validate_invoice(row)
            requested_approval = bool(row["Approved"])
            old_approval = bool(saved["approved"]) if saved else False

            changed_fields = []
            if saved:
                for field in EDITABLE_FIELDS:
                    old_value = value_for_db(field, saved.get(DB_FIELD_NAMES[field]))
                    if old_value != current[field]:
                        changed_fields.append((field, old_value, current[field]))
            # Editing a previously approved invoice revokes approval until it is reviewed again.
            approval = requested_approval
            if old_approval and changed_fields:
                approval = False
                messages.append(f"{row['File']}: edits reset its approval; review and approve it again.")
            if approval and validation != "OK":
                approval = False
                messages.append(f"{row['File']}: approval was not saved because validation is not OK.")

            if saved is None:
                original_values = current.copy()
                values = [
                    doc_id, str(row["File"]), original_values["Invoice Number"],
                    original_values["Client"], original_values["Invoice Date"],
                    original_values["Due Date"], original_values["Amount"],
                    original_values["Currency"], original_values["Status"],
                    current["Invoice Number"], current["Client"], current["Invoice Date"],
                    current["Due Date"], current["Amount"], current["Currency"],
                    current["Status"], int(approval), int(row["Confidence"]),
                    str(row["Extraction"]), str(row["Review Notes"]), now,
                ]
                conn.execute(
                    """INSERT INTO invoices (
                        doc_id,file_name,original_invoice_number,original_client,
                        original_invoice_date,original_due_date,original_amount,original_currency,
                        original_status,invoice_number,client,invoice_date,due_date,amount,currency,
                        status,approved,confidence,extraction,review_notes,updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    values,
                )
                for field in EDITABLE_FIELDS:
                    log_change(conn, doc_id, "Initial extraction", field, None, current[field], now)
            else:
                if old_approval != approval:
                    log_change(conn, doc_id, "Approval", "Approved", str(old_approval), str(approval), now)
                for field, old_value, new_value in changed_fields:
                    log_change(conn, doc_id, "Review edit", field, old_value, new_value, now)
                conn.execute(
                    """UPDATE invoices SET file_name=?,invoice_number=?,client=?,invoice_date=?,
                       due_date=?,amount=?,currency=?,status=?,approved=?,confidence=?,extraction=?,
                       review_notes=?,updated_at=? WHERE doc_id=?""",
                    (
                        str(row["File"]), current["Invoice Number"], current["Client"],
                        current["Invoice Date"], current["Due Date"], current["Amount"],
                        current["Currency"], current["Status"], int(approval),
                        int(row["Confidence"]), str(row["Extraction"]),
                        str(row["Review Notes"]), now, doc_id,
                    ),
                )
            if saved is None and approval:
                log_change(conn, doc_id, "Approval", "Approved", "False", "True", now)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return messages


conn = db_connect()
if uploaded_files:
    records = []
    doc_ids = []
    progress = st.progress(0)
    for index, uploaded_file in enumerate(uploaded_files):
        pdf_bytes = uploaded_file.getvalue()
        doc_id = hashlib.sha256(pdf_bytes).hexdigest()
        try:
            record = process_invoice(uploaded_file.name, pdf_bytes)
            record["Document ID"] = doc_id
            records.append(record)
            doc_ids.append(doc_id)
        except Exception as error:
            st.error(f"Could not process {uploaded_file.name}: {error}")
        finally:
            progress.progress((index + 1) / len(uploaded_files))
    progress.empty()
    if not records:
        st.error("No invoices could be processed.")
        conn.close()
        st.stop()
    df = pd.DataFrame(records).drop_duplicates(subset=["Document ID"], keep="first")
    saved_records = read_saved_records(conn, df["Document ID"].tolist())
else:
    st.info("👆 Upload one or more PDF invoices from the sidebar to review them.")
    st.caption("Previously saved records and audit history remain in the local database; upload the same PDF to review it again.")
    conn.close()
    st.stop()

for index, row in df.iterrows():
    saved = saved_records.get(row["Document ID"])
    for field in EDITABLE_FIELDS:
        df.at[index, field] = load_row_value(saved, field, row[field])
    df.at[index, "Approved"] = bool(saved["approved"]) if saved else False
    if saved:
        df.at[index, "Confidence"] = saved["confidence"]
        df.at[index, "Extraction"] = saved["extraction"]
        df.at[index, "Review Notes"] = saved["review_notes"]

st.subheader("📝 Review, edit, and approve invoices")
st.caption(
    "Edit the extracted fields below. Approval is blocked until validation passes. "
    "Changing a previously approved invoice revokes its approval."
)
editor_columns = ["Document ID", "File"] + EDITABLE_FIELDS + ["Approved"]
editor_input = df[editor_columns].copy()
reviewed = st.data_editor(
    editor_input,
    key="invoice_review_" + hashlib.sha256("".join(df["Document ID"]).encode()).hexdigest()[:16],
    width="stretch",
    hide_index=True,
    num_rows="fixed",
    column_config={
        "Document ID": None,
        "File": st.column_config.TextColumn(disabled=True),
        "Invoice Number": st.column_config.TextColumn(required=True),
        "Client": st.column_config.TextColumn(required=True),
        "Invoice Date": st.column_config.DateColumn(format="YYYY-MM-DD"),
        "Due Date": st.column_config.DateColumn(format="YYYY-MM-DD"),
        "Amount": st.column_config.NumberColumn(min_value=0.0, format="%.2f"),
        "Currency": st.column_config.SelectboxColumn(options=CURRENCIES, required=True),
        "Status": st.column_config.SelectboxColumn(options=VALID_STATUSES, required=True),
        "Approved": st.column_config.CheckboxColumn(help="Only validated records can be approved."),
    },
)

# Preserve extraction metadata/original values for review and audit comparison.
for column in ("Confidence", "Extraction", "Review Notes"):
    reviewed[column] = df[column].values
reviewed["Confidence Band"] = reviewed["Confidence"].map(confidence_band)
reviewed["Validation"] = reviewed.apply(validate_invoice, axis=1)

if st.button("💾 Save reviewed invoices", type="primary"):
    try:
        messages = save_reviews(conn, reviewed, saved_records)
        st.success("Reviewed invoice data and audit history saved to the local database.")
        for message in messages:
            st.warning(message)
        # Refresh the saved-state snapshot for history and original-vs-reviewed views.
        saved_records = read_saved_records(conn, reviewed["Document ID"].tolist())
    except Exception as error:
        st.error(f"Could not save invoice reviews: {error}")

# Read current saved approval state for dashboard values, even before a fresh upload.
latest_saved = read_saved_records(conn, reviewed["Document ID"].tolist())
reviewed["Approved"] = reviewed["Document ID"].map(
    lambda doc_id: bool(latest_saved.get(doc_id, {}).get("approved", False))
)
reviewed["Is Paid"] = reviewed["Status"].eq("Paid")
reviewed["Overdue"] = (
    reviewed["Status"].eq("Unpaid")
    & reviewed["Due Date"].notna()
    & (reviewed["Due Date"] < pd.Timestamp(date.today()))
)
reviewed["Outstanding"] = reviewed["Status"].eq("Unpaid")

invalid_count = int((reviewed["Validation"] != "OK").sum())
if invalid_count:
    st.warning(f"{invalid_count} invoice(s) need review before approval.")

st.subheader("📊 Invoice Dashboard")
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("📄 Invoices", len(reviewed))
with col2:
    st.metric("✅ Approved", int(reviewed["Approved"].sum()))
with col3:
    st.metric("🔴 Overdue", int(reviewed["Overdue"].sum()))
with col4:
    st.metric("📝 Needs review", invalid_count)

st.markdown("**Financial totals by currency** (currencies are not combined):")
for currency, group in reviewed.groupby("Currency", dropna=False):
    outstanding = group.loc[group["Outstanding"], "Amount"].sum(min_count=1)
    overdue = group.loc[group["Overdue"], "Amount"].sum(min_count=1)
    paid = group.loc[group["Is Paid"], "Amount"].sum(min_count=1)
    currency_name = currency if pd.notna(currency) else "Unspecified"
    fmt = lambda value: "N/A" if pd.isna(value) else f"{value:,.2f} {currency_name}"
    st.write(f"- Outstanding: {fmt(outstanding)} · Overdue: {fmt(overdue)} · Paid: {fmt(paid)}")

st.divider()
st.subheader("📋 Invoice Register")
st.caption("Extraction is heuristic; verify the PDF before using the data for accounting or payment decisions.")
col1, col2, col3 = st.columns(3)
with col1:
    selected_status = st.multiselect("Payment Status", sorted(reviewed["Status"].unique()), default=sorted(reviewed["Status"].unique()))
with col2:
    selected_currencies = st.multiselect("Currency", sorted(reviewed["Currency"].dropna().unique()), default=sorted(reviewed["Currency"].dropna().unique()))
with col3:
    overdue_only = st.checkbox("🔴 Show overdue invoices only")
filtered = reviewed[
    reviewed["Status"].isin(selected_status) & reviewed["Currency"].isin(selected_currencies)
].copy()
if overdue_only:
    filtered = filtered[filtered["Overdue"]]
register_columns = [
    "File", "Invoice Number", "Client", "Invoice Date", "Due Date", "Amount", "Currency",
    "Status", "Approved", "Overdue", "Confidence Band", "Confidence", "Extraction", "Validation", "Review Notes",
]
st.dataframe(
    filtered[register_columns],
    width="stretch",
    hide_index=True,
    column_config={
        "Invoice Date": st.column_config.DateColumn(format="YYYY-MM-DD"),
        "Due Date": st.column_config.DateColumn(format="YYYY-MM-DD"),
        "Amount": st.column_config.NumberColumn(format="%.2f"),
        "Confidence": st.column_config.ProgressColumn("Confidence", min_value=0, max_value=100, format="%d%%"),
    },
)

with st.expander("🔎 Original extraction vs reviewed values"):
    comparison_rows = []
    for _, row in reviewed.iterrows():
        saved = latest_saved.get(row["Document ID"])
        original = {}
        for field in EDITABLE_FIELDS:
            original_db_field = "original_" + DB_FIELD_NAMES[field]
            original[field] = saved.get(original_db_field) if saved else value_for_db(field, row[field])
        comparison_rows.append({
            "File": row["File"],
            "Original invoice number": original["Invoice Number"],
            "Reviewed invoice number": row["Invoice Number"],
            "Original client": original["Client"],
            "Reviewed client": row["Client"],
            "Original amount": original["Amount"],
            "Reviewed amount": row["Amount"],
            "Original currency": original["Currency"],
            "Reviewed currency": row["Currency"],
            "Original status": original["Status"],
            "Reviewed status": row["Status"],
        })
    st.dataframe(pd.DataFrame(comparison_rows), width="stretch", hide_index=True)

export_columns = [
    "File", "Invoice Number", "Client", "Invoice Date", "Due Date", "Amount", "Currency",
    "Status", "Approved", "Confidence Band", "Confidence", "Extraction", "Validation", "Review Notes",
]
export_df = filtered[export_columns].copy()
for column in ("Invoice Date", "Due Date"):
    export_df[column] = export_df[column].map(lambda value: value.strftime("%Y-%m-%d") if pd.notna(value) else "")
st.download_button(
    "⬇️ Download Reviewed Invoice Report",
    export_df.to_csv(index=False).encode("utf-8-sig"),
    "invoice_report.csv",
    "text/csv",
)

with st.expander("📜 Audit history"):
    doc_ids = reviewed["Document ID"].tolist()
    placeholders = ",".join("?" for _ in doc_ids)
    audit_rows = conn.execute(
        f"SELECT changed_at, doc_id, action, field_name, old_value, new_value "
        f"FROM audit_log WHERE doc_id IN ({placeholders}) ORDER BY id DESC LIMIT 500",
        tuple(doc_ids),
    ).fetchall()
    if audit_rows:
        audit_df = pd.DataFrame([dict(row) for row in audit_rows])
        names = reviewed.set_index("Document ID")["File"].to_dict()
        audit_df["File"] = audit_df["doc_id"].map(names).fillna("Unknown file")
        st.dataframe(
            audit_df[["changed_at", "File", "action", "field_name", "old_value", "new_value"]],
            width="stretch",
            hide_index=True,
        )
    else:
        st.info("No saved changes yet. Save reviewed invoices to start the audit history.")

conn.close()
st.divider()
st.caption("🧾 Invoice PDF Tracker • Python + Streamlit + Pandas + PyPDF • Local SQLite metadata storage")
