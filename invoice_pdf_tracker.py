import re
from datetime import date, datetime

import pandas as pd
import streamlit as st
from pypdf import PdfReader


st.set_page_config(
    page_title="Invoice PDF Tracker",
    page_icon="🧾",
    layout="wide",
)

st.markdown(
    """
    <style>
    .stApp {
        background: linear-gradient(180deg, #f8fbff 0%, #eef5ff 100%);
        color: #132238;
    }
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

st.title("🧾 Invoice PDF Tracker")
st.write(
    "Upload invoice PDFs to review extracted details, overdue invoices, "
    "and outstanding amounts. Check extracted values against the original PDF."
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
    "The app extracts invoice number, client, dates, amount, and an explicit "
    "payment status when it can identify one. Scanned PDFs require OCR and "
    "may not produce extractable text."
)


def extract_pdf_text(uploaded_file):
    """Extract embedded text, while rejecting oversized or unreadable PDFs."""
    if uploaded_file.size > MAX_FILE_SIZE_MB * 1024 * 1024:
        raise ValueError(f"File exceeds the {MAX_FILE_SIZE_MB} MB limit.")

    uploaded_file.seek(0)
    reader = PdfReader(uploaded_file, strict=False)
    if reader.is_encrypted:
        raise ValueError("The PDF is encrypted and cannot be read.")
    if len(reader.pages) > MAX_PDF_PAGES:
        raise ValueError(f"PDF exceeds the {MAX_PDF_PAGES}-page limit.")

    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    return "\n".join(pages).strip()


def clean_text(text):
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    return text


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
    match = re.search(
        rf"(?:{label_pattern})\s*[:\-]?\s*{date_pattern}",
        text,
        re.IGNORECASE,
    )
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

    ambiguous = bool(re.fullmatch(r"\d{1,2}[-/. ]\d{1,2}[-/. ]\d{2,4}", raw_value))
    confidence = 0.65 if ambiguous else 0.95
    return pd.Timestamp(parsed), confidence, ambiguous


def extract_client(text):
    patterns = (
        r"(?:bill\s*to|billed\s*to|client|customer|customer\s*name|client\s*name)\s*[:\-]?\s*([^\n]{1,100})",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            client = re.sub(r"\s+", " ", match.group(1)).strip(" :,-")
            # Avoid treating another common invoice field label as part of the name.
            client = re.split(
                r"\b(?:invoice\s*(?:number|date|no\.?|#)|due\s*date|date|amount)\b",
                client,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0].strip(" :,-")
            if client:
                return client, 0.75
    return "Not found", 0.0


def parse_amount(raw_value):
    normalized = raw_value.replace(",", "").strip()
    try:
        amount = float(normalized)
    except ValueError:
        return None
    if amount < 0:
        return None
    return amount


def extract_amount(text):
    """Only accept amounts associated with a total/due label; never guess max."""
    amount_pattern = r"(?:₹|Rs\.?|INR|\$|€|£)?\s*([\d,]+(?:\.\d{1,2})?)"
    label_pattern = (
        r"(?:total\s*(?:amount|due)?|grand\s*total|balance\s*due|"
        r"amount\s*due|invoice\s*total)"
    )
    for line in text.splitlines():
        match = re.search(rf"\b{label_pattern}\b\s*[:\-]?\s*{amount_pattern}", line, re.IGNORECASE)
        if match:
            amount = parse_amount(match.group(1))
            if amount is not None:
                return amount, 0.9
    return None, 0.0


def extract_status(text):
    """Prefer explicit status labels; use a clear standalone keyword second."""
    explicit = re.search(
        r"(?:payment\s*)?status\s*[:\-]\s*(paid|unpaid|overdue|pending|outstanding)\b",
        text,
        re.IGNORECASE,
    )
    if explicit:
        value = explicit.group(1).lower()
        return ("Paid" if value == "paid" else "Unpaid"), 0.95

    # Do not let an incidental mention of a past payment determine the status.
    if re.search(r"\b(?:unpaid|overdue|pending|outstanding)\b", text, re.IGNORECASE):
        return "Unpaid", 0.65
    if re.search(r"\bpaid\s+in\s+full\b", text, re.IGNORECASE):
        return "Paid", 0.75
    return "Unknown", 0.0


def process_invoice(uploaded_file):
    text = clean_text(extract_pdf_text(uploaded_file))
    if not text:
        raise ValueError("No selectable text found; this may be a scanned PDF that needs OCR.")

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

    confidence_parts = [
        invoice_confidence,
        client_confidence,
        invoice_date_confidence,
        due_date_confidence,
        amount_confidence,
        status_confidence,
    ]
    confidence = round(sum(confidence_parts) / len(confidence_parts) * 100)
    warnings = []
    if amount is None:
        warnings.append("Total amount not confidently identified")
    if status == "Unknown":
        warnings.append("Payment status not identified")
    if invoice_date_ambiguous or due_date_ambiguous:
        warnings.append("Numeric date may be ambiguous (day/month order)")

    return {
        "File": uploaded_file.name,
        "Invoice Number": invoice_number,
        "Client": client,
        "Invoice Date": invoice_date,
        "Due Date": due_date,
        "Amount": amount,
        "Status": status,
        "Confidence": confidence,
        "Review Notes": "; ".join(warnings) or "",
    }


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


today = pd.Timestamp(date.today())
df["Is Paid"] = df["Status"].eq("Paid")
df["Overdue"] = (
    df["Status"].eq("Unpaid")
    & df["Due Date"].notna()
    & (df["Due Date"] < today)
)
df["Outstanding"] = df["Status"].eq("Unpaid")

outstanding_amount = df.loc[df["Outstanding"], "Amount"].sum(min_count=1)
overdue_amount = df.loc[df["Overdue"], "Amount"].sum(min_count=1)
paid_amount = df.loc[df["Is Paid"], "Amount"].sum(min_count=1)
overdue_count = int(df["Overdue"].sum())
unknown_status_count = int(df["Status"].eq("Unknown").sum())

st.subheader("📊 Invoice Dashboard")
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("📄 Invoices", len(df))
with col2:
    st.metric("💰 Outstanding", "N/A" if pd.isna(outstanding_amount) else f"₹{outstanding_amount:,.2f}")
with col3:
    st.metric("🔴 Overdue", overdue_count)
with col4:
    st.metric("✅ Paid", "N/A" if pd.isna(paid_amount) else f"₹{paid_amount:,.2f}")

if overdue_count:
    st.error(
        f"⚠️ {overdue_count} invoice(s) are overdue. "
        f"Total overdue amount: {'N/A' if pd.isna(overdue_amount) else f'₹{overdue_amount:,.2f}'}"
    )
else:
    st.success("🎉 No overdue invoices detected.")
if unknown_status_count:
    st.warning(
        f"Payment status could not be confidently identified for {unknown_status_count} "
        "invoice(s); review those PDFs before relying on the totals."
    )

st.divider()
st.subheader("🔎 Invoice Filters")
col1, col2 = st.columns(2)
with col1:
    status_options = sorted(df["Status"].unique())
    selected_status = st.multiselect("Payment Status", status_options, default=status_options)
with col2:
    overdue_only = st.checkbox("🔴 Show overdue invoices only")

filtered_df = df[df["Status"].isin(selected_status)].copy()
if overdue_only:
    filtered_df = filtered_df[filtered_df["Overdue"]]

display_columns = [
    "File", "Invoice Number", "Client", "Invoice Date", "Due Date",
    "Amount", "Status", "Overdue", "Confidence", "Review Notes",
]
display_df = filtered_df[display_columns].copy()


def highlight_overdue(row):
    if row["Overdue"]:
        return ["background-color: #ffcccc; color: #b00020; font-weight: bold;"] * len(row)
    return [""] * len(row)


styled_df = display_df.style.apply(highlight_overdue, axis=1).format(
    {
        "Amount": lambda value: "N/A" if pd.isna(value) else f"₹{value:,.2f}",
        "Invoice Date": lambda value: value.strftime("%Y-%m-%d") if pd.notna(value) else "N/A",
        "Due Date": lambda value: value.strftime("%Y-%m-%d") if pd.notna(value) else "N/A",
        "Confidence": "{}%",
    }
)

st.subheader("📋 Invoice Register")
st.caption("Extraction is heuristic; verify the PDF before using the data for accounting or payment decisions.")
st.dataframe(styled_df, width="stretch", hide_index=True)

# Convert dates to strings and missing values to blanks for a clean CSV export.
download_df = display_df.copy()
for column in ("Invoice Date", "Due Date"):
    download_df[column] = download_df[column].map(
        lambda value: value.strftime("%Y-%m-%d") if pd.notna(value) else ""
    )
download_df["Amount"] = download_df["Amount"].map(
    lambda value: "" if pd.isna(value) else value
)
csv_output = download_df.to_csv(index=False).encode("utf-8-sig")

st.download_button(
    "⬇️ Download Invoice Report",
    csv_output,
    "invoice_report.csv",
    "text/csv",
)

st.divider()
st.caption("🧾 Invoice PDF Tracker • Built with Python + Streamlit + Pandas + PyPDF")
