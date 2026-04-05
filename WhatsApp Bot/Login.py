#!/usr/bin/env python3
"""
WhatsApp Login Handler - Complete /login command logic
- Handles phone number pairing login
- Extracts and displays pairing code
- Takes screenshot for debugging
- Returns code to user
"""

import os
import time
import logging
import re
import traceback
from datetime import datetime
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from telegram import InputFile

# Configure logging
logger = logging.getLogger(__name__)

# ======================== LOGIN FUNCTIONS ========================

def login_with_phone(driver, phone_number):
    """Login to WhatsApp Web using phone number pairing instead of QR code"""
    try:
        logger.info(f"Starting phone login for: {phone_number}")

        # Navigate to WhatsApp Web
        driver.get("https://web.whatsapp.com/")

        # Wait for page to load
        logger.debug("Waiting for page to load...")
        wait = WebDriverWait(driver, 30)
        time.sleep(15)  # Give extra time for initial load

        # Save initial page for debugging
        driver.save_screenshot("initial_page.png")

        # Find and click "Link with phone number" or "Log in with phone number"
        logger.debug("Looking for phone login option...")

        # Various selectors to try
        selectors = [
            "//div[contains(text(), 'Log in with phone number')]",
            "//span[contains(text(), 'Log in with phone number')]",
            "//div[contains(text(), 'Link with phone number')]",
            "//span[contains(text(), 'Link with phone number')]",
            "//div[contains(text(), 'phone number')]",
            "//div[@role='button'][contains(., 'phone number')]",
            "//div[@aria-label='Link with phone number']",
            "//*[@data-testid='link-device-phone-number']"
        ]

        link_element = None
        for selector in selectors:
            try:
                elements = driver.find_elements(By.XPATH, selector)
                for element in elements:
                    if element.is_displayed():
                        logger.debug(f"Found element with text: {element.text}")
                        link_element = element
                        break
                if link_element:
                    break
            except Exception as e:
                continue

        if link_element:
            # Click using JavaScript for reliability
            driver.execute_script("arguments[0].click();", link_element)
            logger.debug("Clicked phone login option")
            time.sleep(3)
        else:
            logger.debug("Could not find phone login option")
            driver.save_screenshot("no_login_option.png")
            return False, None, None

        # Enter phone number - Clear default +1 first
        logger.debug(f"Entering phone number: {phone_number}")

        # Wait for input field
        phone_input = wait.until(EC.presence_of_element_located((By.XPATH, "//input[@type='text']")))

        # Clear the default country code (usually +1)
        logger.debug("Clearing default country code...")
        phone_input.click()

        # Method 1: Select all and delete
        phone_input.send_keys(Keys.CONTROL + "a")
        phone_input.send_keys(Keys.BACKSPACE)
        time.sleep(1)

        # Method 2: Triple click to select all text
        phone_input.click()
        time.sleep(0.5)
        phone_input.click()
        time.sleep(0.5)
        phone_input.click()
        phone_input.send_keys(Keys.BACKSPACE)
        time.sleep(1)

        # Get the current value to verify it's cleared
        current_value = driver.execute_script("return arguments[0].value;", phone_input)
        logger.debug(f"Field value after clearing: '{current_value}'")

        # Now enter the phone number
        phone_input.send_keys(phone_number)
        logger.debug(f"Entered: {phone_number}")

        # Verify what was entered
        final_value = driver.execute_script("return arguments[0].value;", phone_input)
        logger.debug(f"Final field value: '{final_value}'")

        if not final_value or final_value == '':
            logger.debug("Phone number entry failed! Trying alternative method...")
            # Alternative method: JavaScript to set value directly
            driver.execute_script(f"arguments[0].value = '{phone_number}';", phone_input)
            # Trigger input event
            driver.execute_script("arguments[0].dispatchEvent(new Event('input', { bubbles: true }));", phone_input)
            final_value = driver.execute_script("return arguments[0].value;", phone_input)
            logger.debug(f"After JavaScript set: '{final_value}'")

        # Wait for auto-formatting
        time.sleep(3)

        # Click Next
        logger.debug("Clicking Next...")

        # Try to find Next button
        next_selectors = [
            "//div[@role='button'][contains(., 'Next')]",
            "//div[contains(text(), 'Next')]",
            "//span[contains(text(), 'Next')]"
        ]

        next_button = None
        for selector in next_selectors:
            try:
                next_button = wait.until(EC.element_to_be_clickable((By.XPATH, selector)))
                break
            except:
                continue

        if next_button:
            driver.execute_script("arguments[0].click();", next_button)
            logger.debug("Clicked Next")
        else:
            # Try pressing Enter
            phone_input.send_keys(Keys.RETURN)
            logger.debug("Pressed Enter")

        # Wait for pairing code
        logger.debug("Waiting for pairing code to generate (this may take up to 30 seconds)...")

        # Wait for the code to appear (can take 15-20 seconds)
        for i in range(30):
            time.sleep(1)
            if i % 5 == 0:
                logger.debug(f"{i+1} seconds...")

            # Check if code appears
            try:
                code_elements = driver.find_elements(By.XPATH, "//span[@data-testid='pairing-code']//span")
                if code_elements and any(el.text.strip() for el in code_elements):
                    logger.debug("Pairing code detected!")
                    break
            except:
                pass

        # Save screenshot
        screenshot_path = "pairing_code.png"
        driver.save_screenshot(screenshot_path)

        # Try to extract code
        try:
            # Look for code in various possible locations
            code_selectors = [
                "//span[@data-testid='pairing-code']//span",
                "//div[@data-testid='qr-code-container']//span",
                "//div[contains(@class, 'pairing')]//span",
                "//div[contains(@class, '_akbu')]//span",
                "//div[@role='button'][contains(., 'Copy')]/../..//span",
                "//div[contains(text(), 'code')]/following-sibling::div//span",
                "//span[contains(@dir, 'auto')][string-length(text()) >= 8]"
            ]

            pairing_code = None
            for selector in code_selectors:
                code_elements = driver.find_elements(By.XPATH, selector)
                if code_elements:
                    # Filter to get only numeric codes
                    for element in code_elements:
                        text = element.text.strip()
                        if text and len(text) >= 8 and any(c.isdigit() for c in text):
                            # Clean the text to get just digits
                            digits = ''.join(c for c in text if c.isdigit())
                            if len(digits) >= 8:
                                pairing_code = text
                                logger.debug(f"Found potential code: {text}")
                                break
                if pairing_code:
                    break

            # Also try to get from page title or any large text
            if not pairing_code:
                body_text = driver.find_element(By.TAG_NAME, "body").text
                # Look for 8-12 digit sequences
                code_pattern = r'\b(\d{4}[- ]?\d{4}[- ]?\d{0,4})\b'
                matches = re.findall(code_pattern, body_text)
                if matches:
                    pairing_code = matches[0]
                    logger.debug(f"Found code in body text: {pairing_code}")

            if pairing_code:
                # Format the code nicely
                clean_code = ''.join(c for c in pairing_code if c.isdigit())
                if len(clean_code) >= 8:
                    formatted_code = f"{clean_code[:4]}-{clean_code[4:8]}-{clean_code[8:12]}" if len(clean_code) >= 12 else pairing_code
                else:
                    formatted_code = pairing_code

                logger.info(f"✅ Login successful! Pairing code: {formatted_code}")
                return True, formatted_code, screenshot_path
            else:
                logger.debug("Could not automatically extract the code. Check the screenshot.")
                return True, None, screenshot_path

        except Exception as e:
            logger.error(f"Could not extract code: {e}")
            return True, None, screenshot_path

    except Exception as e:
        logger.error(f"Phone login failed: {e}")
        traceback.print_exc()
        return False, None, None

# ======================== MAIN FUNCTION ========================

async def run(update, context, driver):
    """Main function called from main bot"""
    
    try:
        user_id = update.effective_user.id
        logger.info(f"User {user_id} initiated login command")
        
        # Check if phone number is provided
        if not context.args:
            await update.message.reply_text(
                "❌ Please provide your phone number with country code\n\n"
                "Usage: `/login +923190779215`\n\n"
                "Example: `/login +923001234567`",
                parse_mode='Markdown'
            )
            return

        phone_number = context.args[0].strip()

        # Basic phone number validation
        if not phone_number.startswith('+'):
            await update.message.reply_text(
                "⚠️ Phone number should start with `+` and country code (e.g., `+923001234567`)",
                parse_mode='Markdown'
            )
            return

        await update.message.reply_text(
            f"🔐 Starting WhatsApp login with phone number: `{phone_number}`\n\n"
            f"This will generate a pairing code. Please wait...",
            parse_mode='Markdown'
        )

        # Run login
        success, code, screenshot_path = login_with_phone(driver, phone_number)

        if success:
            if code:
                await update.message.reply_text(
                    f"✅ Login initiated successfully!\n\n"
                    f"🔑 Your pairing code: `{code}`\n\n"
                    f"📱 *Instructions:*\n"
                    f"1. Open WhatsApp on your phone\n"
                    f"2. Go to Settings → Linked Devices → Link a Device\n"
                    f"3. Select 'Link with phone number instead'\n"
                    f"4. Enter this 12-digit code\n\n"
                    f"After linking, use `/get` to check your WhatsApp Web status",
                    parse_mode='Markdown'
                )
            else:
                # Send the screenshot
                if screenshot_path and os.path.exists(screenshot_path):
                    with open(screenshot_path, 'rb') as photo:
                        await update.message.reply_photo(
                            photo=InputFile(photo),
                            caption="📸 Please check this screenshot for the pairing code"
                        )

                await update.message.reply_text(
                    "📱 *Instructions:*\n"
                    "1. Look for a 12-digit code in the screenshot above\n"
                    "2. Open WhatsApp on your phone\n"
                    "3. Go to Settings → Linked Devices → Link a Device\n"
                    "4. Select 'Link with phone number instead'\n"
                    "5. Enter the 12-digit code\n\n"
                    "After linking, use `/get` to check your WhatsApp Web status",
                    parse_mode='Markdown'
                )
        else:
            await update.message.reply_text(
                "❌ Failed to initiate phone login.\n\n"
                "Please try again or use the traditional QR code method."
            )
        
        # Clean up screenshot
        if screenshot_path and os.path.exists(screenshot_path):
            try:
                os.remove(screenshot_path)
            except:
                pass

    except Exception as e:
        logger.error(f"Error in login command: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Error: {str(e)}")