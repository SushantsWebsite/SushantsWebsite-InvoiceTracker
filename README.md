# Invoice PDF Tracker

A Streamlit app for extracting invoice metadata from PDFs, reviewing and correcting the results, tracking payments, and exporting reports.

## Features

- Upload multiple PDF invoices and extract invoice number, client, dates, amount, currency, and payment status
- OCR fallback for scanned PDFs when the required system tools are installed
- Edit extracted values in a review table and validate required fields
- Approve validated invoices; edits to an approved invoice revoke its approval
- Keep an audit history of initial extraction, edits, and approval changes
- Store reviewed metadata in a local SQLite database (PDF contents are not stored)
- View totals separately by currency and download reviewed data as CSV

## Run locally

```bash
pip install -r requirements.txt
streamlit run invoice_pdf_tracker.py
```

### OCR prerequisites

The Python OCR packages are listed in `requirements.txt`. OCR also requires the Tesseract executable and Poppler tools to be installed on the host. If those system tools are unavailable, text-based PDFs still work, but scanned PDFs will show an OCR error.

## Local database and deployment

By default, the app creates `invoice_tracker.db` in its working directory. Set `INVOICE_DB_PATH` to choose another writable path. The database contains invoice metadata and audit history, not uploaded PDF files. Treat it as sensitive financial data: keep it out of public repositories and back it up securely.

The SQLite file is local to the running app instance. On hosting platforms with ephemeral filesystems, the database may be lost on restart or may not be shared across multiple instances. Use a managed database or persistent volume for durable production storage. The app does not upload data to a remote database.

## Deploy on Streamlit Community Cloud

Select `main` as the branch and `invoice_pdf_tracker.py` as the app entry point. PDF text extraction is available with the Python requirements. OCR depends on host-installed Tesseract and Poppler; installing Python packages alone does not install those system binaries. Local SQLite storage should not be treated as durable on ephemeral hosting.

## Important

Invoice extraction is heuristic. Review the original PDF and confirm the extracted values before relying on them for accounting, collections, or payment decisions.
