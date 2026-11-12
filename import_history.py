```python
import os
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv
from openpyxl import load_workbook
from supabase import create_client


# --------------------------------------------------
# 1. Load Supabase credentials from .env
# --------------------------------------------------
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_SECRET_KEY", "").strip()

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError(
        "Missing SUPABASE_URL or SUPABASE_SECRET_KEY in .env."
    )

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


# --------------------------------------------------
# 2. Open the Excel workbook
# --------------------------------------------------
EXCEL_FILE = Path(__file__).parent / "Recovereed.xlsx"

if not EXCEL_FILE.exists():
    raise FileNotFoundError(
        f"Excel file not found: {EXCEL_FILE}\n"
        "Check the spelling of the filename in your project."
    )

workbook = load_workbook(EXCEL_FILE, data_only=True)

if "Sheet1" not in workbook.sheetnames:
    raise ValueError(
        f"Sheet1 was not found. Available sheets: {workbook.sheetnames}"
    )

sheet = workbook["Sheet1"]


# --------------------------------------------------
# 3. Find the Excel column headings
# --------------------------------------------------
header_row = None
columns = {}

required_headers = {
    "Date",
    "Weight (kg)",
    "Weight Range Category",
    "Daily Feed Amount (kg)",
}

for row_num in range(1, min(sheet.max_row, 30) + 1):
    normalized = {}

    for col_num in range(1, sheet.max_column + 1):
        value = sheet.cell(row_num, col_num).value

        if value is not None:
            normalized[str(value).strip()] = col_num

    if required_headers.issubset(normalized.keys()):
        header_row = row_num
        columns = normalized
        break

if header_row is None:
    raise ValueError(
        "Could not find all required headings in Sheet1.\n"
        "Expected: Date, Weight (kg), Weight Range Category, "
        "Daily Feed Amount (kg)."
    )

print(f"Found Excel headings on row {header_row}.")


# --------------------------------------------------
# 4. Convert Excel-formatted weights to kilograms
# --------------------------------------------------
def convert_excel_weight(value):
    if isinstance(value, datetime):
        if value < datetime(1900, 3, 1):
            origin = datetime(1899, 12, 31)
        else:
            origin = datetime(1899, 12, 30)

        return (value - origin).total_seconds() / 86400

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)

    raise ValueError(f"Unexpected weight value: {value!r}")


# --------------------------------------------------
# 5. Convert feed amounts to kilograms
# --------------------------------------------------
def convert_feed_amount(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)

    if isinstance(value, str):
        cleaned = value.lower().replace("kg", "").strip()
        return float(cleaned)

    raise ValueError(f"Unexpected feed amount: {value!r}")


# --------------------------------------------------
# 6. Read historical daily records
# --------------------------------------------------
START_DATE = date(2026, 7, 28)
END_DATE = date(2026, 10, 17)
DEVICE_ID = "pig-feeder-01"

records = []
seen_dates = set()

for row_num in range(header_row + 1, sheet.max_row + 1):
    date_value = sheet.cell(
        row_num, columns["Date"]
    ).value

    if not isinstance(date_value, (date, datetime)):
        continue

    feeding_date = (
        date_value.date()
        if isinstance(date_value, datetime)
        else date_value
    )

    if not START_DATE <= feeding_date <= END_DATE:
        continue

    if feeding_date in seen_dates:
        raise ValueError(
            f"Duplicate date {feeding_date} in Excel row {row_num}."
        )

    weight_value = sheet.cell(
        row_num, columns["Weight (kg)"]
    ).value

    category_value = sheet.cell(
        row_num, columns["Weight Range Category"]
    ).value

    feed_value = sheet.cell(
        row_num, columns["Daily Feed Amount (kg)"]
    ).value

    pig_weight_kg = convert_excel_weight(weight_value)
    feed_amount_kg = convert_feed_amount(feed_value)

    if pig_weight_kg <= 0 or feed_amount_kg < 0:
        raise ValueError(
            f"Invalid weight or feed amount in Excel row {row_num}."
        )

    category = (
        str(category_value).strip()
        if category_value is not None
        else None
    )

    records.append({
        "device_id": DEVICE_ID,
        "feeding_date": feeding_date.isoformat(),
        "pig_weight_kg": round(pig_weight_kg, 2),
        "weight_range_category": category,
        "feed_amount_kg": round(feed_amount_kg, 3),
        "result": "Recovered from Excel",
        "feeding_time": "Daily total",
    })

    seen_dates.add(feeding_date)


# --------------------------------------------------
# 7. Validate and preview before uploading
# --------------------------------------------------
records.sort(key=lambda item: item["feeding_date"])

if not records:
    raise ValueError(
        "No records found in the requested date range. "
        "Check the Excel dates and column headings."
    )

expected_days = (END_DATE - START_DATE).days + 1

print("\n--- IMPORT PREVIEW ---")
print("Records found:", len(records))
print("Expected calendar days:", expected_days)
print("First record:", records[0])
print("Last record:", records[-1])

total_feed = round(
    sum(record["feed_amount_kg"] for record in records), 3
)
print("Total daily feed (kg):", total_feed)

if records[0]["feeding_date"] != START_DATE.isoformat():
    raise ValueError("The first record is not July 28, 2026.")

if records[-1]["feeding_date"] != END_DATE.isoformat():
    raise ValueError("The last record is not October 17, 2026.")

if len(records) != expected_days:
    raise ValueError(
        f"Found {len(records)} records, but expected {expected_days}. "
        "Check for missing dates before importing."
    )

if abs(records[0]["pig_weight_kg"] - 7.3) > 0.01:
    raise ValueError(
        "First weight is not approximately 7.3 kg. "
        "Check Excel weight conversion."
    )

if abs(records[-1]["pig_weight_kg"] - 88.6) > 0.01:
    raise ValueError(
        "Last weight is not approximately 88.6 kg. "
        "Check Excel weight conversion."
    )

print("\nValidation passed.")

confirmation = input(
    "Type IMPORT to upload these records, or anything else to cancel: "
).strip()

if confirmation != "IMPORT":
    print("Cancelled. No records were uploaded.")
    raise SystemExit


# --------------------------------------------------
# 8. Upload records to Supabase
# --------------------------------------------------
try:
    for start in range(0, len(records), 50):
        batch = records[start:start + 50]

        response = (
            supabase.table("historical_feeding_records")
            .upsert(
                batch,
                on_conflict="device_id,feeding_date"
            )
            .execute()
        )

        print(
            f"Successfully submitted batch "
            f"{start // 50 + 1}."
        )

    print(
        f"\nFinished. Submitted {len(records)} historical daily records."
    )
    print("Verify the final record count in Supabase.")

except Exception as error:
    print("\nUPLOAD FAILED.")
    print("Supabase returned:", str(error))
    raise
```