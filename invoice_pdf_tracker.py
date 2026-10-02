import streamlit as st
import pandas as pd
import re
from datetime import date
from pypdf import PdfReader

st.markdown(
    """
    <style>
    .stApp {
        background: linear-gradient(180deg, #f8fbff 0%, #eef5ff 100%);
        color: #132238;
    }
    .main > div {
        padding-top: 2rem;
    }
    h1 {
        color: #0f172a;
        font-size: 2.5rem !important;
        font-weight: 800 !important;
        margin-bottom: 0.25rem;
    }
    h3 {
        color: #0f172a;
        font-weight: 700 !important;
    }
    [data-testid="stMetricLabel"] {
        color: #475569 !important;
        font-size: 0.9rem !important;
    }
    [data-testid="stMetricValue"] {
        color: #0f172a !important;
        font-weight: 800 !important;
    }
    div[data-testid="stHorizontalBlock"] > div {
        background: rgba(255,255,255,0.72);
        border: 1px solid rgba(148,163,184,0.22);
        border-radius: 18px;
        padding: 0.75rem 1rem;
        box-shadow: 0 8px 20px rgba(15, 23, 42, 0.05);
    }
    .stDataFrame {
        border-radius: 14px;
        overflow: hidden;
    }
    .stAlert {
        border-radius: 14px;
    }
    .stSidebar {
        background: linear-gradient(180deg, #f7fbff 0%, #edf4ff 100%);
    }
    .stSidebar .block-container {
        padding-top: 1.25rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.set_page_config(
    page_title="Invoice PDF Tracker",
    page_icon="🧾",
    layout="wide",
)

st.title("🧾 Invoice PDF Tracker")
st.write(
    "Upload your invoice PDFs to extract invoice details, "
    "identify overdue payments, and calculate outstanding amounts."
)
st.divider()

st.sidebar.header("📂 Upload Invoices")
uploaded_files = st.sidebar.file_uploader(
    "Upload PDF invoices",
    type=["pdf"],
    accept_multiple_files=True,
)

st.sidebar.info(
    """
    Upload one or multiple PDF invoices.

    The application extracts:
    • Invoice Number
    • Client
    • Invoice Date
    • Due Date
    • Amount
    • Payment Status
    """
)


def extract_pdf_text(uploaded_file):
    reader = PdfReader(uploaded_file)
    text = ""
    for page in reader.pages:
        page_text = page.extract_text()
        if page_text:
            text += page_text + "\n"
    return text


def clean_text(text):
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    return text


def extract_invoice_number(text):
    patterns = [
        r"(?:invoice\s*(?:number|no|#|id)?|inv\s*#?)\s*[:\-]?\s*([A-Za-z0-9\-_\/]+)",
        r"#\s*([A-Za-z0-9\-_\/]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return "Not found"


def extract_date(text, target_label):
    date_regex = r"(\d{4}[-/. ]\d{1,2}[-/. ]\d{1,2}|\d{1,2}[-/. ]\d{1,2}[-/. ]\d{2,4}|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4}\b)"
    pattern = rf"{re.escape(target_label)}\s*[:\-]?\s*{date_regex}"
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def extract_client(text):
    patterns = [
        r"(?:bill\s*to|billed\s*to|client|customer)\s*[:\-]?\s*([^\n]+)",
        r"(?:customer\s*name|client\s*name)\s*[:\-]?\s*([^\n]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            client = match.group(1).strip()
            return re.sub(r"\s+", " ", client)
    return "Not found"


def extract_amount(text):
    priority_patterns = [
        r"(?:total\s*due|balance\s*due|grand\s*total|amount\s*due|total)\s*[:\-]?\s*(?:₹|Rs\.?|INR|\$|€|£)?\s*([\d,]+(?:\.\d{1,2})?)",
    ]
    for pattern in priority_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                return float(match.group(1).replace(",", ""))
            except ValueError:
                pass

    all_amounts = re.findall(r"(?:₹|Rs\.?|INR|\$|€|£)\s*([\d,]+(?:\.\d{2})?)", text)
    if all_amounts:
        parsed = []
        for amt in all_amounts:
            try:
                parsed.append(float(amt.replace(",", "")))
            except ValueError:
                pass
        if parsed:
            return max(parsed)

    return 0.0


def extract_status(text):
    text_lower = text.lower()
    if re.search(r"\bpaid\b", text_lower):
        return "Paid"
    elif re.search(r"\b(unpaid|overdue|pending|outstanding)\b", text_lower):
        return "Unpaid"
    return "Unpaid"


def normalize_date(value):
    if not value:
        return pd.NaT
    return pd.to_datetime(value, errors="coerce")


def process_invoice(uploaded_file):
    text = extract_pdf_text(uploaded_file)
    text = clean_text(text)

    invoice_number = extract_invoice_number(text)
    client = extract_client(text)
    invoice_date = extract_date(text, "invoice date")
    due_date = (
        extract_date(text, "due date")
        or extract_date(text, "expiry date")
        or extract_date(text, "expires")
    )
    amount = extract_amount(text)
    status = extract_status(text)

    return {
        "File": uploaded_file.name,
        "Invoice Number": invoice_number,
        "Client": client,
        "Invoice Date": normalize_date(invoice_date),
        "Due Date": normalize_date(due_date),
        "Amount": amount,
        "Status": status,
    }


if uploaded_files:
    records = []
    progress = st.progress(0)

    for index, uploaded_file in enumerate(uploaded_files):
        try:
            record = process_invoice(uploaded_file)
            records.append(record)
        except Exception as error:
            st.error(f"Could not process {uploaded_file.name}: {error}")

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

df["Is Paid"] = df["Status"].str.lower().eq("paid")
df["Overdue"] = (
    (~df["Is Paid"])
    & df["Due Date"].notna()
    & (df["Due Date"] < today)
)
df["Outstanding"] = ~df["Is Paid"]


total_invoices = len(df)
outstanding_amount = df.loc[df["Outstanding"], "Amount"].sum()
overdue_amount = df.loc[df["Overdue"], "Amount"].sum()
paid_amount = df.loc[df["Is Paid"], "Amount"].sum()
overdue_count = int(df["Overdue"].sum())

st.subheader("📊 Invoice Dashboard")
col1, col2, col3, col4 = st.columns(4)

with col1:
    st.metric("📄 Invoices", total_invoices)
with col2:
    st.metric("💰 Outstanding", f"₹{outstanding_amount:,.2f}")
with col3:
    st.metric("🔴 Overdue", overdue_count)
with col4:
    st.metric("✅ Paid", f"₹{paid_amount:,.2f}")

if overdue_count:
    st.error(
        f"⚠️ {overdue_count} invoice(s) are overdue. "
        f"Total overdue amount: ₹{overdue_amount:,.2f}"
    )
else:
    st.success("🎉 No overdue invoices detected.")

st.divider()
st.subheader("🔎 Invoice Filters")

col1, col2 = st.columns(2)
with col1:
    status_options = sorted(df["Status"].unique())
    selected_status = st.multiselect(
        "Payment Status",
        status_options,
        default=status_options,
    )
with col2:
    overdue_only = st.checkbox("🔴 Show overdue invoices only")

filtered_df = df[df["Status"].isin(selected_status)].copy()

if overdue_only:
    filtered_df = filtered_df[filtered_df["Overdue"]]

display_df = filtered_df[
    [
        "File",
        "Invoice Number",
        "Client",
        "Invoice Date",
        "Due Date",
        "Amount",
        "Status",
        "Overdue",
    ]
].copy()


def highlight_overdue(row):
    if row["Overdue"]:
        return ["background-color: #ffcccc; color: #b00020; font-weight: bold;"] * len(row)
    return [""] * len(row)

styled_df = (
    display_df.style
    .apply(highlight_overdue, axis=1)
    .format(
        {
            "Amount": "₹{:,.2f}",
            "Invoice Date": lambda t: t.strftime("%Y-%m-%d") if pd.notna(t) else "N/A",
            "Due Date": lambda t: t.strftime("%Y-%m-%d") if pd.notna(t) else "N/A",
        }
    )
)

st.subheader("📋 Invoice Register")
st.dataframe(styled_df, width="stretch", hide_index=True)

download_df = display_df.copy()
download_df["Invoice Date"] = download_df["Invoice Date"].dt.strftime("%Y-%m-%d")
download_df["Due Date"] = download_df["Due Date"].dt.strftime("%Y-%m-%d")

csv_output = download_df.to_csv(index=False).encode("utf-8")

st.download_button(
    "⬇️ Download Invoice Report",
    csv_output,
    "invoice_report.csv",
    "text/csv",
)

st.divider()
st.caption("🧾 Invoice PDF Tracker • Built with Python + Streamlit + Pandas + PyPDF")
