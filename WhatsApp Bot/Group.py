#!/usr/bin/env python3
"""
WhatsApp Group & Community Join Handler - FIXED VERSION
- Features:
  1. ALL timeouts unified to 5 minutes (300s) to handle extremely slow loading.
  2. "Already in Group" detection with specific user messaging (CHECKED FIRST).
  3. Support for "Join Group", "Join Community", and "Request to Join" (auto-clicks).
  4. Triple-redundant clicking (Native, ActionChains, and JavaScript).
  5. Enhanced group name extraction from headers and dialogs.
  6. FIXED: "Link Revoked" only shown when specific error elements appear.
  7. ADDED: Pending request detection ("Cancel request" button).
"""

import os
import time
import logging
import asyncio
import traceback
import re
from datetime import datetime
from telegram import Update
from telegram.ext import ContextTypes

# Selenium imports
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import TimeoutException, NoSuchElementException

logger = logging.getLogger(__name__)

def clean_group_name(text):
    """Clean group name by removing phone numbers and extra text"""
    if not text:
        return "Unknown Group"
    
    # If it looks like a list of phone numbers or is too long
    if re.search(r'\+?\d[\d\s\(\)\-]{7,}', text) or len(text) > 50:
        return "WhatsApp Group"
    
    return text

async def run(update: Update, context: ContextTypes.DEFAULT_TYPE, driver):
    """
    Main entry point for the /group command.
    Navigates to the link, detects status, and automatically joins if possible.
    """
    try:
        # 1. Validate Input
        if not context.args:
            await update.message.reply_text("❌ *Missing Link*\nUsage: `/group https://chat.whatsapp.com/XXXXX`", parse_mode='Markdown')
            return
        
        group_link = context.args[0]
        context.user_data['pending_group_link'] = group_link
        
        status_msg = await update.message.reply_text("🔍 *Accessing WhatsApp Web...*\nThis may take up to 5 minutes to load depending on your connection.", parse_mode='Markdown')
        
        # 2. Link Normalization
        if "chat.whatsapp.com/" in group_link:
            code = group_link.split("chat.whatsapp.com/")[1].split("?")[0]
        else:
            code = group_link
            
        direct_link = f"https://web.whatsapp.com/accept?code={code}"
        
        # 3. Status Detection and Auto-Join Logic (Blocking Selenium operations)
        loop = asyncio.get_event_loop()
        import sys
        main_module = sys.modules.get('__main__')
        executor = getattr(main_module, 'executor', None)
        
        def process_join_blocking():
            try:
                logger.info(f"Navigating to: {direct_link}")
                driver.get(direct_link)
                
                # --- UNIFIED 5 MINUTE MONITORING LOOP ---
                max_wait = 300  # 5 minutes
                start_time = time.time()
                status = "Unknown"
                group_name = "Unknown Group"
                action_taken = False
                
                logger.info("Starting 5-minute monitoring for WhatsApp interface and group dialog...")
                
                while time.time() - start_time < max_wait:
                    # A. First, check if WhatsApp Web base is even loaded
                    base_loaded = driver.find_elements(By.XPATH, '//div[@contenteditable="true"]')
                    if not base_loaded:
                        # If not loaded, just wait and continue the loop
                        time.sleep(5)
                        continue

                    # B. CHECK FOR "ALREADY IN GROUP" FIRST - THIS IS THE MOST IMPORTANT CHECK
                    # Check if we're directly in the chat (group opened)
                    chat_indicators = [
                        "//footer//div[@contenteditable='true']",  # Chat input present
                        "//div[@role='textbox'][@contenteditable='true']",  # Message input
                        "//div[contains(@class, '_ak1q')]//div[@contenteditable='true']",  # WhatsApp input field
                        "//div[@role='main']//footer"  # Main area with footer (chat area)
                    ]
                    
                    for xpath in chat_indicators:
                        if driver.find_elements(By.XPATH, xpath):
                            # Check if there's a group header
                            header_elements = driver.find_elements(By.XPATH, "//header//span[@title]")
                            if header_elements:
                                group_name = header_elements[0].get_attribute("title")
                                # Also check for participant count to confirm it's a group
                                participants = driver.find_elements(By.XPATH, "//div[contains(@title, 'participants')]")
                                if participants or group_name:
                                    status = "Already in group"
                                    logger.info(f"Detected: Already in group (chat open) - {group_name}")
                                    break
                    
                    # Check for explicit "already a member" messages
                    already_in_indicators = [
                        "//div[contains(text(), 'You are already a member')]",
                        "//div[contains(text(), 'Already a member')]",
                        "//div[contains(text(), 'already a participant')]",
                        "//span[contains(text(), 'already in this group')]"
                    ]
                    
                    for xpath in already_in_indicators:
                        elements = driver.find_elements(By.XPATH, xpath)
                        if elements:
                            for el in elements:
                                if el.is_displayed():
                                    status = "Already in group"
                                    logger.info("Detected: Already in group (explicit message)")
                                    
                                    # Try to extract group name from nearby elements
                                    try:
                                        # Look for group name in parent dialog
                                        parent_dialog = el.find_element(By.XPATH, "ancestor::div[@role='dialog']")
                                        if parent_dialog:
                                            spans = parent_dialog.find_elements(By.XPATH, ".//span")
                                            for span in spans:
                                                txt = span.text.strip()
                                                if txt and len(txt) > 1 and txt not in ["You are already a member", "Already a member", "OK", "Cancel"]:
                                                    group_name = txt
                                                    break
                                    except:
                                        pass
                                    break
                    
                    if status == "Already in group":
                        # Clean the group name
                        group_name = clean_group_name(group_name)
                        break
                    
                    # C. Check for Error States
                    error_indicators = [
                        "//div[contains(text(), 'link is invalid')]",
                        "//div[contains(text(), 'reset')]",
                        "//div[contains(text(), 'Link is invalid')]",
                        "//div[contains(text(), 'Reset')]",
                        "//div[contains(text(), 'Unable to join this group')]"
                    ]
                    for xpath in error_indicators:
                        if driver.find_elements(By.XPATH, xpath):
                            status = "Link revoked"
                            break
                    if status == "Link revoked": break

                    # D. Check for and auto-click "Join" Buttons
                    join_targets = ["Join group", "Join community", "Request to join"]
                    join_button_found = False
                    
                    for target in join_targets:
                        join_buttons = driver.find_elements(By.XPATH, f"//*[contains(text(), '{target}')]")
                        for button in join_buttons:
                            if button.is_displayed():
                                join_button_found = True
                                logger.info(f"Found '{target}' button - WILL CLICK IT")
                                
                                # Extract group name from dialog before clicking
                                try:
                                    dialogs = driver.find_elements(By.XPATH, "//div[@role='dialog']")
                                    if dialogs:
                                        potentials = dialogs[0].find_elements(By.XPATH, ".//span | .//strong")
                                        for p in potentials:
                                            txt = p.text.strip()
                                            if txt and len(txt) > 1 and txt not in join_targets + ["Cancel", "WhatsApp", "Already a member"]:
                                                group_name = clean_group_name(txt)
                                                break
                                except: pass
                                
                                # Triple-redundant click
                                try:
                                    button.click()
                                    logger.info(f"Clicked '{target}' button using native click")
                                except:
                                    try:
                                        ActionChains(driver).move_to_element(button).click().perform()
                                        logger.info(f"Clicked '{target}' button using ActionChains")
                                    except:
                                        driver.execute_script("arguments[0].click();", button)
                                        logger.info(f"Clicked '{target}' button using JavaScript")
                                
                                status = f"Clicked: {target}"
                                action_taken = True
                                time.sleep(12)  # Wait for join to complete
                                break
                        if action_taken: break
                    
                    if action_taken:
                        # After clicking, check if we're now in the group (chat input appears)
                        time.sleep(3)
                        if driver.find_elements(By.XPATH, "//footer//div[@contenteditable='true']"):
                            status = "Successfully joined"
                        break

                    # E. ONLY if NO join button was found, check for "Request already sent"
                    if not join_button_found and not action_taken:
                        pending_indicators = [
                            "//*[contains(text(), 'Cancel request')]",
                            "//*[contains(text(), 'Request sent. Waiting for admin approval')]"
                        ]
                        for xpath in pending_indicators:
                            pending_elements = driver.find_elements(By.XPATH, xpath)
                            if pending_elements:
                                for el in pending_elements:
                                    if el.is_displayed():
                                        logger.info("Found 'Cancel request' button - Request already pending")
                                        status = "Request already sent"
                                        
                                        # Try to extract group name
                                        try:
                                            dialogs = driver.find_elements(By.XPATH, "//div[@role='dialog']")
                                            if dialogs:
                                                potentials = dialogs[0].find_elements(By.XPATH, ".//span | .//strong")
                                                for p in potentials:
                                                    txt = p.text.strip()
                                                    if txt and len(txt) > 1 and txt not in ["Cancel request", "Request sent. Waiting for admin approval.", "Join group", "Join community", "Request to join"]:
                                                        group_name = clean_group_name(txt)
                                                        break
                                        except: pass
                                        break
                            if status == "Request already sent": break
                    
                    if status == "Request already sent": break
                    
                    # F. Check for "Group is full"
                    full_indicators = [
                        "//div[contains(text(), 'group is full')]",
                        "//div[contains(text(), 'Group is full')]",
                        "//div[contains(text(), 'This group is full')]"
                    ]
                    for xpath in full_indicators:
                        if driver.find_elements(By.XPATH, xpath):
                            status = "Group is full"
                            break
                    
                    if status == "Group is full": break
                        
                    time.sleep(5)
                
                # Final check if WhatsApp base never loaded even after 5 mins
                if not driver.find_elements(By.XPATH, '//div[@contenteditable="true"]') and status == "Unknown":
                    return False, "WhatsApp Web failed to load completely within 5 minutes.", "Unknown Group"

                # --- EXTRACT GROUP NAME (if still unknown) ---
                if group_name == "Unknown Group" or group_name == "WhatsApp Group":
                    try:
                        # Try header first
                        header_elements = driver.find_elements(By.XPATH, "//header//span[@title]")
                        if header_elements:
                            group_name = header_elements[0].get_attribute("title")
                        
                        # Try dialog title
                        if group_name == "Unknown Group":
                            dialogs = driver.find_elements(By.XPATH, "//div[@role='dialog']")
                            if dialogs:
                                potentials = dialogs[0].find_elements(By.XPATH, ".//span | .//strong")
                                for p in potentials:
                                    txt = p.text.strip()
                                    if txt and len(txt) > 1 and txt not in ["Join group", "Join community", "Request to join", "Cancel", "WhatsApp", "Already a member", "Cancel request", "Unable to join this group", "Request sent. Waiting for admin approval."]:
                                        group_name = txt
                                        break
                        
                        # Clean the name
                        group_name = clean_group_name(group_name)
                    except: pass
                
                return True, status, group_name
                
            except Exception as e:
                logger.error(f"Blocking process error: {traceback.format_exc()}")
                return False, str(e), "Unknown"

        # Execute the blocking Selenium work
        if executor:
            success, status, group_name = await loop.run_in_executor(executor, process_join_blocking)
        else:
            success, status, group_name = process_join_blocking()
            
        if not success:
            await status_msg.edit_text(f"❌ *WhatsApp Error*\n{status}", parse_mode='Markdown')
            return

        # Clean group name one more time for display
        group_name = clean_group_name(group_name)

        # 4. Send appropriate message based on status
        if status == "Link revoked":
            await status_msg.edit_text(
                f"❌ *Link Revoked*\nThe invite link is invalid or has been reset.",
                parse_mode='Markdown'
            )
        elif status == "Group is full":
            await status_msg.edit_text(
                f"⚠️ *Group Full*\nThe group you're trying to join is currently full.",
                parse_mode='Markdown'
            )
        elif status == "Already in group":
            await status_msg.edit_text(
                f"✅ *Already a Member*\nYou are already a member of *{group_name}*.",
                parse_mode='Markdown'
            )
        elif status == "Request already sent":
            await status_msg.edit_text(
                f"ℹ️ *Request Pending*\nA join request for *{group_name}* has already been sent and is awaiting approval.",
                parse_mode='Markdown'
            )
        elif status == "Successfully joined":
            await status_msg.edit_text(
                f"✅ *Success!*\nYou have successfully joined *{group_name}*.",
                parse_mode='Markdown'
            )
        elif "Clicked:" in status:
            join_type = status.replace("Clicked: ", "")
            await status_msg.edit_text(
                f"✅ *Join Initiated*\n{join_type} request sent for *{group_name}*.",
                parse_mode='Markdown'
            )
        elif status == "Unknown":
            await status_msg.edit_text(
                f"❓ *Status Unknown*\nCould not determine the status after 5 minutes.",
                parse_mode='Markdown'
            )
        else:
            await status_msg.edit_text(
                f"ℹ️ *Status*\n{status}",
                parse_mode='Markdown'
            )
            
    except Exception as e:
        logger.error(f"Handler Run Error: {traceback.format_exc()}")
        await update.message.reply_text(f"❌ *System Error*\n`{str(e)}`", parse_mode='Markdown')