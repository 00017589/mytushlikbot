import logging

logger = logging.getLogger(__name__)

async def update_user_balance_in_sheet(telegram_id: int, new_balance: float) -> bool:
    """
    Update a user's balance in Google Sheets.
    Returns True if successful, False otherwise.
    """
    try:
        # Get the worksheet
        worksheet = await get_worksheet()
        if not worksheet:
            logger.error("Failed to get worksheet for balance update")
            return False

        # Get all values
        all_values = worksheet.get_all_values()
        if not all_values:
            logger.error("No data found in worksheet")
            return False

        # Find the row with matching telegram_id
        for i, row in enumerate(all_values[1:], start=2):  # Skip header row
            if len(row) >= 3 and row[2] == str(telegram_id):  # telegram_id is in column C
                # Update balance in column D
                worksheet.update_cell(i, 4, new_balance)
                logger.info(f"Updated balance for user {telegram_id} to {new_balance}")
                return True

        logger.warning(f"User {telegram_id} not found in sheet")
        return False

    except Exception as e:
        logger.error(f"Error updating balance in sheet: {str(e)}")
        return False

async def sync_balances_from_sheet() -> dict:
    """
    Sync balances from Google Sheets to the database.
    Returns a dict with sync results.
    """
    try:
        # Get the worksheet
        worksheet = await get_worksheet()
        if not worksheet:
            return {"success": False, "error": "Failed to get worksheet"}

        # Get all values
        all_values = worksheet.get_all_values()
        if not all_values:
            return {"success": False, "error": "No data found in worksheet"}

        # Process each row
        updated = 0
        errors = 0
        for row in all_values[1:]:  # Skip header row
            if len(row) >= 4:  # Ensure row has enough columns
                try:
                    telegram_id = int(row[2])  # Column C
                    balance = float(row[3])    # Column D
                    
                    # Update user in database
                    user = await get_user_async(telegram_id)
                    if user:
                        user.balance = balance
                        user.save()
                        updated += 1
                    else:
                        errors += 1
                        logger.warning(f"User {telegram_id} not found in database")
                except (ValueError, IndexError) as e:
                    errors += 1
                    logger.error(f"Error processing row: {str(e)}")

        return {
            "success": True,
            "updated": updated,
            "errors": errors
        }

    except Exception as e:
        logger.error(f"Error syncing balances: {str(e)}")
        return {"success": False, "error": str(e)} 