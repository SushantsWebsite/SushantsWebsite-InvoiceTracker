# Invoice PDF Tracker

A Streamlit app for uploading invoice PDFs and extracting invoice metadata, overdue status, and payment information.

## Live app

Local development URL:

- http://localhost:8503

## Features

- Upload multiple PDF invoices
- Extract invoice number, client, dates, amount, and status
- Track overdue invoices
- Display overview metrics and dashboard
- Download filtered invoice data as CSV

## Run locally

```bash
pip install streamlit pandas pypdf
streamlit run invoice_pdf_tracker.py
```

## Deployment

This project is ready to be deployed to Streamlit Cloud or any Python hosting service.
