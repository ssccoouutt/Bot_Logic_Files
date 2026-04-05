#!/usr/bin/env python3
"""
WhatsApp Scrap Command Handler - Complete /scrap command logic
- Extracts phone numbers from WhatsApp group info
- Supports all Unicode characters including emojis
- Returns results as TXT file with one number per line
"""

import time
import logging
import re
import os
import tempfile
from datetime import datetime
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

# Configure logging
logger = logging.getLogger(__name__)

# ======================== UTILITY FUNCTIONS ========================

def extract_phone_numbers(text):
    """
    Extract phone numbers from text with robust pattern matching.
    Handles various formats: +1234567890, +1 234 567 890, +12-345-678-90, etc.
    """
    if not text:
        return []
    
    # Pattern for international phone numbers with optional separators
    # Matches: + followed by 1-3 digits (country code), then digits with optional spaces, dashes, dots
    phone_pattern = r'\+\d{1,3}[\d\s\-\.\(\)]{7,20}'
    
    # Also look for numbers that might be written without spaces but with country code
    simple_pattern = r'\+\d{10,15}'
    
    found_numbers = []
    
    # Try complex pattern first
    complex_matches = re.findall(phone_pattern, text)
    for match in complex_matches:
        # Clean the number: remove spaces, dashes, dots, parentheses
        cleaned = re.sub(r'[\s\-\.\(\)]', '', match)
        # Ensure it's a valid phone number (at least 10 digits after +)
        if re.match(r'\+\d{10,15}$', cleaned):
            found_numbers.append(cleaned)
    
    # Try simple pattern
    simple_matches = re.findall(simple_pattern, text)
    for match in simple_matches:
        if match not in found_numbers:
            found_numbers.append(match)
    
    return found_numbers

def extract_numbers_from_element(driver, element):
    """
    Extract phone numbers from a single element's text and attributes.
    Handles Unicode characters properly.
    """
    numbers = set()
    
    try:
        # Get text content
        text = element.text
        if text and '+' in text:
            found = extract_phone_numbers(text)
            numbers.update(found)
        
        # Get title attribute (often contains contact info)
        title = element.get_attribute('title')
        if title and '+' in title:
            found = extract_phone_numbers(title)
            numbers.update(found)
        
        # Get aria-label (sometimes contains phone numbers)
        aria = element.get_attribute('aria-label')
        if aria and '+' in aria:
            found = extract_phone_numbers(aria)
            numbers.update(found)
        
        # Get data-* attributes that might contain numbers
        data_attrs = driver.execute_script("""
            var attrs = [];
            var element = arguments[0];
            for (var i = 0, attrs = element.attributes, len = attrs.length; i < len; i++) {
                if (attrs[i].name.startsWith('data-')) {
                    attrs.push(attrs[i].name + ':' + attrs[i].value);
                }
            }
            return attrs;
        """, element)
        
        for attr in data_attrs:
            if '+' in attr:
                found = extract_phone_numbers(attr)
                numbers.update(found)
                
    except Exception as e:
        logger.debug(f"Error extracting from element: {e}")
    
    return numbers

def scroll_group_members_panel(driver):
    """
    Scroll through the group members panel to load all members.
    Returns True if scrolling was successful.
    """
    try:
        # Find the members panel (scrollable area)
        members_panel_selectors = [
            '//div[@role="list"]',  # Common for contact lists
            '//div[contains(@class, "members-list")]',
            '//div[contains(@class, "participants")]',
            '//div[@data-testid="group-members-list"]',
            '//div[contains(@style, "overflow-y")]'  # Any scrollable div
        ]
        
        members_panel = None
        for selector in members_panel_selectors:
            try:
                members_panel = driver.find_element(By.XPATH, selector)
                break
            except:
                continue
        
        if not members_panel:
            logger.warning("Could not find members panel, attempting to scroll entire page")
            members_panel = driver.find_element(By.TAG_NAME, 'body')
        
        # Scroll to load all members
        last_height = 0
        scroll_attempts = 0
        max_scrolls = 30
        scroll_pause = 1.5
        
        for scroll in range(max_scrolls):
            # Scroll down
            driver.execute_script("arguments[0].scrollTop += 500;", members_panel)
            time.sleep(scroll_pause)
            
            # Check if we've reached the bottom
            new_height = driver.execute_script("return arguments[0].scrollHeight;", members_panel)
            scroll_top = driver.execute_script("return arguments[0].scrollTop;", members_panel)
            client_height = driver.execute_script("return arguments[0].clientHeight;", members_panel)
            
            logger.debug(f"Scroll {scroll+1}: position {scroll_top}/{new_height} (client: {client_height})")
            
            if new_height - scroll_top <= client_height + 100:
                logger.info("Reached bottom of members panel")
                break
            
            if scroll_top == last_height:
                scroll_attempts += 1
                if scroll_attempts >= 3:
                    logger.info("Scrolling stalled")
                    break
            else:
                last_height = scroll_top
                scroll_attempts = 0
        
        return True
        
    except Exception as e:
        logger.error(f"Error scrolling members panel: {e}")
        return False

def find_and_open_group(driver, group_name):
    """
    Find and open a group by name using search.
    Returns True if successful.
    """
    try:
        logger.info(f"🔍 Searching for group: '{group_name}'")
        
        # Find search box
        search_box_selectors = [
            '//div[@contenteditable="true"][@data-tab="3"]',
            '//div[@contenteditable="true"][@data-tab="6"]',
            '//div[contains(@class, "lexical-rich-text-input")]//div[@contenteditable="true"]'
        ]
        
        search_box = None
        for selector in search_box_selectors:
            try:
                search_box = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH, selector))
                )
                break
            except:
                continue
        
        if not search_box:
            logger.error("Could not find search box")
            return False
        
        # Clear and search
        search_box.click()
        time.sleep(0.5)
        search_box.send_keys(Keys.CONTROL + "a")
        search_box.send_keys(Keys.BACKSPACE)
        time.sleep(0.5)
        search_box.send_keys(group_name)
        time.sleep(3)  # Wait for search results
        
        # Try to find the group in results
        group_selectors = [
            f'//span[@title="{group_name}"]',
            f'//span[contains(@title, "{group_name}")]',
            f'//div[@role="listitem"]//span[contains(text(), "{group_name}")]',
            f'//div[contains(@class, "chat-title")]//span[contains(text(), "{group_name}")]'
        ]
        
        for selector in group_selectors:
            try:
                group_element = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH, selector))
                )
                driver.execute_script("arguments[0].scrollIntoView(true);", group_element)
                time.sleep(0.5)
                group_element.click()
                logger.info(f"✅ Clicked on group: {group_name}")
                time.sleep(3)  # Wait for group to open
                return True
            except:
                continue
        
        logger.error(f"Group '{group_name}' not found in search results")
        return False
        
    except Exception as e:
        logger.error(f"Error finding group: {e}")
        return False

def open_group_info_panel(driver):
    """
    Open the group info side panel by clicking the header.
    Returns True if successful.
    """
    try:
        logger.info("📁 Opening group info panel...")
        
        # Click on header to open group info
        header_selectors = [
            '//header',
            '//div[@data-testid="chat-header"]',
            '//div[contains(@class, "header")]',
            '//div[@role="button"][contains(@class, "header")]'
        ]
        
        header = None
        for selector in header_selectors:
            try:
                header = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH, selector))
                )
                break
            except:
                continue
        
        if not header:
            logger.error("Could not find header")
            return False
        
        header.click()
        logger.info("✅ Clicked header, waiting for info panel...")
        time.sleep(5)  # Wait for info panel to open
        
        return True
        
    except Exception as e:
        logger.error(f"Error opening group info: {e}")
        return False

# ======================== MAIN FUNCTION ========================

async def run(update, context, driver):
    """Main function called from main bot"""
    
    try:
        user_id = update.effective_user.id
        logger.info(f"User {user_id} initiated scrap command")
        
        # Get group name from command
        if not context.args:
            await update.message.reply_text(
                "❌ Please provide a group name. Usage: `/scrap [group name]`",
                parse_mode='Markdown'
            )
            return
        
        group_name = " ".join(context.args)
        logger.info(f"🎯 Target group: '{group_name}'")
        
        status_msg = await update.message.reply_text(f"🔍 Searching for group: {group_name}...")
        
        # Step 1: Find and open the group
        if not find_and_open_group(driver, group_name):
            await status_msg.edit_text(f"❌ Group '{group_name}' not found.")
            return
        
        # Step 2: Open group info panel
        await status_msg.edit_text(f"📁 Opening group info for '{group_name}'...")
        if not open_group_info_panel(driver):
            await status_msg.edit_text(f"❌ Could not open group info for '{group_name}'.")
            return
        
        # Step 3: Scroll to load all members
        await status_msg.edit_text(f"📜 Loading all members from '{group_name}'...")
        scroll_group_members_panel(driver)
        
        # Step 4: Extract phone numbers
        await status_msg.edit_text(f"🔢 Extracting phone numbers from '{group_name}'...")
        logger.info("🔍 Searching for elements containing phone numbers...")
        
        # Find all elements that might contain phone numbers
        potential_elements = driver.find_elements(By.XPATH, '//*[contains(text(), "+") or contains(@title, "+") or contains(@aria-label, "+")]')
        logger.info(f"Found {len(potential_elements)} potential elements")
        
        numbers = set()
        
        for i, elem in enumerate(potential_elements):
            if i % 50 == 0:  # Progress update every 50 elements
                logger.debug(f"Processed {i}/{len(potential_elements)} elements, found {len(numbers)} numbers so far")
            
            try:
                extracted = extract_numbers_from_element(driver, elem)
                numbers.update(extracted)
            except Exception as e:
                logger.debug(f"Error processing element {i}: {e}")
                continue
        
        logger.info(f"📊 Total unique numbers found: {len(numbers)}")
        
        # Step 5: Send results as TXT file
        if numbers:
            # Sort numbers for consistent output
            cleaned_numbers = sorted(list(numbers))
            
            # Create TXT file with one number per line
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_group_name = re.sub(r'[^\w\-_]', '_', group_name)
            filename = f"scraped_{safe_group_name}_{timestamp}.txt"
            
            with open(filename, "w", encoding='utf-8') as f:
                for number in cleaned_numbers:
                    f.write(f"{number}\n")  # One number per line
            
            file_size = os.path.getsize(filename)
            line_count = len(cleaned_numbers)
            logger.info(f"✅ Saved {line_count} numbers to {filename} ({file_size} bytes)")
            
            await status_msg.edit_text(
                f"✅ Successfully scraped *{line_count}* numbers from '{group_name}'.\n"
                f"📁 Sending TXT file...",
                parse_mode='Markdown'
            )
            
            # Send the file
            with open(filename, 'rb') as f:
                await update.message.reply_document(
                    document=f,
                    filename=filename,
                    caption=f"📞 Phone numbers from '{group_name}'\nTotal: {line_count}"
                )
            
            # Clean up
            os.remove(filename)
            logger.info(f"🧹 Cleaned up temporary file: {filename}")
            
        else:
            logger.warning(f"No phone numbers found for group '{group_name}'")
            await status_msg.edit_text(
                f"❌ No phone numbers found in '{group_name}'.\n"
                f"Make sure the group info panel is open and contains members."
            )
            
            # Take screenshot for debugging
            try:
                screenshot = driver.get_screenshot_as_png()
                await update.message.reply_photo(
                    photo=screenshot,
                    caption=f"Debug screenshot for '{group_name}'"
                )
            except:
                pass
        
    except Exception as e:
        logger.error(f"Error in scrap command: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Error: {str(e)}")