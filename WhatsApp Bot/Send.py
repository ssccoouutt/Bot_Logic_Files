#!/usr/bin/env python3
"""
WhatsApp Send Command Handler - Complete /send command logic
- Finds chats by name or phone number
- Handles scrolling and search
- Sends messages with Unicode support
- 5 minute timeout for direct message chats
- FIXED: Properly clears any pre-filled messages
"""

import time
import logging
import re
from datetime import datetime
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from selenium.webdriver.common.action_chains import ActionChains

# Configure logging
logger = logging.getLogger(__name__)

# ======================== UTILITY FUNCTIONS ========================

def is_phone_number(text):
    """Check if text is a phone number format"""
    return re.fullmatch(r'^\+\d{10,15}$', text) is not None

def capture_screenshot(driver):
    """Capture screenshot for debugging"""
    try:
        return driver.get_screenshot_as_png()
    except:
        return None

def escape_markdown_v1(text):
    """Escape for Markdown V1 (simpler escaping)"""
    if not text:
        return ""
    return text.replace('*', '\\*').replace('_', '\\_')

def clear_input_box_completely(driver, input_box):
    """Completely clear the input box using multiple methods"""
    try:
        # Method 1: Click and select all + delete
        input_box.click()
        time.sleep(0.2)
        input_box.send_keys(Keys.CONTROL + "a")
        time.sleep(0.2)
        input_box.send_keys(Keys.BACKSPACE)
        time.sleep(0.2)
        
        # Method 2: Clear via JavaScript
        driver.execute_script("arguments[0].innerHTML = '';", input_box)
        driver.execute_script("arguments[0].innerText = '';", input_box)
        time.sleep(0.2)
        
        # Method 3: Set value to empty
        driver.execute_script("arguments[0].textContent = '';", input_box)
        
        # Verify it's empty
        remaining = driver.execute_script("return arguments[0].innerText;", input_box)
        if remaining and remaining.strip():
            logger.warning(f"Input box still has content after clearing: '{remaining}'")
            # One more attempt with brute force
            for _ in range(5):
                input_box.send_keys(Keys.BACKSPACE)
                time.sleep(0.1)
        else:
            logger.debug("Input box cleared successfully")
        
        return True
    except Exception as e:
        logger.error(f"Error clearing input box: {e}")
        return False

def send_message_via_paste(driver, message):
    """Send message using clipboard paste method (handles Unicode best)"""
    try:
        # Create a unique ID for the textarea
        temp_id = f"temp_textarea_{int(time.time())}"
        
        # JavaScript to create a temporary textarea, paste content, and insert into WhatsApp
        script = f"""
        // Create a temporary textarea
        var textarea = document.createElement('textarea');
        textarea.id = '{temp_id}';
        textarea.style.position = 'fixed';
        textarea.style.top = '0';
        textarea.style.left = '0';
        textarea.style.width = '2em';
        textarea.style.height = '2em';
        textarea.style.padding = '0';
        textarea.style.border = 'none';
        textarea.style.outline = 'none';
        textarea.style.boxShadow = 'none';
        textarea.style.background = 'transparent';
        document.body.appendChild(textarea);
        
        // Set the value and select it
        textarea.value = arguments[0];
        textarea.select();
        textarea.setSelectionRange(0, textarea.value.length);
        
        // Execute paste command
        var success = document.execCommand('copy');
        
        // Clean up
        document.body.removeChild(textarea);
        
        return success;
        """
        
        # Copy to clipboard using JavaScript
        driver.execute_script(script, message)
        
        # Find WhatsApp input box
        input_box = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, '//div[@contenteditable="true"][@data-tab="10"]'))
        )
        
        # COMPLETELY CLEAR the input box first
        clear_input_box_completely(driver, input_box)
        
        # Focus and paste
        input_box.click()
        time.sleep(0.2)
        
        # Use ActionChains for paste
        actions = ActionChains(driver)
        actions.key_down(Keys.CONTROL).send_keys('v').key_up(Keys.CONTROL).perform()
        time.sleep(0.5)
        
        # Verify what was pasted
        inserted_text = driver.execute_script("return arguments[0].innerText", input_box)
        
        return inserted_text
        
    except Exception as e:
        logger.error(f"Paste method failed: {e}")
        return None

def send_message_via_js_dom(driver, message):
    """Send message using direct DOM manipulation with Unicode support"""
    try:
        input_box = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, '//div[@contenteditable="true"][@data-tab="10"]'))
        )
        
        # COMPLETELY CLEAR the input box first
        clear_input_box_completely(driver, input_box)
        
        # Split message by lines
        lines = message.split('\n')
        
        # Build HTML content with proper line breaks
        for i, line in enumerate(lines):
            if line:  # Only add non-empty lines
                # Use JSON.stringify to handle Unicode properly
                driver.execute_script("arguments[0].appendChild(document.createTextNode(arguments[1]));", input_box, line)
            
            if i < len(lines) - 1:  # Add line break between lines
                driver.execute_script("arguments[0].appendChild(document.createElement('br'));", input_box)
        
        # Trigger input event
        driver.execute_script("""
            var event = new InputEvent('input', { bubbles: true, cancelable: true });
            arguments[0].dispatchEvent(event);
        """, input_box)
        
        time.sleep(0.5)
        
        # Verify
        inserted_text = driver.execute_script("return arguments[0].innerText", input_box)
        
        return inserted_text
        
    except Exception as e:
        logger.error(f"DOM method failed: {e}")
        return None

def find_and_open_chat_by_name(driver, chat_name):
    """Find and open a chat by name using search and scroll"""
    try:
        logger.info(f"Searching for chat: '{chat_name}'")
        
        # First attempt: Try to find in current view
        chat_selector = f'//span[@title="{chat_name}"]'
        try:
            chat_element = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.XPATH, chat_selector))
            )
            chat_element.click()
            logger.info(f"Found and clicked chat in current view: {chat_name}")
            return True
        except:
            logger.debug(f"Chat '{chat_name}' not in view, trying search...")
        
        # Second attempt: Use the search box
        search_box_xpaths = [
            '//div[@contenteditable="true"][@data-tab="3"]',
            '//div[@contenteditable="true"][@data-tab="6"]',
            '//div[contains(@class, "lexical-rich-text-input")]//div[@contenteditable="true"]'
        ]
        
        search_box = None
        for xpath in search_box_xpaths:
            try:
                search_box = WebDriverWait(driver, 3).until(
                    EC.presence_of_element_located((By.XPATH, xpath))
                )
                if search_box: break
            except: continue
        
        if not search_box:
            # Fallback to scrolling if search box not found
            logger.warning("Search box not found, attempting to scroll chat list...")
            pane_side = driver.find_element(By.ID, "pane-side")
            found = False
            for _ in range(10):  # Scroll 10 times
                driver.execute_script("arguments[0].scrollTop += 500;", pane_side)
                time.sleep(0.5)
                try:
                    chat_element = driver.find_element(By.XPATH, chat_selector)
                    chat_element.click()
                    found = True
                    logger.info(f"Found and clicked chat after scrolling: {chat_name}")
                    break
                except: continue
            
            if not found:
                return False
        else:
            # Clear and type in search box
            search_box.click()
            time.sleep(0.5)
            search_box.send_keys(Keys.CONTROL + "a")
            search_box.send_keys(Keys.BACKSPACE)
            time.sleep(0.5)
            search_box.send_keys(chat_name)
            time.sleep(2)  # Wait for search results
            
            # Click the first matching result in the search list
            result_selectors = [
                f'//span[@title="{chat_name}"]',
                f'//span[contains(@title, "{chat_name}")]',
                f'//div[@role="listitem"]//span[contains(text(), "{chat_name}")]',
                f'//div[contains(@class, "lh6s0_j")]//span[contains(text(), "{chat_name}")]'
            ]
            
            found_result = False
            for selector in result_selectors:
                try:
                    chat_element = WebDriverWait(driver, 5).until(
                        EC.element_to_be_clickable((By.XPATH, selector))
                    )
                    driver.execute_script("arguments[0].scrollIntoView(true);", chat_element)
                    time.sleep(0.5)
                    chat_element.click()
                    found_result = True
                    logger.info(f"Found and clicked chat via search using selector '{selector}': {chat_name}")
                    break
                except: continue
            
            if not found_result:
                return False
        
        time.sleep(1.5)  # Wait for chat to open
        return True
        
    except Exception as e:
        logger.error(f"Error finding chat '{chat_name}': {e}")
        return False

def open_direct_message_chat(driver, phone_number):
    """Open a direct message chat using phone number with 5 minute timeout"""
    try:
        logger.info(f"Opening direct message chat for: {phone_number}")
        
        # Remove '+' for the URL
        phone_number_for_url = phone_number.replace("+", "")
        direct_message_url = f"https://web.whatsapp.com/send?phone={phone_number_for_url}"
        driver.get(direct_message_url)
        logger.debug(f"Navigated to direct message link for {phone_number}")
        
        # Wait for the "Starting Chat" overlay to disappear (30 seconds)
        try:
            WebDriverWait(driver, 30).until(
                EC.invisibility_of_element_located((By.XPATH, "//div[contains(text(), 'Starting chat')]"))
            )
            logger.debug("'Starting chat' overlay disappeared.")
        except TimeoutException:
            logger.debug("'Starting chat' overlay did not appear or disappear within 30 seconds.")
        
        # 5 MINUTE TIMEOUT for the chat to fully load
        logger.info(f"⏱️ Waiting up to 5 minutes for chat with {phone_number} to load...")
        
        try:
            # Wait for the message input box to be present and clickable
            WebDriverWait(driver, 300).until(
                EC.element_to_be_clickable((By.XPATH, '//div[@contenteditable="true"][@data-tab="10"]'))
            )
            logger.info(f"✅ Direct message chat opened successfully for {phone_number}")
            
            # IMPORTANT: After opening, ensure the input box is completely empty
            # Sometimes WhatsApp pre-fills with previous message
            time.sleep(1)  # Wait a moment for any auto-fill
            input_box = driver.find_element(By.XPATH, '//div[@contenteditable="true"][@data-tab="10"]')
            clear_input_box_completely(driver, input_box)
            
            return True
            
        except TimeoutException:
            # Check for common error messages
            error_message = ""
            try:
                invalid_number_element = driver.find_element(By.XPATH, 
                    "//*[contains(text(), 'Phone number shared via url is invalid') or "
                    "contains(text(), 'phone number is not valid') or "
                    "contains(text(), 'Invalid phone number')]"
                )
                error_message = invalid_number_element.text
            except NoSuchElementException:
                pass
            
            if error_message:
                raise Exception(f"❌ Invalid phone number. Error: {error_message}")
            else:
                raise Exception(f"⏱️ Timed out waiting for direct message chat to load after 5 minutes.")
                
    except Exception as e:
        logger.error(f"Error opening direct message chat: {e}")
        return False

# ======================== MAIN FUNCTION ========================

async def run(update, context, driver):
    """Main function called from main bot"""
    
    try:
        user_id = update.effective_user.id
        logger.info(f"User {user_id} initiated send command")
        
        # Get the full message text
        full_text = update.message.text
        logger.debug(f"Full command text: {repr(full_text)}")
        
        # Format: /send Tech Zone - Hello World
        if " - " not in full_text:
            logger.warning("Invalid format: missing ' - ' separator")
            await update.message.reply_text(
                "❌ Invalid format. Use: `/send (chatname) - (message)`\n"
                "Example: `/send Tech Zone - Hello World`\n"
                "For phone numbers: `/send +1234567890 - Hello`",
                parse_mode='Markdown'
            )
            return

        # Split based on the first occurrence of " - "
        parts = full_text.split("/send ", 1)[1].split(" - ", 1)
        chat_name = parts[0].strip()
        message_content = parts[1].strip()
        
        # Debug info
        logger.info(f"Target chat: '{chat_name}'")
        logger.info(f"Message content: {repr(message_content)}")
        
        # Count newlines
        newline_count = message_content.count('\n')
        lines = message_content.split('\n')
        
        # Check for Unicode characters outside BMP
        unicode_chars = []
        for char in message_content:
            if ord(char) > 0xFFFF:
                unicode_chars.append(f"{char} (U+{ord(char):X})")
        
        # Send initial response
        await update.message.reply_text(
            f"📤 Sending message to {chat_name}...\n"
            f"Lines: {len(lines)}, Newlines: {newline_count}\n"
            f"Special chars: {len(unicode_chars)}"
        )
        
        # OPEN THE CHAT - Two methods based on input type
        chat_opened = False
        
        # Check if chat_name is a phone number
        if is_phone_number(chat_name):
            logger.info(f"Target identified as phone number. Using direct link method with 5 minute timeout.")
            chat_opened = open_direct_message_chat(driver, chat_name)
        else:
            # Regular chat name - use search and scroll
            chat_opened = find_and_open_chat_by_name(driver, chat_name)
        
        if not chat_opened:
            # Take screenshot for debugging
            debug_screenshot = capture_screenshot(driver)
            if debug_screenshot:
                await update.message.reply_photo(
                    photo=debug_screenshot,
                    caption=f"❌ Failed to find chat: {chat_name}"
                )
            else:
                await update.message.reply_text(f"❌ Could not find chat: {chat_name}")
            return
        
        # Wait a moment for chat to fully load
        time.sleep(1)
        
        # Try multiple methods to send message
        inserted_text = None
        methods_tried = []
        
        # Method 1: Paste method (BEST for Unicode)
        logger.debug("Attempting Method 1: Clipboard paste")
        inserted_text = send_message_via_paste(driver, message_content)
        methods_tried.append("paste")
        
        if inserted_text and inserted_text.strip() == message_content.strip():
            logger.info("✅ Method 1 (paste) succeeded")
        else:
            logger.debug(f"Method 1 result: {repr(inserted_text)}")
            
            # Method 2: DOM manipulation
            logger.debug("Attempting Method 2: DOM manipulation")
            inserted_text = send_message_via_js_dom(driver, message_content)
            methods_tried.append("dom")
            
            if inserted_text and inserted_text.strip() == message_content.strip():
                logger.info("✅ Method 2 (DOM) succeeded")
            else:
                logger.debug(f"Method 2 result: {repr(inserted_text)}")
        
        # Send the message
        if inserted_text and inserted_text.strip():
            # Find input box and send
            input_box = driver.find_element(By.XPATH, '//div[@contenteditable="true"][@data-tab="10"]')
            
            # Double-check input box has our message
            final_check = driver.execute_script("return arguments[0].innerText;", input_box)
            logger.debug(f"Final check before sending: '{final_check}'")
            
            input_box.send_keys(Keys.ENTER)
            logger.debug("Pressed ENTER to send message")
            
            # Wait for message to send
            time.sleep(2)
            
            # Verify message was sent
            try:
                outgoing_messages = driver.find_elements(By.XPATH, '//div[contains(@class, "message-out")]//span[contains(@class, "selectable-text")]')
                if outgoing_messages:
                    last_message = outgoing_messages[-1].text
                    if last_message == message_content:
                        logger.info("✅ Message verification: Content matches exactly")
                    else:
                        logger.warning(f"⚠️ Message mismatch. Sent: '{message_content[:30]}...', Got: '{last_message[:30]}...'")
            except Exception as e:
                logger.debug(f"Could not verify sent message: {e}")
        else:
            logger.warning("No text was inserted, trying direct send_keys as fallback")
            
            # Fallback: direct send_keys
            input_box = driver.find_element(By.XPATH, '//div[@contenteditable="true"][@data-tab="10"]')
            clear_input_box_completely(driver, input_box)
            
            for line in lines:
                input_box.send_keys(line)
                if line != lines[-1]:
                    ActionChains(driver).key_down(Keys.SHIFT).send_keys(Keys.ENTER).key_up(Keys.SHIFT).perform()
                    time.sleep(0.1)
            
            input_box.send_keys(Keys.ENTER)
            methods_tried.append("send_keys_fallback")
        
        # Send confirmation
        safe_preview = message_content[:50].replace('*', '\\*').replace('_', '\\_')
        methods_str = ", ".join(methods_tried)
        
        confirmation_msg = (
            f"✅ Message sent to {chat_name}!\n\n"
            f"📊 Debug Info:\n"
            f"• Lines: {len(lines)}\n"
            f"• Newlines: {newline_count}\n"
            f"• Total chars: {len(message_content)}\n"
            f"• Unicode chars > BMP: {len(unicode_chars)}\n"
            f"• Methods tried: {methods_str}\n"
            f"• Preview: {safe_preview}{'...' if len(message_content) > 50 else ''}"
        )
        
        await update.message.reply_text(confirmation_msg)
        logger.info(f"✅ Message successfully sent to {chat_name}")
        
    except Exception as e:
        logger.error(f"Error in send_message: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Error: {str(e)}")