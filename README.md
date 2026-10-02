# Invoice PDF Tracker

A Streamlit app for uploading invoice PDFs and extracting invoice metadata, overdue status, and payment information.

## Open the app

Deploy this repository on Streamlit Community Cloud:

- [Deploy Invoice PDF Tracker](https://share.streamlit.io/deploy?repository=https%3A%2F%2Fgithub.com%2FSushantsWebsite%2FSushantsWebsite-InvoiceTracker&branch=main&mainModule=invoice_pdf_tracker.py)

The public app URL will be added here after deployment is complete.

## Features

- Upload multiple PDF invoices
- Extract invoice number, client, dates, amount, and status
- Track overdue invoices
- Display overview metrics and dashboard
- Download filtered invoice data as CSV

## Run locally

```bash
pip install -r requirements.txt
streamlit run invoice_pdf_tracker.py
```

## Deployment

The repository includes `requirements.txt` for Streamlit Community Cloud. Select `main` as the branch and `invoice_pdf_tracker.py` as the app entry point.
