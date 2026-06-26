"""
Credit Card Tracker Bot
Telegram bot that logs credit card transactions to Google Sheets.
"""
import os
import json
import logging
import re
from datetime import datetime

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

import gspread
from google.oauth2.service_account import Credentials

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────

BOT_TOKEN = "8881387349:AAEi75IzDKJ-U0w3ms8tFg37-jRboIa1hog"
SPREADSHEET_ID = "18waMn7TvK3xDCCF3r1cmVTl3HE6nn1_koD3rrsHuRIA"
SHEET_NAME = "Transactions"
SERVICE_ACCOUNT_FILE = "service_account.json"

VALID_BANKS = {"AMEX", "BPI", "BDO VISA", "ATOME", "SB"}
VALID_USERS = {"MD", "VK", "Others"}

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# GOOGLE SHEETS CONNECTION
# ─────────────────────────────────────────────

def get_sheet():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if creds_json:
        creds_dict = json.loads(creds_json)
        creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    else:
        creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    return spreadsheet.worksheet(SHEET_NAME)


def ensure_headers(sheet):
    headers = sheet.row_values(1)
    expected = ["#", "DATE", "BANK", "AMOUNT", "MERCHANT", "USAGE", "LOGGED AT"]
    if headers != expected:
        sheet.update("A1:G1", [expected])


# ─────────────────────────────────────────────
# PARSER
# ─────────────────────────────────────────────

FIELD_PATTERN = re.compile(
    r"credit card\s*:\s*(.+)\n"
    r"amount\s*:\s*(.+)\n"
    r"transaction\s*:\s*(.+)\n"
    r"usage\s*:\s*(.+)\n"
    r"date\s*:\s*(.+)",
    re.IGNORECASE,
)


def parse_message(text: str):
    """
    Returns a dict of fields or raises ValueError with a specific error message.
    """
    match = FIELD_PATTERN.search(text.strip())
    if not match:
        raise ValueError(
            "Format not recognized. Please use the exact format:\n\n"
            "credit card: <bank>\n"
            "amount: <amount>\n"
            "transaction: <merchant>\n"
            "usage: <name>\n"
            "date: <date>"
        )

    bank = match.group(1).strip().upper()
    amount_raw = match.group(2).strip()
    merchant = match.group(3).strip().upper()
    usage = match.group(4).strip().upper()
    date_raw = match.group(5).strip()

    # Validate bank
    if bank not in VALID_BANKS:
        raise ValueError(
            f"Invalid bank: '{bank.upper()}'. Accepted values: AMEX, BPI, BDO, ATOME, SB"
        )

    # Validate amount
    try:
        amount_clean = amount_raw.replace(",", "").replace("PHP", "").replace("₱", "").strip()
        amount = float(amount_clean)
    except ValueError:
        raise ValueError(f"Invalid amount: '{amount_raw}'. Please enter a numeric value.")

    # Validate usage
    usage_parts = [u.strip().upper() for u in re.split(r"[/,]", usage)]
    for part in usage_parts:
        if part not in VALID_USERS:
            raise ValueError(
                f"Invalid usage: '{part}'. Accepted values: MD, VK, Others"
            )

    # Validate date
    for fmt in ("%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d", "%d/%m/%Y", "%B %d, %Y"):
        try:
            parsed_date = datetime.strptime(date_raw, fmt)
            formatted_date = parsed_date.strftime("%m/%d/%Y")
            break
        except ValueError:
            continue
    else:
        raise ValueError(
            f"Invalid date: '{date_raw}'. Accepted formats: MM/DD/YYYY or MM-DD-YYYY"
        )

    return {
        "bank": bank,
        "amount": amount,
        "merchant": merchant,
        "usage": " / ".join(usage_parts),
        "date": formatted_date,
    }


# ─────────────────────────────────────────────
# SHEET LOGGING
# ─────────────────────────────────────────────

def log_to_sheet(data: dict) -> int:
    sheet = get_sheet()
    ensure_headers(sheet)

    # Count only rows that have actual data (excluding header row 1)
    existing_data = sheet.get_all_values()
    data_rows = [r for r in existing_data[1:] if any(cell.strip() for cell in r)]
    record_number = len(data_rows) + 1

    logged_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    row = [
        record_number,
        data["date"],
        data["bank"],
        data["amount"],
        data["merchant"],
        data["usage"],
        logged_at,
    ]
    # Write directly to the next truly empty row to avoid ghost row issues
    next_row = len(sheet.get_all_values()) + 1
    sheet.update(f"A{next_row}:G{next_row}", [row])
    return record_number


# ─────────────────────────────────────────────
# HANDLERS
# ─────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Credit Card Tracker is active.\n\n"
        "Send a transaction using this format:\n\n"
        "credit card: <AMEX / BPI / BDO VISA / ATOME / SB>\n"
        "amount: <amount>\n"
        "transaction: <merchant name>\n"
        "usage: <MD / VK / Others>\n"
        "date: <MM/DD/YYYY>"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    try:
        data = parse_message(text)
    except ValueError as e:
        await update.message.reply_text(f"Input error:\n{e}")
        return

    try:
        record_num = log_to_sheet(data)
    except Exception as e:
        logger.error("Sheet error: %s", e)
        await update.message.reply_text("Failed to save to Google Sheets. Please try again.")
        return

    confirmation = (
        f"Transaction saved.\n\n"
        f"Record #: {record_num}\n"
        f"Bank: {data['bank']}\n"
        f"Amount: PHP {data['amount']:,.2f}\n"
        f"Merchant: {data['merchant']}\n"
        f"Usage: {data['usage']}\n"
        f"Date: {data['date']}"
    )
    await update.message.reply_text(confirmation)


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
