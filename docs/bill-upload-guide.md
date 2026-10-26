# How to Upload a Bill in UBMS

A step-by-step guide for uploading bills into the Unit Billing Management System (UBMS). This is written for someone new to the system — it covers the click-by-click UI flow, the exact file format required, and what happens behind the scenes so you know how to interpret the results.

## Who can do this

Bill upload is an **Administrator-only** action. If you're logged in as a Unit Accountant or Leadership user, you won't see the Upload page at all — it's hidden from your navigation and blocked at the route level.

## Before you start

Bills are uploaded in bulk, one account at a time, via an Excel file — there is no form for entering a single bill by hand. For an uploaded bill to succeed, two things must already exist in UBMS:

1. The **account** (in Account Master) must exist and be **Active**.
2. That account must have at least one **Active allocation mapping** to a unit (Account ↔ Unit allocation, adding up to 100%). This is what tells UBMS which unit(s) — and therefore which Unit Accountant(s) — the bill should be split across.

If either is missing, the bill will fail to upload (see "Why did some rows fail?" below) — set these up first in Accounts / Units if you haven't already.

## Step 1 — Go to the Bill Upload page

1. Log in as an Administrator.
2. In the left sidebar, click **"Bill Upload"**. (URL: `/upload`.)

You'll see two sections: **"Upload Bill Data (Excel)"** at the top, and **"Upload History"** below it, listing past upload batches.

## Step 2 — Get the file template (recommended)

You don't have to build the spreadsheet from scratch:

1. In the "Upload Bill Data (Excel)" section, click the **"Template ▾"** button.
2. A list of every active account (account number + name) opens in a popover.
3. Click the account you're billing. A file named `bill-template-<accountNumber>.xlsx` downloads automatically, with the account number already filled in on row 1.

> **Note:** the downloaded template only pre-fills 4 columns (`account_number`, `billing_period`, `bill_date`, `total_amount`). The backend also accepts an optional 5th column, `due_date` — add it manually to the header row if you want to record one (see format table below).

If the popover shows "No active accounts available," it means no account is active yet — go create/activate one in Account Master first.

## Step 3 — Fill in the spreadsheet

Each row = one bill for one account. Required and optional columns (header row, case-insensitive):

| Column | Required? | Notes |
|---|---|---|
| `account_number` | **Yes** | Must exactly match an existing, **Active** account in Account Master. If it doesn't match, that row fails. |
| `total_amount` | **Yes** | The bill amount, numeric. Blank = the row fails. |
| `billing_period` | No | Stored as free text/whatever value you enter — not validated. |
| `bill_date` | No | Stored as-is. |
| `due_date` | No | Parsed as a date if provided; if it can't be parsed, it's just stored as blank — it won't fail the row. |

Other things to know:
- Currency is **always AED** — there's no currency column, and nothing else is currently supported.
- You can put multiple rows (multiple bills) in one file, including rows for different accounts.
- There's no duplicate check — if you upload the same account/period twice, you'll get two separate bills. Double-check your file before uploading.

## Step 4 — Upload the file

1. Drag your finished `.xlsx`/`.xls` file onto the dropzone box (labeled **"Drag & drop your Excel file here"**), or click the box to browse and select it.
2. Limits: file must be `.xlsx` or `.xls`, and under **10 MB**.
3. While it uploads, the box dims and shows **"Uploading…"**.

## Step 5 — Read the result

You'll get a toast notification:

- **All rows succeeded:** green toast — *"Upload complete: X/Y records processed successfully."*
- **Some rows failed:** red toast — *"Upload complete: X/Y records processed successfully, Z failed."* This is expected/normal behavior, not a system error — see below.
- **The file itself was rejected** (wrong extension, corrupt file, not really an Excel file): red toast — *"Invalid file — check the format and try again."*

Either way, a new row appears at the top of **"Upload History"** showing the file name, who uploaded it and when, total records, ✓ success count, ✗ error count, and an overall batch status:
- **Completed** — every row succeeded.
- **Completed with Errors** — some rows succeeded, some failed.
- **Failed** — every row failed.

There is currently **no drill-down screen** to see exactly *which* rows failed or why — only the aggregate counts. If you get errors, the most common causes are covered below.

## Why did some rows fail?

A row is counted as an error (and skipped — it does **not** stop the rest of the file from processing) if any of these are true:

- `account_number` or `total_amount` is blank.
- `account_number` doesn't match any account in Account Master, or matches one that is **not Active**.
- The account matches, but has **no Active allocation mapping** to any unit — the bill can't be split to a Unit Accountant, so it's rejected entirely (nothing is saved for that row).

Rows are processed independently: if row 5 fails, rows 1–4 (and 6 onward) are unaffected and still get uploaded.

## What happens after a successful upload

For each successful row, UBMS creates:
1. One **bill** record, with status **"Pending Approval"**.
2. One **bill_unit_details** allocation row per Active account↔unit mapping for that account — the total amount is split proportionally by each mapping's allocation percentage (rounded to 2 decimals).

From here, the bill moves into the approval workflow:
- Each assigned **Unit Accountant** sees their share of the bill (only for units assigned to them) and can **approve** or **reject** it.
- Once every allocation for a bill has been approved, the bill's overall status automatically becomes **"Approved."**
- If any allocation is rejected, the whole bill goes **"On Hold"** for an Administrator to resolve.

The uploaded Excel file itself is **not saved anywhere** in UBMS — it's read once in memory and discarded, so keep your own copy if you need to re-upload or check it later.
