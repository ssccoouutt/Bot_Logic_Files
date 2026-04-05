#!/usr/bin/env python3
"""
WhatsApp Bulk Send Handler - Complete /bulk command logic
- Handles entire conversation flow
- Asks for TXT file, message, and range
- Sends messages to multiple phone numbers
- Provides real-time progress updates
- Generates failure reports
- Same logic as /send command for each number
"""

import os
import time
import logging
import tempfile
import re
import asyncio
from datetime import datetime
from typing import Tuple, Optional, List, Dict, Any
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from selenium.webdriver.common.action_chains import ActionChains

# Configure logging
logger = logging.getLogger(__name__)

# Conversation states
WAITING_FOR_FILE = 1
WAITING_FOR_MESSAGE = 2
WAITING_FOR_RANGE = 3
WAITING_FOR_CONFIRMATION = 4

# Store user data
user_data: Dict[int, Dict[str, Any]] = {}

# ======================== UTILITY FUNCTIONS ========================

def is_phone_number(text: str) -> bool:
    """Check if text is a phone number format"""
    return bool(re.fullmatch(r'^\+\d{10,15}$', text.strip()))

def parse_range(range_str: str, max_index: int) -> Tuple[Optional[int], Optional[int]]:
    """
    Parse range string like "3 to 9" or "3-9" or "3"
    Returns (start, end) indices (1-based, inclusive)
    """
    range_str = range_str.strip().lower()
    
    # Check for "to" or "-" format
    if ' to ' in range_str:
        parts = range_str.split(' to ')
    elif '-' in range_str:
        parts = range_str.split('-')
    else:
        # Single number
        try:
            num = int(range_str)
            if 1 <= num <= max_index:
                return num, num
        except:
            pass
        return None, None
    
    if len(parts) == 2:
        try:
            start = int(parts[0].strip())
            end = int(parts[1].strip())
            
            # Validate
            if 1 <= start <= max_index and 1 <= end <= max_index and start <= end:
                return start, end
        except:
            pass
    
    return None, None

async def send_message_to_number(driver, phone_number: str, message: str, 
                                   index: int, total: int) -> Tuple[bool, str]:
    """
    Send a message to a single phone number using the same logic as /send command.
    Returns (success, error_message)
    """
    try:
        logger.info(f"[{index}/{total}] 📤 Sending to {phone_number}...")
        
        # Navigate to direct message chat
        phone_for_url = phone_number.replace("+", "")
        direct_url = f"https://web.whatsapp.com/send?phone={phone_for_url}"
        driver.get(direct_url)
        
        # Wait for "Starting Chat" overlay
        try:
            WebDriverWait(driver, 30).until(
                EC.invisibility_of_element_located((By.XPATH, "//div[contains(text(), 'Starting chat')]"))
            )
        except TimeoutException:
            pass
        
        # Wait for input box (5 minute timeout)
        try:
            WebDriverWait(driver, 300).until(
                EC.element_to_be_clickable((By.XPATH, '//div[@contenteditable="true"][@data-tab="10"]'))
            )
        except TimeoutException:
            # Check for invalid number error
            try:
                error_elem = driver.find_element(By.XPATH, 
                    "//*[contains(text(), 'Phone number shared via url is invalid') or "
                    "contains(text(), 'phone number is not valid')]"
                )
                return False, f"Invalid number: {error_elem.text[:100]}"
            except:
                return False, "Timeout waiting for chat to load (5 minutes)"
        
        time.sleep(1)
        
        # Find input box
        input_box = driver.find_element(By.XPATH, '//div[@contenteditable="true"][@data-tab="10"]')
        
        # Clear existing content
        input_box.click()
        input_box.send_keys(Keys.CONTROL + "a")
        input_box.send_keys(Keys.BACKSPACE)
        driver.execute_script("arguments[0].innerHTML = '';", input_box)
        
        # Send message line by line
        lines = message.split('\n')
        for i, line in enumerate(lines):
            if line:
                input_box.send_keys(line)
            if i < len(lines) - 1:
                ActionChains(driver).key_down(Keys.SHIFT).send_keys(Keys.ENTER).key_up(Keys.SHIFT).perform()
                time.sleep(0.1)
        
        # Press Enter to send
        input_box.send_keys(Keys.ENTER)
        time.sleep(2)
        
        # Verify (optional)
        try:
            outgoing = driver.find_elements(By.XPATH, '//div[contains(@class, "message-out")]//span[contains(@class, "selectable-text")]')
            if outgoing:
                last_msg = outgoing[-1].text
                if last_msg == message:
                    logger.info(f"✅ [{index}/{total}] Sent to {phone_number}")
                    return True, "Success"
                else:
                    logger.info(f"✅ [{index}/{total}] Sent to {phone_number} (content mismatch)")
                    return True, "Sent (content mismatch)"
        except:
            pass
        
        logger.info(f"✅ [{index}/{total}] Sent to {phone_number}")
        return True, "Sent"
        
    except Exception as e:
        error_msg = str(e)
        logger.error(f"❌ [{index}/{total}] Failed for {phone_number}: {error_msg}")
        return False, error_msg

# ======================== CONVERSATION HANDLERS ========================

async def start_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the bulk messaging conversation"""
    user_id = update.effective_user.id
    user_data[user_id] = {'state': WAITING_FOR_FILE}
    
    await update.message.reply_text(
        "📤 *Bulk Messaging*\n\n"
        "Please send me the `.txt` file containing phone numbers.\n"
        "One number per line, starting with `+` and country code.\n\n"
        "Example:\n"
        "```\n"
        "+1234567890\n"
        "+447911123456\n"
        "+920301234567\n"
        "```",
        parse_mode='Markdown'
    )
    
    return WAITING_FOR_FILE

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the uploaded TXT file"""
    user_id = update.effective_user.id
    document = update.message.document
    
    if user_id not in user_data:
        user_data[user_id] = {}
    
    if not document.file_name.endswith('.txt'):
        await update.message.reply_text("❌ Please send a `.txt` file.")
        return WAITING_FOR_FILE
    
    status_msg = await update.message.reply_text("📥 Downloading and processing file...")
    
    try:
        # Download file
        file = await context.bot.get_file(document.file_id)
        file_bytes = await file.download_as_bytearray()
        content = file_bytes.decode('utf-8')
        
        # Parse numbers
        numbers = []
        invalid_lines = []
        lines = content.split('\n')
        
        for i, line in enumerate(lines, 1):
            line = line.strip()
            if not line:
                continue
            if is_phone_number(line):
                numbers.append(line)
            else:
                invalid_lines.append(f"Line {i}: {line[:50]}")
        
        if not numbers:
            await status_msg.edit_text("❌ No valid phone numbers found in file.")
            return WAITING_FOR_FILE
        
        # Store in user data
        user_data[user_id]['numbers'] = numbers
        user_data[user_id]['invalid_lines'] = invalid_lines
        user_data[user_id]['file_content'] = content
        user_data[user_id]['state'] = WAITING_FOR_MESSAGE
        
        # Preview
        preview = "\n".join(numbers[:5])
        if len(numbers) > 5:
            preview += f"\n... and {len(numbers)-5} more"
        
        invalid_msg = f"\n\n⚠️ Ignored {len(invalid_lines)} invalid lines." if invalid_lines else ""
        
        await status_msg.edit_text(
            f"✅ Loaded *{len(numbers)}* valid numbers.{invalid_msg}\n\n"
            f"📋 Preview:\n```\n{preview}\n```\n\n"
            f"✏️ Now send me the message you want to send.\n"
            f"(Supports multiple lines, emojis, special characters)",
            parse_mode='Markdown'
        )
        
        return WAITING_FOR_MESSAGE
        
    except Exception as e:
        await status_msg.edit_text(f"❌ Error processing file: {e}")
        return WAITING_FOR_FILE

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the message to be sent"""
    user_id = update.effective_user.id
    message_text = update.message.text
    
    if user_id not in user_data:
        await update.message.reply_text("❌ Session expired. Please start over with /bulk")
        return -1
    
    user_data[user_id]['message'] = message_text
    user_data[user_id]['state'] = WAITING_FOR_RANGE
    
    total = len(user_data[user_id]['numbers'])
    
    await update.message.reply_text(
        f"✅ Message received. Length: {len(message_text)} chars, {message_text.count(chr(10))+1} lines.\n\n"
        f"📊 You have *{total}* numbers total.\n\n"
        f"Please specify the range to send:\n"
        f"• `3 to 9` - send from 3rd to 9th number\n"
        f"• `5-15` - send from 5th to 15th\n"
        f"• `7` - send only 7th number\n"
        f"• `all` - send to all numbers",
        parse_mode='Markdown'
    )
    
    return WAITING_FOR_RANGE

async def handle_range(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the range selection"""
    user_id = update.effective_user.id
    range_input = update.message.text.strip().lower()
    
    if user_id not in user_data:
        await update.message.reply_text("❌ Session expired. Please start over with /bulk")
        return -1
    
    data = user_data[user_id]
    numbers = data['numbers']
    total = len(numbers)
    
    # Parse range
    if range_input == 'all':
        start_idx, end_idx = 1, total
    else:
        start_idx, end_idx = parse_range(range_input, total)
        if start_idx is None:
            await update.message.reply_text(
                f"❌ Invalid range. Please use format like `3 to 9`, `5-15`, or `all`.\n"
                f"Total numbers: {total}",
                parse_mode='Markdown'
            )
            return WAITING_FOR_RANGE
    
    # Store selected range
    data['start_idx'] = start_idx
    data['end_idx'] = end_idx
    data['state'] = WAITING_FOR_CONFIRMATION
    
    selected_count = end_idx - start_idx + 1
    selected_numbers = numbers[start_idx-1:end_idx]
    
    # Create inline keyboard
    keyboard = [
        [
            InlineKeyboardButton("✅ Yes, Start Sending", callback_data="bulk_confirm_yes"),
            InlineKeyboardButton("❌ No, Cancel", callback_data="bulk_confirm_no")
        ],
        [InlineKeyboardButton("🔄 Change Range", callback_data="bulk_change_range")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Calculate estimated time
    est_minutes = selected_count * 1.5
    
    # Show preview
    preview_numbers = ", ".join(selected_numbers[:3])
    preview_msg = data['message'][:100] + ("..." if len(data['message']) > 100 else "")
    
    await update.message.reply_text(
        f"📤 *Confirmation*\n\n"
        f"Range: #{start_idx} to #{end_idx} of {total}\n"
        f"Numbers to send: *{selected_count}*\n"
        f"First few: `{preview_numbers}`\n"
        f"{'...' if selected_count > 3 else ''}\n\n"
        f"Message preview:\n```\n{preview_msg}\n```\n\n"
        f"⏱️ Estimated time: {est_minutes:.1f} minutes\n\n"
        f"Proceed?",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )
    
    return WAITING_FOR_CONFIRMATION

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle callback queries from inline keyboard"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    callback_data = query.data
    
    if user_id not in user_data:
        await query.edit_message_text("❌ Session expired. Please start over with /bulk")
        return -1
    
    data = user_data[user_id]
    
    if callback_data == "bulk_confirm_no":
        # Cancel
        await query.edit_message_text("❌ Bulk operation cancelled.")
        if user_id in user_data:
            del user_data[user_id]
        return -1
    
    elif callback_data == "bulk_change_range":
        # Go back to range input
        data['state'] = WAITING_FOR_RANGE
        total = len(data['numbers'])
        await query.edit_message_text(
            f"📊 You have *{total}* numbers total.\n\n"
            f"Please specify the range to send:\n"
            f"• `3 to 9` - send from 3rd to 9th number\n"
            f"• `5-15` - send from 5th to 15th\n"
            f"• `7` - send only 7th number\n"
            f"• `all` - send to all numbers",
            parse_mode='Markdown'
        )
        return WAITING_FOR_RANGE
    
    elif callback_data == "bulk_confirm_yes":
        # Start bulk sending
        await query.edit_message_text("🚀 Starting bulk send...")
        
        # Get data
        numbers = data['numbers']
        message = data['message']
        start_idx = data['start_idx']
        end_idx = data['end_idx']
        
        # Convert to 0-based indices
        start_0 = start_idx - 1
        end_0 = end_idx - 1
        selected_numbers = numbers[start_0:end_0+1]
        total_selected = len(selected_numbers)
        total_all = len(numbers)
        
        # Send initial status
        status_msg = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"🚀 Sending to {total_selected} numbers...\n0/{total_selected} complete"
        )
        
        # Results tracking
        success_count = 0
        fail_count = 0
        failures = []
        
        # Send one by one
        driver = context.bot_data.get('driver')  # Get driver from bot_data
        
        for idx, number in enumerate(selected_numbers, 1):
            global_idx = start_idx + idx - 1
            
            # Update status every 5 messages
            if idx % 5 == 0 or idx == 1:
                await status_msg.edit_text(
                    f"📤 Sending... {idx}/{total_selected}\n"
                    f"✅ Success: {success_count}\n"
                    f"❌ Failed: {fail_count}"
                )
            
            success, error = await send_message_to_number(
                driver, number, message, global_idx, total_all
            )
            
            if success:
                success_count += 1
            else:
                fail_count += 1
                failures.append(f"{number}: {error[:50]}")
            
            # Small delay between messages
            time.sleep(2)
        
        # Generate report
        report = (
            f"📊 *Bulk Send Complete*\n\n"
            f"📤 Total attempted: {total_selected}\n"
            f"✅ Successful: {success_count}\n"
            f"❌ Failed: {fail_count}\n"
        )
        
        if failures:
            report += f"\n⚠️ *First 10 Failures:*\n"
            report += "\n".join(f"• {f}" for f in failures[:10])
            if len(failures) > 10:
                report += f"\n... and {len(failures)-10} more"
            
            # Create failure report file
            report_file = tempfile.NamedTemporaryFile(delete=False, suffix='.txt', mode='w+', encoding='utf-8')
            report_file.write("=== Bulk Send Failures ===\n\n")
            report_file.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            report_file.write(f"Total Attempted: {total_selected}\n")
            report_file.write(f"Success: {success_count}\n")
            report_file.write(f"Failed: {fail_count}\n\n")
            report_file.write("=== Failed Numbers ===\n\n")
            for f in failures:
                report_file.write(f"{f}\n")
            report_file.close()
            
            # Send report file
            with open(report_file.name, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=f"bulk_failures_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
                    caption=f"Failure report for bulk send"
                )
            os.unlink(report_file.name)
        
        await status_msg.edit_text(report, parse_mode='Markdown')
        
        # Clean up
        if user_id in user_data:
            del user_data[user_id]
        
        return -1

async def handle_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle cancel command"""
    user_id = update.effective_user.id
    if user_id in user_data:
        del user_data[user_id]
    await update.message.reply_text("❌ Operation cancelled.")
    return -1

async def handle_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle timeout - user took too long"""
    user_id = update.effective_user.id
    if user_id in user_data:
        del user_data[user_id]
    await update.message.reply_text("⏱️ Operation timed out. Please start over with /bulk")
    return -1

# ======================== MAIN FUNCTION ========================

async def run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Main function called from main bot.
    This starts the conversation and handles all states.
    """
    user_id = update.effective_user.id
    
    # Check if this is a callback query
    if update.callback_query:
        return await handle_callback(update, context)
    
    # Check if this is a cancel command
    if update.message and update.message.text and update.message.text.lower() == '/cancel':
        return await handle_cancel(update, context)
    
    # Get current state for user
    current_state = user_data.get(user_id, {}).get('state', -1)
    
    # Handle based on state
    if current_state == -1:
        # Start new conversation
        return await start_conversation(update, context)
    
    elif current_state == WAITING_FOR_FILE:
        # Waiting for file upload
        if update.message and update.message.document:
            return await handle_file(update, context)
        else:
            await update.message.reply_text("Please send a `.txt` file.")
            return WAITING_FOR_FILE
    
    elif current_state == WAITING_FOR_MESSAGE:
        # Waiting for message
        if update.message and update.message.text:
            return await handle_message(update, context)
        else:
            await update.message.reply_text("Please send the message you want to send.")
            return WAITING_FOR_MESSAGE
    
    elif current_state == WAITING_FOR_RANGE:
        # Waiting for range
        if update.message and update.message.text:
            return await handle_range(update, context)
        else:
            await update.message.reply_text("Please specify the range.")
            return WAITING_FOR_RANGE
    
    elif current_state == WAITING_FOR_CONFIRMATION:
        # Waiting for confirmation - should be handled by callback
        await update.message.reply_text("Please use the buttons above to confirm or change range.")
        return WAITING_FOR_CONFIRMATION
    
    else:
        # Unknown state, reset
        if user_id in user_data:
            del user_data[user_id]
        await update.message.reply_text("Something went wrong. Please start over with /bulk")
        return -1