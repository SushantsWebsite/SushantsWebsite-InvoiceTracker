# Invoice PDF Tracker

A Streamlit app for uploading invoice PDFs and extracting invoice metadata, overdue status, and payment information.

## Open the app

[Open Invoice PDF Tracker](https://sushantswebsite-sushantswebsite-invo-invoice-pdf-tracker-uu6jtf.streamlit.app/)

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
