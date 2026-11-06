import hashlib
import io
import re
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

st.title("🧾 Invoice PDF Tracker")
st.write(
    "Upload invoices, review and correct extracted fields, then use validated "
    "values for the dashboard and export."
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
    "Text is extracted directly when available. Scanned PDFs are sent through "
    "optional OCR; extracted values should always be checked against the PDF."
)


def extract_pdf_text(uploaded_file):
    """Extract embedded PDF text; try optional OCR if no selectable text exists."""
    if uploaded_file.size > MAX_FILE_SIZE_MB * 1024 * 1024:
        raise ValueError(f"File exceeds the {MAX_FILE_SIZE_MB} MB limit.")

    uploaded_file.seek(0)
    pdf_bytes = uploaded_file.getvalue()
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

    # OCR dependencies are optional in local environments, but are included for
    # the hosted app. A clear error is shown if the Tesseract executable is absent.
    try:
        from pdf2image import convert_from_bytes
        import pytesseract

        images = convert_from_bytes(
            pdf_bytes,
            first_page=1,
            last_page=min(len(reader.pages), MAX_PDF_PAGES),
            timeout=60,
        )
        ocr_pages = [pytesseract.image_to_string(image) for image in images]
        text = "\n".join(ocr_pages).strip()
        if text:
            return text, "OCR"
        raise ValueError("OCR found no readable text in this PDF.")
    except ImportError as error:
        raise ValueError(
            "No selectable text found. OCR support is not installed; install the "
            "optional OCR dependencies and Tesseract to process scanned PDFs."
        ) from error
    except Exception as error:
        if isinstance(error, ValueError) and str(error).startswith("OCR found"):
            raise
        raise ValueError(
            "No selectable text found and OCR could not run. Check that the PDF "
            "is readable and that Tesseract and Poppler are installed."
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
    ambiguous = numeric_date and int(raw_value.split(re.search(r"[-/. ]", raw_value).group())[0]) <= 12
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
    """Only accept amounts associated with a total/due label; never guess max."""
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


def extract_status(text):
    explicit = re.search(
        r"(?:payment\s*)?status\s*[:\-]\s*(paid|unpaid|overdue|pending|outstanding)\b",
        text,
        re.IGNORECASE,
    )
    if explicit:
        value = explicit.group(1).lower()
        return ("Paid" if value == "paid" else "Unpaid"), 0.95
    if re.search(r"\b(?:unpaid|overdue|pending|outstanding)\b", text, re.IGNORECASE):
        return "Unpaid", 0.65
    if re.search(r"\bpaid\s+in\s+full\b", text, re.IGNORECASE):
        return "Paid", 0.75
    return "Unknown", 0.0


def process_invoice(uploaded_file):
    raw_text, extraction_method = extract_pdf_text(uploaded_file)
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
        "File": uploaded_file.name,
        "Invoice Number": invoice_number,
        "Client": client,
        "Invoice Date": invoice_date,
        "Due Date": due_date,
        "Amount": amount,
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
    if pd.notna(row["Invoice Date"]) and pd.notna(row["Due Date"]) and row["Due Date"] < row["Invoice Date"]:
        issues.append("Due date is before invoice date")
    return "; ".join(issues) if issues else "OK"


if uploaded_files:
    records = []
    progress = st.progress(0)
    for index, uploaded_file in enumerate(uploaded_files):
        try:
            records.append(process_invoice(uploaded_file))
        except Exception as error:
            st.error(f"Could not process {uploaded_file.name}: {error}")
        finally:
            progress.progress((index + 1) / len(uploaded_files))
    progress.empty()
    if not records:
        st.error("No invoices could be processed.")
        st.stop()
    df = pd.DataFrame(records)
else:
    st.info("👆 Upload one or more PDF invoices from the sidebar to begin.")
    st.stop()

# Keep editor state tied to the uploaded file contents, so a new upload gets a fresh review.
upload_signature = hashlib.sha256(
    "".join(hashlib.sha256(file.getvalue()).hexdigest() for file in uploaded_files).encode()
).hexdigest()[:16]
editor_key = f"invoice_review_{upload_signature}"

st.subheader("📝 Review and correct extracted data")
st.caption(
    "Edit invoice number, client, dates, amount, and status directly in the table. "
    "Dashboard totals update using your reviewed values."
)
editor_input = df[[
    "File", "Invoice Number", "Client", "Invoice Date", "Due Date", "Amount", "Status"
]].copy()
reviewed = st.data_editor(
    editor_input,
    key=editor_key,
    width="stretch",
    hide_index=True,
    num_rows="fixed",
    column_config={
        "File": st.column_config.TextColumn(disabled=True),
        "Invoice Number": st.column_config.TextColumn(required=True),
        "Client": st.column_config.TextColumn(required=True),
        "Invoice Date": st.column_config.DateColumn(format="YYYY-MM-DD"),
        "Due Date": st.column_config.DateColumn(format="YYYY-MM-DD"),
        "Amount": st.column_config.NumberColumn(min_value=0.0, format="%.2f"),
        "Status": st.column_config.SelectboxColumn(options=VALID_STATUSES, required=True),
    },
)

# Add computed extraction metadata back to the reviewed values.
for column in ("Confidence", "Extraction", "Review Notes"):
    reviewed[column] = df[column].values
reviewed["Confidence Band"] = reviewed["Confidence"].map(confidence_band)
reviewed["Validation"] = reviewed.apply(validate_invoice, axis=1)
reviewed["Is Paid"] = reviewed["Status"].eq("Paid")
reviewed["Overdue"] = (
    reviewed["Status"].eq("Unpaid")
    & reviewed["Due Date"].notna()
    & (reviewed["Due Date"] < pd.Timestamp(date.today()))
)
reviewed["Outstanding"] = reviewed["Status"].eq("Unpaid")

needs_review = reviewed[reviewed["Validation"] != "OK"]
if not needs_review.empty:
    st.warning(f"{len(needs_review)} invoice(s) need review. Fix the issues in the editable table above.")

st.subheader("📊 Invoice Dashboard")
col1, col2, col3, col4 = st.columns(4)
outstanding_amount = reviewed.loc[reviewed["Outstanding"], "Amount"].sum(min_count=1)
overdue_amount = reviewed.loc[reviewed["Overdue"], "Amount"].sum(min_count=1)
paid_amount = reviewed.loc[reviewed["Is Paid"], "Amount"].sum(min_count=1)
overdue_count = int(reviewed["Overdue"].sum())
with col1:
    st.metric("📄 Invoices", len(reviewed))
with col2:
    st.metric("💰 Outstanding", "N/A" if pd.isna(outstanding_amount) else f"₹{outstanding_amount:,.2f}")
with col3:
    st.metric("🔴 Overdue", overdue_count)
with col4:
    st.metric("✅ Paid", "N/A" if pd.isna(paid_amount) else f"₹{paid_amount:,.2f}")

if overdue_count:
    formatted_overdue = "N/A" if pd.isna(overdue_amount) else f"₹{overdue_amount:,.2f}"
    st.error(f"⚠️ {overdue_count} invoice(s) are overdue. Total overdue amount: {formatted_overdue}")
else:
    st.success("🎉 No overdue invoices detected.")

st.divider()
st.subheader("📋 Invoice Register")
st.caption("Extraction is heuristic; verify the PDF before using the data for accounting or payment decisions.")
status_options = sorted(reviewed["Status"].unique())
col1, col2 = st.columns(2)
with col1:
    selected_status = st.multiselect("Payment Status", status_options, default=status_options)
with col2:
    overdue_only = st.checkbox("🔴 Show overdue invoices only")

filtered = reviewed[reviewed["Status"].isin(selected_status)].copy()
if overdue_only:
    filtered = filtered[filtered["Overdue"]]
register_columns = [
    "File", "Invoice Number", "Client", "Invoice Date", "Due Date", "Amount",
    "Status", "Overdue", "Confidence Band", "Confidence", "Extraction", "Validation", "Review Notes",
]
register = filtered[register_columns].copy()
st.dataframe(
    register,
    width="stretch",
    hide_index=True,
    column_config={
        "Invoice Date": st.column_config.DateColumn(format="YYYY-MM-DD"),
        "Due Date": st.column_config.DateColumn(format="YYYY-MM-DD"),
        "Amount": st.column_config.NumberColumn(format="₹%.2f"),
        "Confidence": st.column_config.ProgressColumn("Confidence", min_value=0, max_value=100, format="%d%%"),
    },
)

# Export corrected values and validation details; missing values become blank cells.
export_columns = [
    "File", "Invoice Number", "Client", "Invoice Date", "Due Date", "Amount", "Status",
    "Confidence Band", "Confidence", "Extraction", "Validation", "Review Notes",
]
export_df = filtered[export_columns].copy()
for column in ("Invoice Date", "Due Date"):
    export_df[column] = export_df[column].map(
        lambda value: value.strftime("%Y-%m-%d") if pd.notna(value) else ""
    )
export_df["Amount"] = export_df["Amount"].map(lambda value: "" if pd.isna(value) else value)
st.download_button(
    "⬇️ Download Reviewed Invoice Report",
    export_df.to_csv(index=False).encode("utf-8-sig"),
    "invoice_report.csv",
    "text/csv",
)

st.divider()
st.caption("🧾 Invoice PDF Tracker • Built with Python + Streamlit + Pandas + PyPDF")
