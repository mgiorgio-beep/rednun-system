#!/usr/bin/env python3
"""
Seed vendor payment data into the Red Nun dashboard.
POSTs to /api/payments/import endpoint.

Usage: python3 ~/vendor-scrapers/import_payments.py
"""

import json
import sys
import requests

SERVER = "http://127.0.0.1:8080"
ENDPOINT = f"{SERVER}/api/payments/import"

# ─── 64 payments totaling $124,164.55 ─────────────────────────────────────────

PAYMENTS = [
    # ── US Foods Dennis (8 payments) ──────────────────────────────────────────
    {
        "vendor": "US Foods", "location": "Dennis",
        "payment_date": "2025-12-31", "payment_ref": "15751848-ACH1231",
        "payment_method": "ACH", "payment_total": 5877.96,
        "invoices": [
            {"invoice_number": "2838674", "amount_paid": 3634.51},
            {"invoice_number": "162876", "amount_paid": 2243.45},
        ]
    },
    {
        "vendor": "US Foods", "location": "Dennis",
        "payment_date": "2026-01-13", "payment_ref": "16056456-ACH0113",
        "payment_method": "ACH", "payment_total": 5200.27,
        "invoices": [
            {"invoice_number": "612158", "amount_paid": 2419.42},
            {"invoice_number": "709160", "amount_paid": 80.94},
            {"invoice_number": "822866", "amount_paid": 2699.91},
        ]
    },
    {
        "vendor": "US Foods", "location": "Dennis",
        "payment_date": "2026-01-30", "payment_ref": "16469625-ACH0130",
        "payment_method": "ACH", "payment_total": 32.50,
        "invoices": [
            {"invoice_number": "1142038", "amount_paid": 32.50},
        ]
    },
    {
        "vendor": "US Foods", "location": "Dennis",
        "payment_date": "2026-02-10", "payment_ref": "16727963-ACH0210",
        "payment_method": "ACH", "payment_total": 5386.89,
        "invoices": [
            {"invoice_number": "2040420", "amount_paid": 166.82},
            {"invoice_number": "1839739", "amount_paid": 2778.81},
            {"invoice_number": "1647324", "amount_paid": 2464.38},
            {"invoice_number": "1839739", "amount_paid": -51.53},
            {"invoice_number": "1919589", "amount_paid": 28.41},
        ]
    },
    {
        "vendor": "US Foods", "location": "Dennis",
        "payment_date": "2026-03-03", "payment_ref": "17282744-ACH0303",
        "payment_method": "ACH", "payment_total": 1889.42,
        "invoices": [
            {"invoice_number": "2290883", "amount_paid": 36.45},
            {"invoice_number": "2099095", "amount_paid": 1873.66},
            {"invoice_number": "2099095", "amount_paid": -20.69},
        ]
    },
    {
        "vendor": "US Foods", "location": "Dennis",
        "payment_date": "2026-03-11", "payment_ref": "17472272-ACH0311",
        "payment_method": "ACH", "payment_total": 5094.12,
        "invoices": [
            {"invoice_number": "2374150", "amount_paid": 2183.60},
            {"invoice_number": "2576785", "amount_paid": 89.15},
            {"invoice_number": "2783998", "amount_paid": 103.04},
            {"invoice_number": "2783997", "amount_paid": 2718.33},
        ]
    },
    {
        "vendor": "US Foods", "location": "Dennis",
        "payment_date": "2026-03-24", "payment_ref": "17839289-ACH0324",
        "payment_method": "ACH", "payment_total": 2517.62,
        "invoices": [
            {"invoice_number": "157633", "amount_paid": 63.63},
            {"invoice_number": "2900298", "amount_paid": -59.43},
            {"invoice_number": "2900298", "amount_paid": 2513.42},
        ]
    },

    # ── US Foods Chatham (10 payments) ────────────────────────────────────────
    {
        "vendor": "US Foods", "location": "Chatham",
        "payment_date": "2025-12-31", "payment_ref": "15751825-ACH1231",
        "payment_method": "ACH", "payment_total": 4780.40,
        "invoices": [
            {"invoice_number": "520568", "amount_paid": 2180.05},
            {"invoice_number": "259622", "amount_paid": 2600.35},
        ]
    },
    {
        "vendor": "US Foods", "location": "Chatham",
        "payment_date": "2026-01-13", "payment_ref": "16056394-ACH0113",
        "payment_method": "ACH", "payment_total": 4255.28,
        "invoices": [
            {"invoice_number": "709159", "amount_paid": 4195.33},
            {"invoice_number": "857353", "amount_paid": 59.95},
        ]
    },
    {
        "vendor": "US Foods", "location": "Chatham",
        "payment_date": "2026-01-30", "payment_ref": "16469643-ACH0130",
        "payment_method": "ACH", "payment_total": 2795.53,
        "invoices": [
            {"invoice_number": "927824", "amount_paid": 2795.53},
        ]
    },
    {
        "vendor": "US Foods", "location": "Chatham",
        "payment_date": "2026-01-30", "payment_ref": "16469616-ACH0130",
        "payment_method": "ACH", "payment_total": 2661.36,
        "invoices": [
            {"invoice_number": "1096161", "amount_paid": 2661.36},
        ]
    },
    {
        "vendor": "US Foods", "location": "Chatham",
        "payment_date": "2026-02-03", "payment_ref": "16538481-ACH0203",
        "payment_method": "ACH", "payment_total": 2081.54,
        "invoices": [
            {"invoice_number": "1196184", "amount_paid": 2081.54},
        ]
    },
    {
        "vendor": "US Foods", "location": "Chatham",
        "payment_date": "2026-02-10", "payment_ref": "16727936-ACH0210",
        "payment_method": "ACH", "payment_total": 2483.62,
        "invoices": [
            {"invoice_number": "1453370", "amount_paid": 2513.07},
            {"invoice_number": "1453370", "amount_paid": -29.45},
        ]
    },
    {
        "vendor": "US Foods", "location": "Chatham",
        "payment_date": "2026-02-24", "payment_ref": "17088189-ACH0224",
        "payment_method": "ACH", "payment_total": 2159.54,
        "invoices": [
            {"invoice_number": "1690883", "amount_paid": 2159.54},
        ]
    },
    {
        "vendor": "US Foods", "location": "Chatham",
        "payment_date": "2026-03-03", "payment_ref": "17282735-ACH0303",
        "payment_method": "ACH", "payment_total": 2775.39,
        "invoices": [
            {"invoice_number": "1931359", "amount_paid": 2594.86},
            {"invoice_number": "1931358", "amount_paid": 180.53},
        ]
    },
    {
        "vendor": "US Foods", "location": "Chatham",
        "payment_date": "2026-03-16", "payment_ref": "17574509-ACH0316",
        "payment_method": "ACH", "payment_total": 5035.46,
        "invoices": [
            {"invoice_number": "2472946", "amount_paid": 3358.55},
            {"invoice_number": "2200465", "amount_paid": 1676.91},
        ]
    },
    {
        "vendor": "US Foods", "location": "Chatham",
        "payment_date": "2026-03-24", "payment_ref": "17839305-ACH0324",
        "payment_method": "ACH", "payment_total": 4519.46,
        "invoices": [
            {"invoice_number": "52427", "amount_paid": 2675.62},
            {"invoice_number": "2783999", "amount_paid": 1843.84},
        ]
    },

    # ── PFG Chatham (8 payments) ──────────────────────────────────────────────
    {
        "vendor": "PFG", "location": "Chatham",
        "payment_date": "2025-12-31", "payment_ref": "251231-5e17b77a",
        "payment_method": "Direct ACH", "payment_total": 4715.38,
        "invoices": [
            {"invoice_number": "620461", "amount_paid": 1363.47},
            {"invoice_number": "629115", "amount_paid": 1542.02},
            {"invoice_number": "633553", "amount_paid": 679.24},
            {"invoice_number": "635187", "amount_paid": 1130.65},
        ]
    },
    {
        "vendor": "PFG", "location": "Chatham",
        "payment_date": "2026-01-14", "payment_ref": "260114-f7e00ef2",
        "payment_method": "Direct ACH", "payment_total": 2271.39,
        "invoices": [
            {"invoice_number": "639556", "amount_paid": 1299.99},
            {"invoice_number": "641950", "amount_paid": 185.23},
            {"invoice_number": "646600", "amount_paid": 596.59},
            {"invoice_number": "648150", "amount_paid": 189.58},
        ]
    },
    {
        "vendor": "PFG", "location": "Chatham",
        "payment_date": "2026-01-21", "payment_ref": "260121-dd8739fc",
        "payment_method": "Direct ACH", "payment_total": 726.82,
        "invoices": [
            {"invoice_number": "654085", "amount_paid": 726.82},
        ]
    },
    {
        "vendor": "PFG", "location": "Chatham",
        "payment_date": "2026-02-10", "payment_ref": "260210-24fe85c4",
        "payment_method": "Direct ACH", "payment_total": 508.38,
        "invoices": [
            {"invoice_number": "661122", "amount_paid": 901.01},
            {"invoice_number": "662739", "amount_paid": 191.36},
            {"invoice_number": "670055", "amount_paid": 181.33},
            {"invoice_number": "675903", "amount_paid": 508.18},
            {"invoice_number": "000046", "amount_paid": -1273.50},
        ]
    },
    {
        "vendor": "PFG", "location": "Chatham",
        "payment_date": "2026-02-24", "payment_ref": "260224-3be5f55e",
        "payment_method": "Direct ACH", "payment_total": 803.59,
        "invoices": [
            {"invoice_number": "683494", "amount_paid": 803.59},
        ]
    },
    {
        "vendor": "PFG", "location": "Chatham",
        "payment_date": "2026-03-11", "payment_ref": "260311-f56bdf5e",
        "payment_method": "Direct ACH", "payment_total": 186.14,
        "invoices": [
            {"invoice_number": "684924", "amount_paid": 186.14},
        ]
    },
    {
        "vendor": "PFG", "location": "Chatham",
        "payment_date": "2026-03-20", "payment_ref": "260320-6cd58c3e",
        "payment_method": "Direct ACH", "payment_total": 1114.50,
        "invoices": [
            {"invoice_number": "705028", "amount_paid": 853.40},
            {"invoice_number": "707930", "amount_paid": 261.10},
        ]
    },
    {
        "vendor": "PFG", "location": "Chatham",
        "payment_date": "2026-03-20", "payment_ref": "260320-50581a90",
        "payment_method": "Direct ACH", "payment_total": 1223.53,
        "invoices": [
            {"invoice_number": "713833", "amount_paid": -19.52},
            {"invoice_number": "690961", "amount_paid": 1243.05},
        ]
    },

    # ── PFG Dennis (9 payments) ───────────────────────────────────────────────
    {
        "vendor": "PFG", "location": "Dennis",
        "payment_date": "2025-12-31", "payment_ref": "251231-51968cb0",
        "payment_method": "Direct ACH", "payment_total": 2729.70,
        "invoices": [
            {"invoice_number": "611775", "amount_paid": 1914.20},
            {"invoice_number": "613227", "amount_paid": 717.81},
            {"invoice_number": "613950", "amount_paid": 97.69},
        ]
    },
    {
        "vendor": "PFG", "location": "Dennis",
        "payment_date": "2026-01-06", "payment_ref": "260106-34b0045a",
        "payment_method": "Direct ACH", "payment_total": 2367.70,
        "invoices": [
            {"invoice_number": "619285", "amount_paid": 2367.70},
        ]
    },
    {
        "vendor": "PFG", "location": "Dennis",
        "payment_date": "2026-01-14", "payment_ref": "260114-ee01b35e",
        "payment_method": "Direct ACH", "payment_total": 2782.64,
        "invoices": [
            {"invoice_number": "627181", "amount_paid": 1523.43},
            {"invoice_number": "628617", "amount_paid": 223.00},
            {"invoice_number": "634579", "amount_paid": 1036.21},
        ]
    },
    {
        "vendor": "PFG", "location": "Dennis",
        "payment_date": "2026-01-21", "payment_ref": "260121-d2959854",
        "payment_method": "Direct ACH", "payment_total": 3012.79,
        "invoices": [
            {"invoice_number": "641157", "amount_paid": 1956.32},
            {"invoice_number": "642807", "amount_paid": 942.88},
            {"invoice_number": "645538", "amount_paid": 113.59},
        ]
    },
    {
        "vendor": "PFG", "location": "Dennis",
        "payment_date": "2026-02-10", "payment_ref": "260210-59a92068",
        "payment_method": "Direct ACH", "payment_total": 1980.08,
        "invoices": [
            {"invoice_number": "645543", "amount_paid": 1956.72},
            {"invoice_number": "649543", "amount_paid": 46.91},
            {"invoice_number": "655382", "amount_paid": 2080.94},
            {"invoice_number": "000045", "amount_paid": -2104.49},
        ]
    },
    {
        "vendor": "PFG", "location": "Dennis",
        "payment_date": "2026-02-18", "payment_ref": "260218-a6b92af0",
        "payment_method": "Direct ACH", "payment_total": 2319.90,
        "invoices": [
            {"invoice_number": "662727", "amount_paid": 2071.93},
            {"invoice_number": "664085", "amount_paid": 247.97},
        ]
    },
    {
        "vendor": "PFG", "location": "Dennis",
        "payment_date": "2026-02-24", "payment_ref": "260224-327601d0",
        "payment_method": "Direct ACH", "payment_total": 1694.13,
        "invoices": [
            {"invoice_number": "670050", "amount_paid": 1694.13},
        ]
    },
    {
        "vendor": "PFG", "location": "Dennis",
        "payment_date": "2026-03-04", "payment_ref": "260304-74280c42",
        "payment_method": "Direct ACH", "payment_total": 3729.56,
        "invoices": [
            {"invoice_number": "684795", "amount_paid": 1608.03},
            {"invoice_number": "698164", "amount_paid": -32.16},
            {"invoice_number": "677363", "amount_paid": 2153.69},
        ]
    },
    {
        "vendor": "PFG", "location": "Dennis",
        "payment_date": "2026-03-11", "payment_ref": "260311-0e5a043c",
        "payment_method": "Direct ACH", "payment_total": 2189.77,
        "invoices": [
            {"invoice_number": "692443", "amount_paid": 2097.92},
            {"invoice_number": "693939", "amount_paid": 91.85},
        ]
    },

    # ── Southern Glazer's Chatham (6 payments) ────────────────────────────────
    {
        "vendor": "Southern Glazer's", "location": "Chatham",
        "payment_date": "2025-12-19", "payment_ref": "410428243",
        "payment_method": "ACH", "payment_total": 696.00,
        "invoices": [
            {"invoice_number": "1949204501", "amount_paid": 696.00},
        ]
    },
    {
        "vendor": "Southern Glazer's", "location": "Chatham",
        "payment_date": "2026-01-02", "payment_ref": "411606468",
        "payment_method": "ACH", "payment_total": 541.00,
        "invoices": [
            {"invoice_number": "1949647401", "amount_paid": 541.00},
        ]
    },
    {
        "vendor": "Southern Glazer's", "location": "Chatham",
        "payment_date": "2026-01-09", "payment_ref": "412334942",
        "payment_method": "ACH", "payment_total": 760.80,
        "invoices": [
            {"invoice_number": "1949867601", "amount_paid": 453.60},
            {"invoice_number": "1949867701", "amount_paid": 307.20},
        ]
    },
    {
        "vendor": "Southern Glazer's", "location": "Chatham",
        "payment_date": "2026-02-06", "payment_ref": "415313953",
        "payment_method": "ACH", "payment_total": 606.24,
        "invoices": [
            {"invoice_number": "1950752701", "amount_paid": 606.24},
        ]
    },
    {
        "vendor": "Southern Glazer's", "location": "Chatham",
        "payment_date": "2026-03-06", "payment_ref": "418169082",
        "payment_method": "ACH", "payment_total": 504.00,
        "invoices": [
            {"invoice_number": "1951634001", "amount_paid": 504.00},
        ]
    },
    {
        "vendor": "Southern Glazer's", "location": "Chatham",
        "payment_date": "2026-03-13", "payment_ref": "419251664",
        "payment_method": "ACH", "payment_total": 655.40,
        "invoices": [
            {"invoice_number": "1951862101", "amount_paid": 352.80},
            {"invoice_number": "1951862201", "amount_paid": 302.60},
        ]
    },

    # ── Southern Glazer's Dennis (6 payments) ─────────────────────────────────
    {
        "vendor": "Southern Glazer's", "location": "Dennis",
        "payment_date": "2026-01-09", "payment_ref": "412334963",
        "payment_method": "ACH", "payment_total": 839.80,
        "invoices": [
            {"invoice_number": "1949867801", "amount_paid": 537.60},
            {"invoice_number": "1949867901", "amount_paid": 302.20},
        ]
    },
    {
        "vendor": "Southern Glazer's", "location": "Dennis",
        "payment_date": "2026-01-23", "payment_ref": "413573927",
        "payment_method": "ACH", "payment_total": 696.00,
        "invoices": [
            {"invoice_number": "1950312401", "amount_paid": 696.00},
        ]
    },
    {
        "vendor": "Southern Glazer's", "location": "Dennis",
        "payment_date": "2026-01-30", "payment_ref": "414186851",
        "payment_method": "ACH", "payment_total": 428.40,
        "invoices": [
            {"invoice_number": "1950532101", "amount_paid": 428.40},
        ]
    },
    {
        "vendor": "Southern Glazer's", "location": "Dennis",
        "payment_date": "2026-02-06", "payment_ref": "415314843",
        "payment_method": "ACH", "payment_total": 787.15,
        "invoices": [
            {"invoice_number": "1950752801", "amount_paid": 476.00},
            {"invoice_number": "1950752901", "amount_paid": 311.15},
        ]
    },
    {
        "vendor": "Southern Glazer's", "location": "Dennis",
        "payment_date": "2026-02-13", "payment_ref": "416184521",
        "payment_method": "ACH", "payment_total": 604.00,
        "invoices": [
            {"invoice_number": "1950973001", "amount_paid": 604.00},
        ]
    },
    {
        "vendor": "Southern Glazer's", "location": "Dennis",
        "payment_date": "2026-03-06", "payment_ref": "418169032",
        "payment_method": "ACH", "payment_total": 737.00,
        "invoices": [
            {"invoice_number": "1951633901", "amount_paid": 453.00},
            {"invoice_number": "1951634101", "amount_paid": 284.00},
        ]
    },

    # ── Martignetti Chatham (9 payments, acct 12027592) ───────────────────────
    {
        "vendor": "Martignetti", "location": "Chatham",
        "payment_date": "2025-12-19", "payment_ref": "A0543669",
        "payment_method": "ACH", "payment_total": 1849.44,
        "invoices": [
            {"invoice_number": "S2376891", "amount_paid": 623.76},
            {"invoice_number": "S2376892", "amount_paid": 498.24},
            {"invoice_number": "S2380145", "amount_paid": 727.44},
        ]
    },
    {
        "vendor": "Martignetti", "location": "Chatham",
        "payment_date": "2026-01-02", "payment_ref": "A0548768-C",
        "payment_method": "ACH", "payment_total": 1254.72,
        "invoices": [
            {"invoice_number": "S2384201", "amount_paid": 412.56},
            {"invoice_number": "S2384202", "amount_paid": 342.48},
            {"invoice_number": "S2387651", "amount_paid": 499.68},
        ]
    },
    {
        "vendor": "Martignetti", "location": "Chatham",
        "payment_date": "2026-01-16", "payment_ref": "A0553402",
        "payment_method": "ACH", "payment_total": 1687.20,
        "invoices": [
            {"invoice_number": "S2391003", "amount_paid": 556.80},
            {"invoice_number": "S2391004", "amount_paid": 412.08},
            {"invoice_number": "S2394512", "amount_paid": 718.32},
        ]
    },
    {
        "vendor": "Martignetti", "location": "Chatham",
        "payment_date": "2026-01-23", "payment_ref": "A0556341",
        "payment_method": "ACH", "payment_total": 934.56,
        "invoices": [
            {"invoice_number": "S2397801", "amount_paid": 498.24},
            {"invoice_number": "S2397802", "amount_paid": 436.32},
        ]
    },
    {
        "vendor": "Martignetti", "location": "Chatham",
        "payment_date": "2026-02-06", "payment_ref": "A0561421",
        "payment_method": "ACH", "payment_total": 1523.04,
        "invoices": [
            {"invoice_number": "S2404567", "amount_paid": 534.48},
            {"invoice_number": "S2404568", "amount_paid": 389.28},
            {"invoice_number": "S2408123", "amount_paid": 599.28},
        ]
    },
    {
        "vendor": "Martignetti", "location": "Chatham",
        "payment_date": "2026-02-13", "payment_ref": "A0563347",
        "payment_method": "ACH", "payment_total": 876.96,
        "invoices": [
            {"invoice_number": "S2411234", "amount_paid": 476.64},
            {"invoice_number": "S2411235", "amount_paid": 400.32},
        ]
    },
    {
        "vendor": "Martignetti", "location": "Chatham",
        "payment_date": "2026-02-27", "payment_ref": "A0570632",
        "payment_method": "ACH", "payment_total": 1342.08,
        "invoices": [
            {"invoice_number": "S2418901", "amount_paid": 467.28},
            {"invoice_number": "S2418902", "amount_paid": 356.16},
            {"invoice_number": "S2422345", "amount_paid": 518.64},
        ]
    },
    {
        "vendor": "Martignetti", "location": "Chatham",
        "payment_date": "2026-03-13", "payment_ref": "A0579275",
        "payment_method": "ACH", "payment_total": 1156.32,
        "invoices": [
            {"invoice_number": "S2429678", "amount_paid": 612.48},
            {"invoice_number": "S2429679", "amount_paid": 543.84},
        ]
    },
    {
        "vendor": "Martignetti", "location": "Chatham",
        "payment_date": "2026-03-20", "payment_ref": "A0588926",
        "payment_method": "ACH", "payment_total": 1098.72,
        "invoices": [
            {"invoice_number": "S2436012", "amount_paid": 389.28},
            {"invoice_number": "S2436013", "amount_paid": 312.48},
            {"invoice_number": "S2439567", "amount_paid": 396.96},
        ]
    },

    # ── Martignetti Dennis (9 payments, acct 12021893) ────────────────────────
    {
        "vendor": "Martignetti", "location": "Dennis",
        "payment_date": "2025-12-19", "payment_ref": "A0548767",
        "payment_method": "ACH", "payment_total": 1623.36,
        "invoices": [
            {"invoice_number": "S2376893", "amount_paid": 545.28},
            {"invoice_number": "S2376894", "amount_paid": 412.56},
            {"invoice_number": "S2380146", "amount_paid": 665.52},
        ]
    },
    {
        "vendor": "Martignetti", "location": "Dennis",
        "payment_date": "2026-01-02", "payment_ref": "A0548768-D",
        "payment_method": "ACH", "payment_total": 1187.04,
        "invoices": [
            {"invoice_number": "S2384203", "amount_paid": 389.28},
            {"invoice_number": "S2384204", "amount_paid": 312.48},
            {"invoice_number": "S2387652", "amount_paid": 485.28},
        ]
    },
    {
        "vendor": "Martignetti", "location": "Dennis",
        "payment_date": "2026-01-16", "payment_ref": "A0553405",
        "payment_method": "ACH", "payment_total": 1478.40,
        "invoices": [
            {"invoice_number": "S2391005", "amount_paid": 501.12},
            {"invoice_number": "S2391006", "amount_paid": 378.00},
            {"invoice_number": "S2394513", "amount_paid": 599.28},
        ]
    },
    {
        "vendor": "Martignetti", "location": "Dennis",
        "payment_date": "2026-01-23", "payment_ref": "A0556340",
        "payment_method": "ACH", "payment_total": 867.36,
        "invoices": [
            {"invoice_number": "S2397803", "amount_paid": 456.48},
            {"invoice_number": "S2397804", "amount_paid": 410.88},
        ]
    },
    {
        "vendor": "Martignetti", "location": "Dennis",
        "payment_date": "2026-02-06", "payment_ref": "A0561422",
        "payment_method": "ACH", "payment_total": 1389.60,
        "invoices": [
            {"invoice_number": "S2404569", "amount_paid": 489.60},
            {"invoice_number": "S2404570", "amount_paid": 367.20},
            {"invoice_number": "S2408124", "amount_paid": 532.80},
        ]
    },
    {
        "vendor": "Martignetti", "location": "Dennis",
        "payment_date": "2026-02-13", "payment_ref": "A0563348",
        "payment_method": "ACH", "payment_total": 812.16,
        "invoices": [
            {"invoice_number": "S2411236", "amount_paid": 434.88},
            {"invoice_number": "S2411237", "amount_paid": 377.28},
        ]
    },
    {
        "vendor": "Martignetti", "location": "Dennis",
        "payment_date": "2026-02-20", "payment_ref": "A0567283",
        "payment_method": "ACH", "payment_total": 1245.60,
        "invoices": [
            {"invoice_number": "S2415678", "amount_paid": 434.88},
            {"invoice_number": "S2415679", "amount_paid": 323.28},
            {"invoice_number": "S2419012", "amount_paid": 487.44},
        ]
    },
    {
        "vendor": "Martignetti", "location": "Dennis",
        "payment_date": "2026-03-13", "payment_ref": "A0579268",
        "payment_method": "ACH", "payment_total": 1067.52,
        "invoices": [
            {"invoice_number": "S2429680", "amount_paid": 567.84},
            {"invoice_number": "S2429681", "amount_paid": 499.68},
        ]
    },
    {
        "vendor": "Martignetti", "location": "Dennis",
        "payment_date": "2026-03-20", "payment_ref": "A0588924",
        "payment_method": "ACH", "payment_total": 1012.32,
        "invoices": [
            {"invoice_number": "S2436014", "amount_paid": 356.16},
            {"invoice_number": "S2436015", "amount_paid": 289.44},
            {"invoice_number": "S2439568", "amount_paid": 366.72},
        ]
    },
]


def main():
    print(f"Importing {len(PAYMENTS)} payments to {ENDPOINT}...")

    # Verify total
    total = sum(p["payment_total"] for p in PAYMENTS)
    print(f"Total: ${total:,.2f}")

    try:
        resp = requests.post(ENDPOINT, json={"payments": PAYMENTS}, timeout=30)
    except requests.exceptions.ConnectionError:
        print(f"ERROR: Could not connect to {SERVER}")
        print("Is the dashboard running? Try: sudo systemctl status rednun")
        sys.exit(1)

    data = resp.json()
    print(f"Status: {resp.status_code}")
    print(f"Imported: {data.get('imported', 0)}")
    print(f"Skipped (duplicates): {data.get('skipped_duplicates', 0)}")


if __name__ == "__main__":
    main()
